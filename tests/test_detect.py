import errno
from typing import ClassVar

from sys_oled import detect


class FakeSMBus:
    """Devices: {(bus, addr): 'ack' | errno}."""

    devices: ClassVar[dict] = {}

    def __init__(self, bus):
        self.bus = bus

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read_byte(self, addr):
        r = self.devices.get((self.bus, addr), errno.ENXIO)
        if r != "ack":
            raise OSError(r, "fake")


def factory(devices):
    return type("Bus", (FakeSMBus,), {"devices": devices})


def test_finds_first_ack_skipping_display_links(tree):
    tree.i2c(0, "Synopsys DesignWare HDMI DDC")
    tree.i2c(1, "DP-AUX")
    tree.i2c(2, "Meson I2C adapter")
    tree.i2c(3, "Meson I2C adapter")
    res = detect.find_display(sysfs=tree.sys, dev=tree.dev, smbus_factory=factory({(0, 0x3C): "ack", (3, 0x3D): "ack"}))
    assert (res.bus, res.address) == (3, 0x3D)
    assert any("skipped" in line for line in res.log)


def test_busy_address_skipped(tree):
    tree.i2c(0, "i2c-a")
    tree.i2c(1, "i2c-b")
    res = detect.find_display(
        sysfs=tree.sys, dev=tree.dev, smbus_factory=factory({(0, 0x3C): errno.EBUSY, (1, 0x3C): "ack"})
    )
    assert (res.bus, res.address) == (1, 0x3C)
    assert "i2c-0 (i2c-a) 0x3c: busy" in res.log


def test_explicit_bus_and_address(tree):
    tree.i2c(0, "x")
    tree.i2c(5, "y")
    res = detect.find_display(5, 0x3D, tree.sys, tree.dev, factory({(0, 0x3C): "ack", (5, 0x3D): "ack"}))
    assert (res.bus, res.address) == (5, 0x3D)


def test_no_dev_nodes_hints_i2c_dev(tree):
    tree.i2c(0, "x", node=False)
    res = detect.find_display(sysfs=tree.sys, dev=tree.dev, smbus_factory=factory({}))
    assert res.bus is None and "i2c-dev" in res.hint


def test_nothing_acks(tree):
    tree.i2c(0, "x")
    assert detect.find_display(sysfs=tree.sys, dev=tree.dev, smbus_factory=factory({})).bus is None


def test_default_route_lowest_metric(tree):
    tree.route(("wlan0", "00000000", 0x3, 600), ("end0", "00000000", 0x3, 100), ("end0", "0001A8C0", 0x1, 100))
    assert detect.default_route_iface(tree.proc) == "end0"


def test_no_default_route(tree):
    tree.route(("end0", "0001A8C0", 0x1, 100))
    assert detect.default_route_iface(tree.proc) is None
    assert detect.default_route_iface(tree.root / "nope") is None
