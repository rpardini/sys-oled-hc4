import pytest

from sys_oled import storage

GIB = 1 << 30


def mi(n, mm, root, mountpoint, fstype, source, opts="rw,relatime"):
    return f"{n} 1 {mm} {root} {mountpoint} {opts} shared:1 - {fstype} {source} rw"


@pytest.fixture
def fake_fs(monkeypatch):
    """path -> (st_dev, total bytes); paths not listed do not exist."""
    table: dict[str, tuple[int, int]] = {}
    monkeypatch.setattr(storage, "_st_dev", lambda p: table[p][0] if p in table else None)
    monkeypatch.setattr(storage, "usage", lambda p: storage.Usage(table[p][1] // 4, table[p][1], 25.0))
    return table


def blockdev(tree, mm, disk, part=None, extra=None):
    """sysfs /sys/dev/block/M:m -> devices/.../block/<disk>[/<part>]"""
    real = tree.sys / "devices" / (extra or "platform") / "block" / disk
    real.mkdir(parents=True, exist_ok=True)
    if part:
        real = real / part
        real.mkdir()
        (real / "partition").write_text("1\n")
    link = tree.sys / "dev" / "block" / mm
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(real)
    return real


def labels(result):
    return [(s.label, s.mountpoint) for s in result]


def test_sd_root_only(tree, fake_fs):
    d = blockdev(tree, "179:2", "mmcblk0", "mmcblk0p2")
    (d.parent / "device").mkdir()
    (d.parent / "device" / "type").write_text("SD\n")
    fake_fs["/"] = (1, 30 * GIB)
    text = "\n".join(
        [
            mi(1, "179:2", "/", "/", "ext4", "/dev/mmcblk0p2"),
            mi(2, "179:1", "/", "/boot", "ext4", "/dev/mmcblk0p1"),
            mi(3, "0:20", "/", "/run", "tmpfs", "tmpfs"),
        ]
    )
    assert labels(storage.rank(text, sysfs=tree.sys)) == [("sd", "/")]


def test_emmc_root_plus_nvme_data(tree, fake_fs):
    d = blockdev(tree, "179:2", "mmcblk1", "mmcblk1p2")
    (d.parent / "device").mkdir()
    (d.parent / "device" / "type").write_text("MMC\n")
    blockdev(tree, "259:1", "nvme0n1", "nvme0n1p1")
    fake_fs["/"] = (1, 30 * GIB)
    fake_fs["/data"] = (2, 1000 * GIB)
    text = "\n".join(
        [mi(1, "179:2", "/", "/", "ext4", "/dev/mmcblk1p2"), mi(2, "259:1", "/", "/data", "xfs", "/dev/nvme0n1p1")]
    )
    assert labels(storage.rank(text, sysfs=tree.sys)) == [("emmc", "/"), ("nvme", "/data")]


def test_md_raid_and_size_ranking(tree, fake_fs):
    blockdev(tree, "8:1", "sda", "sda1", extra="platform/usb1/1-1")
    blockdev(tree, "9:0", "md0")
    fake_fs.update({"/": (1, 30 * GIB), "/mnt/usb": (2, 64 * GIB), "/mnt/md0": (3, 8000 * GIB)})
    text = "\n".join(
        [
            mi(1, "179:2", "/", "/", "ext4", "/dev/mmcblk0p2"),
            mi(2, "8:1", "/", "/mnt/usb", "ext4", "/dev/sda1"),
            mi(3, "9:0", "/", "/mnt/md0", "ext4", "/dev/md0"),
        ]
    )
    assert labels(storage.rank(text, sysfs=tree.sys))[1:] == [("md0", "/mnt/md0"), ("usb", "/mnt/usb")]


def test_sata_label(tree, fake_fs):
    blockdev(tree, "8:16", "sdb", "sdb1", extra="platform/ata1/host0")
    fake_fs["/srv"] = (5, 100 * GIB)
    text = mi(1, "8:16", "/", "/srv", "ext4", "/dev/sdb1")
    assert labels(storage.rank(text, sysfs=tree.sys)) == [("sata", "/srv")]


def test_zfs_one_entry_per_pool(tree, fake_fs):
    fake_fs.update({"/tank": (10, 4000 * GIB), "/tank/a": (11, 4000 * GIB), "/tank/b/c": (12, 4000 * GIB)})
    text = "\n".join(
        [
            mi(1, "0:50", "/", "/tank/b/c", "zfs", "tank/b/c"),
            mi(2, "0:51", "/", "/tank", "zfs", "tank"),
            mi(3, "0:52", "/", "/tank/a", "zfs", "tank/a"),
        ]
    )
    assert labels(storage.rank(text, sysfs=tree.sys)) == [("tank", "/tank")]


def test_btrfs_subvolumes_and_bind_mounts_dedup(tree, fake_fs):
    fake_fs.update({"/": (1, 100 * GIB), "/home": (1, 100 * GIB), "/srv/bind": (1, 100 * GIB)})
    text = "\n".join(
        [
            mi(1, "0:30", "/@", "/", "btrfs", "/dev/nvme0n1p2"),
            mi(2, "0:30", "/@home", "/home", "btrfs", "/dev/nvme0n1p2"),
            mi(3, "0:30", "/@/srv", "/srv/bind", "btrfs", "/dev/nvme0n1p2"),
        ]
    )
    assert [s.mountpoint for s in storage.rank(text, sysfs=tree.sys)] == ["/"]


def test_armbian_zram_log2ram_and_k8s_noise_dropped(tree, fake_fs):
    fake_fs.update(
        {
            "/": (1, 30 * GIB),
            "/var/log": (2, GIB),
            "/var/log.hdd": (1, 30 * GIB),
            "/var/lib/kubelet/pods/x/volumes": (3, 30 * GIB),
            "/snap/core/1": (4, GIB),
        }
    )
    text = "\n".join(
        [
            mi(1, "179:2", "/", "/", "ext4", "/dev/mmcblk0p2"),
            mi(2, "254:1", "/", "/var/log", "ext4", "/dev/zram1"),
            mi(3, "179:2", "/var/log", "/var/log.hdd", "ext4", "/dev/mmcblk0p2"),
            mi(4, "8:1", "/", "/var/lib/kubelet/pods/x/volumes", "ext4", "/dev/sda1"),
            mi(5, "7:0", "/", "/snap/core/1", "squashfs", "/dev/loop0", "ro"),
        ]
    )
    assert [s.mountpoint for s in storage.rank(text, sysfs=tree.sys)] == ["/"]


def test_explicit_first_then_auto_dedup(tree, fake_fs):
    blockdev(tree, "9:0", "md0")
    fake_fs.update({"/": (1, 30 * GIB), "/mnt/md0": (3, 8000 * GIB)})
    text = "\n".join(
        [mi(1, "179:2", "/", "/", "ext4", "/dev/mmcblk0p2"), mi(3, "9:0", "/", "/mnt/md0", "ext4", "/dev/md0")]
    )
    result = storage.rank(text, explicit=[{"label": "sd", "path": "/"}], sysfs=tree.sys)
    assert labels(result) == [("sd", "/"), ("md0", "/mnt/md0")]
    assert labels(storage.rank(text, explicit=[{"label": "sd", "path": "/"}], auto=False)) == [("sd", "/")]


def test_container_host_root_prefix(tree, fake_fs):
    fake_fs.update({"/host": (1, 30 * GIB), "/host/mnt/md0": (3, 8000 * GIB)})
    text = "\n".join(
        [mi(1, "179:2", "/", "/", "ext4", "/dev/mmcblk0p2"), mi(3, "9:0", "/", "/mnt/md0", "ext4", "/dev/md0")]
    )
    result = storage.rank(text, root="/host", sysfs=tree.sys)
    assert [(s.mountpoint, s.statpath) for s in result] == [("/", "/host"), ("/mnt/md0", "/host/mnt/md0")]


def test_stale_mount_detected(fake_fs):
    fake_fs["/mnt/usb"] = (2, GIB)
    s = storage.Storage("usb", "/mnt/usb", "/mnt/usb", 2)
    assert storage.current_usage(s).total == GIB
    fake_fs["/mnt/usb"] = (1, GIB)  # unmounted: path now on the parent fs
    with pytest.raises(storage.StaleMount):
        storage.current_usage(s)


def test_mountinfo_unescape():
    (m,) = storage.parse_mountinfo(mi(1, "8:1", "/", "/mnt/my\\040disk", "ext4", "/dev/sda1"))
    assert m.mountpoint == "/mnt/my disk"
