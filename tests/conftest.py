from pathlib import Path

import pytest


class SysTree:
    """Builds fake sysfs/procfs/dev trees under a tmp dir."""

    def __init__(self, root: Path):
        self.root = root
        self.sys = root / "sys"
        self.proc = root / "proc"
        self.dev = root / "dev"
        for d in (self.sys, self.proc, self.dev):
            d.mkdir(parents=True, exist_ok=True)
        self._zones = 0
        self._hwmons = 0

    def write(self, path: Path, text: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def zone(self, type_: str, millideg: int | str) -> Path:
        z = self.sys / "class" / "thermal" / f"thermal_zone{self._zones}"
        self._zones += 1
        self.write(z / "type", type_ + "\n")
        self.write(z / "temp", f"{millideg}\n")
        return z

    def hwmon(self, name: str, temps: dict[str, int]) -> Path:
        h = self.sys / "class" / "hwmon" / f"hwmon{self._hwmons}"
        self._hwmons += 1
        self.write(h / "name", name + "\n")
        for i, (label, value) in enumerate(temps.items(), 1):
            self.write(h / f"temp{i}_input", f"{value}\n")
            if label:
                self.write(h / f"temp{i}_label", label + "\n")
        return h

    def i2c(self, n: int, name: str, node: bool = True) -> None:
        self.write(self.sys / "bus" / "i2c" / "devices" / f"i2c-{n}" / "name", name + "\n")
        if node:
            (self.dev / f"i2c-{n}").touch()

    def route(self, *rows: tuple[str, str, int, int]) -> None:
        header = "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT\n"
        lines = [
            f"{i}\t{d}\t0101A8C0\t{f:04X}\t0\t0\t{m}\t{'00000000' if d == '00000000' else '00FFFFFF'}\t0\t0\t0"
            for i, d, f, m in rows
        ]
        self.write(self.proc / "net" / "route", header + "\n".join(lines) + "\n")


@pytest.fixture
def tree(tmp_path) -> SysTree:
    return SysTree(tmp_path)


def add_fan(tree: SysTree, name: str, rpms: dict[int, int]) -> Path:
    h = tree.sys / "class" / "hwmon" / f"hwmon{tree._hwmons}"
    tree._hwmons += 1
    tree.write(h / "name", name + "\n")
    for n, rpm in rpms.items():
        tree.write(h / f"fan{n}_input", f"{rpm}\n")
    return h
