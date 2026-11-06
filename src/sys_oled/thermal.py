"""Pick the most relevant temperature sensor across SoC families (meson, rockchip, genio, allwinner, x86...)."""

import re
from dataclasses import dataclass
from pathlib import Path

# Ordered tiers; the first tier with any usable candidate wins and shows the max of its members.
TIERS: tuple[tuple[str, re.Pattern], ...] = (
    (
        "soc/package",
        re.compile(
            r"^(soc_thermal|package_thermal|x86_pkg_temp|soc_max"
            r"|coretemp:package_id_\d+|k10temp:(tctl|tdie)|k10temp)$"
        ),
    ),
    (
        "cpu",
        re.compile(
            r"^(cpu_thermal|cpu\d+_thermal|cpu_(big|little)\d*_thermal|(big|little)core\d*_thermal"
            r"|cpuss\d*.*|cpu_(b|l)\d*_thermal)$"
        ),
    ),
    ("soc/generic", re.compile(r"^(center_thermal|ap_thermal|soc\d*_thermal|tsens\d*.*)$")),
    ("other", re.compile(r"^(?!acpitz).*$")),
    ("acpi", re.compile(r"^acpitz.*$")),
)
EXCLUDE = re.compile(
    r"gpu|npu|vpu|ddr|dram|venc|vdec|vcodec|img|cam|battery|charger|pmic|bms|nvme|drivetemp|iwlwifi|wifi|bluetooth"
)
VALID_RANGE = (1.0, 150.0)


@dataclass
class Sensor:
    name: str  # normalized
    path: Path  # millidegree file
    tier: int
    temp: float | None = None


def normalize(name: str) -> str:
    return re.sub(r"[\s-]+", "_", name.strip().lower())


def read_millideg(path: Path) -> float:
    return int(path.read_text().strip()) / 1000.0


def _valid(t: float | None) -> bool:
    return t is not None and VALID_RANGE[0] <= t <= VALID_RANGE[1]


def tier_of(name: str) -> int | None:
    if EXCLUDE.search(name):
        return None
    for i, (_, pattern) in enumerate(TIERS):
        if pattern.match(name):
            return i
    return None


def scan(sysfs: Path = Path("/sys")) -> list[Sensor]:
    """All candidate sensors with a current reading (None if unreadable)."""
    found: dict[str, Sensor] = {}

    def add(name: str, path: Path) -> None:
        name = normalize(name)
        if name in found:
            return
        try:
            temp = read_millideg(path)
        except (OSError, ValueError):
            temp = None
        tier = tier_of(name)
        found[name] = Sensor(name, path, tier if tier is not None else -1, temp)

    for zone in sorted((sysfs / "class" / "thermal").glob("thermal_zone*")):
        try:
            add((zone / "type").read_text(), zone / "temp")
        except OSError:
            continue
    for hw in sorted((sysfs / "class" / "hwmon").glob("hwmon*")):
        try:
            hw_name = (hw / "name").read_text().strip()
        except OSError:
            continue
        if normalize(hw_name) in found:  # thermal zones also show up as hwmon devices
            continue
        for inp in sorted(hw.glob("temp*_input")):
            label_file = inp.with_name(inp.name.replace("_input", "_label"))
            try:
                label = label_file.read_text().strip()
            except OSError:
                label = ""
            add(f"{hw_name}:{label}" if label else hw_name, inp)
    return list(found.values())


def select(sensors: list[Sensor], wanted: str = "auto") -> tuple[list[Sensor], str]:
    """Returns (selected sensors, description). The value shown is the max of the selection."""
    usable = [s for s in sensors if _valid(s.temp)]
    if wanted != "auto":
        if wanted.startswith("re:"):
            rx = re.compile(wanted[3:])
            picked = [s for s in usable if rx.search(s.name)]
        else:
            picked = [s for s in usable if s.name == normalize(wanted)]
        return picked, f"configured '{wanted}'"
    for i, (label, _) in enumerate(TIERS):
        picked = [s for s in usable if s.tier == i]
        if picked:
            return picked, f"tier {label}"
    return [], "none usable"


def read_max(sensors: list[Sensor]) -> float:
    """Raises OSError/ValueError if any selected sensor cannot be read (triggers re-selection)."""
    temps = [read_millideg(s.path) for s in sensors]
    if not temps or not all(_valid(t) for t in temps):
        raise ValueError("invalid temperature reading")
    return max(temps)
