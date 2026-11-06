import pytest

from sys_oled import thermal


def pick(tree, wanted="auto"):
    sensors, _ = thermal.select(thermal.scan(tree.sys), wanted)
    return sorted(s.name for s in sensors), (thermal.read_max(sensors) if sensors else None)


def test_meson_cpu_over_ddr(tree):
    tree.zone("cpu-thermal", 51000)
    tree.zone("ddr-thermal", 49000)
    assert pick(tree) == (["cpu_thermal"], 51.0)


def test_rk3399_cpu_over_gpu(tree):
    tree.zone("cpu-thermal", 45000)
    tree.zone("gpu-thermal", 60000)
    assert pick(tree) == (["cpu_thermal"], 45.0)


def test_rk3588_soc_wins(tree):
    tree.zone("soc-thermal", 40000)
    for name, t in (
        ("bigcore0-thermal", 44000),
        ("bigcore1-thermal", 46000),
        ("littlecore-thermal", 42000),
        ("center-thermal", 41000),
        ("gpu-thermal", 50000),
        ("npu-thermal", 52000),
    ):
        tree.zone(name, t)
    assert pick(tree) == (["soc_thermal"], 40.0)


def test_rk3588_without_soc_takes_max_of_cores(tree):
    tree.zone("bigcore0-thermal", 44000)
    tree.zone("bigcore1-thermal", 46000)
    tree.zone("littlecore-thermal", 42000)
    tree.zone("center-thermal", 41000)
    names, temp = pick(tree)
    assert names == ["bigcore0_thermal", "bigcore1_thermal", "littlecore_thermal"]
    assert temp == 46.0


@pytest.mark.parametrize(
    "zones",
    [
        [f"cpu{i}-thermal" for i in range(8)],  # genio mt8195
        ["cpu-little0-thermal", "cpu-little1-thermal", "cpu-big0-thermal", "cpu-big1-thermal"],  # genio mt8188/mt8390
    ],
)
def test_genio_max_of_per_cpu(tree, zones):
    for i, z in enumerate(zones):
        tree.zone(z, 40000 + i * 1000)
    tree.zone("vpu-thermal", 90000)
    tree.zone("soc1-thermal", 30000)
    names, temp = pick(tree)
    assert len(names) == len(zones)
    assert temp == 40.0 + len(zones) - 1


def test_x86_coretemp_package(tree):
    tree.zone("acpitz", 27800)
    tree.hwmon("coretemp", {"Package id 0": 55000, "Core 0": 53000})
    tree.hwmon("nvme", {"Composite": 70000})
    assert pick(tree) == (["coretemp:package_id_0"], 55.0)


def test_amd_k10temp(tree):
    tree.hwmon("k10temp", {"Tctl": 61000, "Tccd1": 58000})
    assert pick(tree) == (["k10temp:tctl"], 61.0)


def test_only_acpitz(tree):
    tree.zone("acpitz", 27800)
    assert pick(tree) == (["acpitz"], 27.8)


def test_unreadable_zone_skipped(tree):
    tree.zone("cpu-thermal", "garbage")
    tree.zone("center-thermal", 30000)
    assert pick(tree) == (["center_thermal"], 30.0)


def test_thermal_zone_hwmon_duplicate_ignored(tree):
    tree.zone("cpu-thermal", 50000)
    tree.hwmon("cpu_thermal", {"": 50000})
    assert [s.name for s in thermal.scan(tree.sys)] == ["cpu_thermal"]


def test_none_at_all(tree):
    assert pick(tree) == ([], None)


def test_configured_name_and_regex(tree):
    tree.zone("cpu-thermal", 50000)
    tree.zone("ddr-thermal", 40000)
    assert pick(tree, "ddr-thermal") == (["ddr_thermal"], 40.0)  # explicit config may pick excluded ones
    assert pick(tree, "re:^(cpu|ddr)") == (["cpu_thermal", "ddr_thermal"], 50.0)


def test_read_failure_raises(tree):
    z = tree.zone("cpu-thermal", 50000)
    sensors, _ = thermal.select(thermal.scan(tree.sys))
    (z / "temp").write_text("")
    with pytest.raises(ValueError):
        thermal.read_max(sensors)
