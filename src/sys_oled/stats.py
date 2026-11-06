"""Per-cycle stats with detect-once semantics: re-detection only on failure or kernel change events."""

import contextlib
import errno
import fcntl
import logging
import os
import socket
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from . import detect, fan, storage, thermal
from .config import Config
from .watch import MountWatcher, RouteWatcher

log = logging.getLogger(__name__)
SIOCGIFADDR = 0x8915


@dataclass
class Snapshot:
    load: float
    temp: float | None
    uptime: float
    mem_used: int
    mem_total: int
    mem_percent: float
    disks: list[tuple[str, storage.Usage]]
    iface: str | None
    ip: str | None
    hostname: str
    fan_rpm: int | None = None  # fastest fan, for display
    fan_min: int | None = None  # slowest fan, for alerts
    loadavg: tuple[float, float, float] = (0.0, 0.0, 0.0)


def iface_ipv4(iface: str) -> str:
    """One ioctl on the cached interface. Raises OSError (ENODEV, EADDRNOTAVAIL) when it is gone/unaddressed."""
    if not sys.platform.startswith("linux"):
        for addr in psutil.net_if_addrs().get(iface, []):
            if addr.family == socket.AF_INET:
                return addr.address
        raise OSError(errno.EADDRNOTAVAIL, "no IPv4 address")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        res = fcntl.ioctl(s.fileno(), SIOCGIFADDR, struct.pack("256s", iface[:15].encode()))
    return socket.inet_ntoa(res[20:24])


@dataclass
class Collector:
    cfg: Config
    sysfs: Path = Path("/sys")
    procfs: Path = Path("/proc")
    detections: dict[str, int] = field(default_factory=lambda: {"thermal": 0, "iface": 0, "storage": 0, "fan": 0})

    def __post_init__(self):
        self.boot_time = psutil.boot_time()
        psutil.cpu_percent(interval=None)  # prime: later calls measure since the previous one
        root = self.cfg.host.root
        self.mountinfo_path = str(self.procfs / ("1" if root not in ("", "/") else "self") / "mountinfo")
        self.mount_watch = MountWatcher(self.mountinfo_path)
        self.route_watch = RouteWatcher()
        self.hostname = self._hostname()
        self.detect_thermal()
        self.detect_fans()
        self.detect_iface()
        self.detect_storage()

    def _hostname(self) -> str:
        root = self.cfg.host.root
        if root not in ("", "/"):
            try:
                return Path(storage.host_path(root, "/etc/hostname")).read_text().strip()
            except OSError:
                pass
        return socket.gethostname().split(".")[0]

    def apply_config(self, cfg: Config) -> None:
        old, self.cfg = self.cfg, cfg
        if old.sensors.thermal != cfg.sensors.thermal:
            self.detect_thermal()
        if old.sensors.fan != cfg.sensors.fan:
            self.detect_fans()
        if old.sensors.network != cfg.sensors.network:
            self.detect_iface()
        if old.section_values("storage") != cfg.section_values("storage"):
            self.detect_storage()

    # --- detection (startup, failures, change events only) ---

    def detect_thermal(self) -> None:
        self.detections["thermal"] += 1
        self.sensors, why = thermal.select(thermal.scan(self.sysfs), self.cfg.sensors.thermal)
        log.info("thermal: %s (%s)", ", ".join(s.name for s in self.sensors) or "none", why)

    def detect_fans(self) -> None:
        self.detections["fan"] += 1
        self.fans = fan.select(fan.scan(self.sysfs), self.cfg.sensors.fan)
        log.info("fans: %s", ", ".join(f.name for f in self.fans) or "none")

    def detect_iface(self) -> None:
        self.detections["iface"] += 1
        wanted = self.cfg.sensors.network
        if wanted != "auto":
            if detect.iface_exists(wanted, self.sysfs):
                self.iface = wanted
                log.info("network: %s (configured)", wanted)
                return
            log.warning("network: configured interface '%s' does not exist, using default route", wanted)
        self.iface = detect.default_route_iface(self.procfs)
        log.info("network: %s (default route)", self.iface)

    def detect_storage(self) -> None:
        self.detections["storage"] += 1
        try:
            text = self.mount_watch.read()
        except OSError:
            text = ""
        st = self.cfg.storage
        self.disks = storage.rank(text, self.cfg.host.root, st.mounts, st.auto, self.sysfs)
        log.info("storage: %s", ", ".join(f"{d.label}={d.mountpoint}" for d in self.disks) or "none")

    # --- per cycle ---

    def read_temp(self) -> float | None:
        if not self.sensors:
            return None
        try:
            return thermal.read_max(self.sensors)
        except (OSError, ValueError) as e:
            log.info("thermal read failed (%s), re-selecting", e)
            self.detect_thermal()
            return None

    def read_fans(self) -> list[int]:
        if not self.fans:
            return []
        try:
            return fan.read_all(self.fans)
        except (OSError, ValueError) as e:
            log.info("fan read failed (%s), re-detecting", e)
            self.detect_fans()
            return []

    def snapshot(self, disk_slots: int) -> Snapshot:
        if self.mount_watch.changed():
            log.info("mount table changed")
            self.detect_storage()
        if self.route_watch.changed():
            self.detect_iface()

        temp = self.read_temp()
        rpms = self.read_fans()

        ip = None
        if self.iface:
            try:
                ip = iface_ipv4(self.iface)
            except OSError:
                self.detect_iface()
                if self.iface:
                    with contextlib.suppress(OSError):
                        ip = iface_ipv4(self.iface)

        disks = []
        for d in self.disks[:disk_slots]:
            try:
                disks.append((d.label, storage.current_usage(d)))
            except (OSError, storage.StaleMount) as e:
                log.info("storage %s stale (%s), re-ranking", d.mountpoint, e)
                self.detect_storage()
                break

        mem = psutil.virtual_memory()
        return Snapshot(
            load=psutil.cpu_percent(interval=None),
            temp=temp,
            uptime=time.time() - self.boot_time,
            mem_used=mem.total - mem.available,
            mem_total=mem.total,
            mem_percent=mem.percent,
            disks=disks,
            iface=self.iface,
            ip=ip,
            hostname=self.hostname,
            fan_rpm=max(rpms) if rpms else None,
            fan_min=min(rpms) if rpms else None,
            loadavg=os.getloadavg(),
        )


def sample_snapshot() -> Snapshot:
    """Deterministic data for render --sample (captures, tests)."""
    gib = 1 << 30
    return Snapshot(
        load=7.0,
        temp=48.0,
        uptime=3 * 86400 + 4 * 3600 + 5 * 60,
        mem_used=int(1.2 * gib),
        mem_total=int(3.7 * gib),
        mem_percent=32.0,
        disks=[
            ("emmc", storage.Usage(int(5.1 * gib), int(28.9 * gib), 18.0)),
            ("md0", storage.Usage(int(1.9 * 1024 * gib), int(7.2 * 1024 * gib), 27.0)),
        ],
        iface="end0",
        ip="192.168.1.42",
        hostname="odroidhc4",
        fan_rpm=1200,
        fan_min=1200,
        loadavg=(0.52, 0.40, 0.31),
    )
