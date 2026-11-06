from sys_oled import fan

from .conftest import add_fan


def test_scan_and_select(tree):
    add_fan(tree, "pwmfan", {1: 1200})
    add_fan(tree, "nct6775", {1: 800, 2: 0})
    tree.hwmon("coretemp", {"Package id 0": 50000})
    fans = fan.scan(tree.sys)
    assert [(f.name, f.rpm) for f in fans] == [("pwmfan:fan1", 1200), ("nct6775:fan1", 800), ("nct6775:fan2", 0)]
    assert len(fan.select(fans)) == 3  # a stopped fan (0 rpm) is still a fan
    assert fan.select(fans, "none") == []
    assert [f.name for f in fan.select(fans, "re:^pwm")] == ["pwmfan:fan1"]
    assert fan.read_all(fan.select(fans)) == [1200, 800, 0]


def test_no_fans(tree):
    assert fan.scan(tree.sys) == []
