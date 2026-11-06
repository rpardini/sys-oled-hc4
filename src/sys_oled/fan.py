"""hwmon fan tachometers (fan*_input, rpm). Detected once; per cycle only the selected files are read."""

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Fan:
    name: str  # <hwmon name>:fanN
    path: Path
    rpm: int | None = None


def read_rpm(path: Path) -> int:
    return int(path.read_text().strip())


def scan(sysfs: Path = Path("/sys")) -> list[Fan]:
    fans = []
    for hw in sorted((sysfs / "class" / "hwmon").glob("hwmon*")):
        try:
            hw_name = (hw / "name").read_text().strip()
        except OSError:
            continue
        for inp in sorted(hw.glob("fan*_input")):
            try:
                rpm = read_rpm(inp)
            except (OSError, ValueError):
                rpm = None
            fans.append(Fan(f"{hw_name}:{inp.name.removesuffix('_input')}", inp, rpm))
    return fans


def select(fans: list[Fan], wanted: str = "auto") -> list[Fan]:
    usable = [f for f in fans if f.rpm is not None]
    if wanted == "none":
        return []
    if wanted.startswith("re:"):
        rx = re.compile(wanted[3:])
        return [f for f in usable if rx.search(f.name)]
    return usable


def read_all(fans: list[Fan]) -> list[int]:
    """Raises OSError/ValueError if a selected fan cannot be read (triggers re-detection)."""
    return [read_rpm(f.path) for f in fans]
