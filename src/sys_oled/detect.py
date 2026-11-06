"""I2C display and default-route detection. Filesystem roots are injectable for tests."""

import errno
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

ADDRESSES = (0x3C, 0x3D)
# EREMOTEIO (121) is Linux-only; what the i2c core returns for a missing ACK on most adapters.
NACK_ERRNOS = {errno.ENXIO, getattr(errno, "EREMOTEIO", 121), errno.EIO, errno.ETIMEDOUT, errno.EAGAIN}
# Adapters that are display links (DP AUX, HDMI DDC), never an OLED bus.
SKIP_ADAPTER = re.compile(r"dp|aux|ddc|hdmi", re.I)


@dataclass
class ProbeResult:
    bus: int | None = None
    address: int | None = None
    log: list[str] = field(default_factory=list)
    hint: str | None = None


def list_adapters(sysfs: Path = Path("/sys")) -> list[tuple[int, str]]:
    base = sysfs / "bus" / "i2c" / "devices"
    out = []
    for entry in base.glob("i2c-*"):
        m = re.fullmatch(r"i2c-(\d+)", entry.name)
        if not m:
            continue
        try:
            name = (entry / "name").read_text().strip()
        except OSError:
            name = ""
        out.append((int(m.group(1)), name))
    return sorted(out)


def probe(bus: int, address: int, smbus_factory=None) -> str:
    """SMBus quick-write probe, same as i2cdetect does for 0x3c. Returns ack|nack|busy|error:<msg>."""
    if smbus_factory is None:
        from smbus2 import SMBus as smbus_factory
    try:
        with smbus_factory(bus) as sm:
            sm.write_quick(address)
        return "ack"
    except OSError as e:
        if e.errno == errno.EBUSY:
            return "busy"
        if e.errno in NACK_ERRNOS:
            return "nack"
        return f"error:{e.strerror or e}"


def find_display(
    bus: int | str = "auto",
    address: int | str = "auto",
    sysfs: Path = Path("/sys"),
    dev: Path = Path("/dev"),
    smbus_factory=None,
) -> ProbeResult:
    result = ProbeResult()
    adapters = list_adapters(sysfs)
    if bus != "auto":
        adapters = [a for a in adapters if a[0] == bus] or [(bus, "")]
    addresses = ADDRESSES if address == "auto" else (address,)
    if not any((dev / f"i2c-{n}").exists() for n, _ in adapters):
        result.hint = "no /dev/i2c-* device nodes: is the i2c-dev module loaded? (modprobe i2c-dev)"
        if not adapters:
            result.hint = "no I2C adapters found in sysfs"
        result.log.append(result.hint)
        return result
    for n, name in adapters:
        if bus == "auto" and SKIP_ADAPTER.search(name):
            result.log.append(f"i2c-{n} ({name}): skipped, display link adapter")
            continue
        if not (dev / f"i2c-{n}").exists():
            result.log.append(f"i2c-{n} ({name}): no device node")
            continue
        for addr in addresses:
            status = probe(n, addr, smbus_factory)
            result.log.append(f"i2c-{n} ({name}) 0x{addr:02x}: {status}")
            if status == "ack":
                result.bus, result.address = n, addr
                return result
            if status.startswith("error:") and "ermission" in status:
                result.hint = "permission denied opening /dev/i2c-*: run as root or grant device access"
    return result


def default_route_iface(procfs: Path = Path("/proc")) -> str | None:
    """Lowest-metric IPv4 default route from /proc/net/route."""
    try:
        lines = (procfs / "net" / "route").read_text().splitlines()[1:]
    except OSError:
        return None
    best = None
    for line in lines:
        cols = line.split()
        if len(cols) < 8 or cols[1] != "00000000" or cols[7] != "00000000":
            continue
        flags, metric = int(cols[3], 16), int(cols[6])
        if flags & 0x1 and (best is None or metric < best[1]):  # RTF_UP
            best = (cols[0], metric)
    return best[0] if best else None


def iface_exists(iface: str, sysfs: Path = Path("/sys")) -> bool:
    return (sysfs / "class" / "net" / iface).exists() or iface in _if_names()


def _if_names() -> set[str]:
    try:
        return {name for _, name in os.if_nameindex()}
    except OSError:
        return set()
