"""Find the most relevant storage: root first, then the biggest real data filesystems, deduped per device/pool."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

REAL_FS = {"ext4", "ext3", "ext2", "xfs", "btrfs", "f2fs", "bcachefs", "zfs"}
SKIP_SOURCES = re.compile(r"^/dev/(loop|zram|ram)\d*")
SKIP_MOUNTS = re.compile(
    r"^/(boot|efi)(/|$)|^/var/log\.hdd(/|$)|^/(snap|run|proc|sys|dev)(/|$)"
    r"|^/var/lib/(docker|containerd|kubelet|rancher|containers|snapd|lxd|lxc)(/|$)"
)


@dataclass
class MountInfo:
    major_minor: str
    root: str
    mountpoint: str
    options: set[str]
    fstype: str
    source: str


@dataclass
class Storage:
    label: str
    mountpoint: str  # as on the host
    statpath: str  # as seen by us (host.root prefixed)
    st_dev: int | None = None
    total: int = 0


@dataclass
class Usage:
    used: int
    total: int
    percent: float


def _unescape(s: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), s)


def parse_mountinfo(text: str) -> list[MountInfo]:
    out = []
    for line in text.splitlines():
        left, sep, right = line.partition(" - ")
        if not sep:
            continue
        lf, rf = left.split(), right.split()
        if len(lf) < 6 or len(rf) < 2:
            continue
        out.append(MountInfo(lf[2], _unescape(lf[3]), _unescape(lf[4]), set(lf[5].split(",")), rf[0], _unescape(rf[1])))
    return out


def host_path(root: str, path: str) -> str:
    return path if root in ("", "/") else os.path.normpath(os.path.join(root, path.lstrip("/")))


def usage(statpath: str) -> Usage:
    st = os.statvfs(statpath)
    total = st.f_blocks * st.f_frsize
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    avail = st.f_bavail * st.f_frsize
    pct = used / (used + avail) * 100 if used + avail else 0.0
    return Usage(used, total, pct)


def _block_dir(mi: MountInfo, sysfs: Path) -> Path | None:
    for candidate in (sysfs / "dev" / "block" / mi.major_minor, sysfs / "class" / "block" / Path(mi.source).name):
        try:
            if candidate.exists():
                return candidate.resolve()
        except OSError:
            pass
    return None


def label_for(mi: MountInfo, sysfs: Path = Path("/sys")) -> str:
    if mi.fstype == "zfs":
        return mi.source.split("/")[0][:5]
    blk = _block_dir(mi, sysfs)
    if blk is None:
        return (Path(mi.source).name or mi.fstype)[:5]
    disk = blk.parent if (blk / "partition").exists() else blk
    name = disk.name
    if name.startswith("mmcblk"):
        try:
            kind = (disk / "device" / "type").read_text().strip()
        except OSError:
            kind = ""
        return "emmc" if kind == "MMC" else "sd"
    if name.startswith("nvme"):
        return "nvme"
    if name.startswith("md"):
        return name[:5]
    if name.startswith("dm-"):
        try:
            return (disk / "dm" / "name").read_text().strip()[:5]
        except OSError:
            return "dm"
    if re.match(r"sd[a-z]", name):
        return "usb" if "/usb" in str(disk) else "sata"
    return name[:5]


def _dedup_key(mi: MountInfo) -> str:
    return "zfs:" + mi.source.split("/")[0] if mi.fstype == "zfs" else mi.major_minor


def candidates(mounts: list[MountInfo]) -> list[MountInfo]:
    """Real filesystems, one per device (or zfs pool), preferring the fs root and the shortest mountpoint."""
    best: dict[str, MountInfo] = {}
    for mi in mounts:
        if mi.fstype not in REAL_FS or SKIP_SOURCES.match(mi.source):
            continue
        if mi.mountpoint != "/" and (SKIP_MOUNTS.match(mi.mountpoint) or "ro" in mi.options):
            continue
        key = _dedup_key(mi)
        cur = best.get(key)
        if (
            mi.mountpoint == "/"
            or cur is None
            or (
                cur.mountpoint != "/"
                and (
                    (mi.root == "/") > (cur.root == "/")
                    or ((mi.root == "/") == (cur.root == "/") and len(mi.mountpoint) < len(cur.mountpoint))
                )
            )
        ):
            best[key] = mi
    return list(best.values())


def _st_dev(path: str) -> int | None:
    try:
        return os.stat(path).st_dev
    except OSError:
        return None


def rank(
    mountinfo_text: str,
    root: str = "/",
    explicit: list[dict[str, str]] = (),
    auto: bool = True,
    sysfs: Path = Path("/sys"),
) -> list[Storage]:
    """Explicit mounts first, then root, then the rest by size (descending), deduped by device."""
    out: list[Storage] = []
    seen: set[int] = set()
    for m in explicit:
        sp = host_path(root, m["path"])
        dev = _st_dev(sp)
        out.append(Storage(m["label"][:5], m["path"], sp, dev))
        if dev is not None:
            seen.add(dev)
    if not auto:
        return out
    ranked = []
    for mi in candidates(parse_mountinfo(mountinfo_text)):
        sp = host_path(root, mi.mountpoint)
        dev = _st_dev(sp)
        if dev is None or dev in seen:
            continue
        try:
            total = usage(sp).total
        except OSError:
            continue
        seen.add(dev)
        ranked.append(Storage(label_for(mi, sysfs), mi.mountpoint, sp, dev, total))
    ranked.sort(key=lambda s: (s.mountpoint != "/", -s.total))
    return out + ranked


class StaleMount(Exception):
    pass


def current_usage(s: Storage) -> Usage:
    """statfs plus a cheap st_dev check: if the mount went away the path resolves to the parent fs."""
    if s.st_dev is not None and _st_dev(s.statpath) != s.st_dev:
        raise StaleMount(s.mountpoint)
    return usage(s.statpath)
