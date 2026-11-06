import threading
from datetime import datetime

import pytest
from PIL import Image

from sys_oled import storage
from sys_oled.app import EXIT_NO_DISPLAY, EXIT_OK, App
from sys_oled.config import ConfigLoader
from sys_oled.device import Panel

from .test_detect import factory


class FakeDevice:
    def __init__(self, size, fail_display=0):
        self.size = size
        self.calls = []
        self.fail_display = fail_display

    def display(self, image):
        if self.fail_display:
            self.fail_display -= 1
            raise OSError(121, "Remote I/O error")
        self.calls.append("display")

    def hide(self):
        self.calls.append("hide")

    def show(self):
        self.calls.append("show")

    def cleanup(self):
        self.calls.append("cleanup")


class StopAfter(threading.Event):
    """Records every wait() and sets itself after `n` of them, so loops run without sleeping."""

    def __init__(self, n, on_wait=None):
        super().__init__()
        self.n, self.waits, self.on_wait = n, [], on_wait

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.on_wait:
            self.on_wait(len(self.waits))
        if len(self.waits) >= self.n:
            self.set()
        return self.is_set()


@pytest.fixture
def hw(tree, monkeypatch):
    tree.i2c(0, "Meson I2C adapter")
    tree.zone("cpu-thermal", 45000)
    tree.route(("end0", "00000000", 0x3, 0))
    ifaces = {"end0": "10.0.0.1"}

    def fake_ipv4(iface):
        if iface not in ifaces:
            raise OSError(19, "No such device")
        return ifaces[iface]

    monkeypatch.setattr("sys_oled.stats.iface_ipv4", fake_ipv4)
    tree.write(tree.proc / "self" / "mountinfo", "")
    opened = []

    def panel_factory(model, bus, address, width, height, rotate, contrast, fail_display=0):
        dev = FakeDevice((width, height) if rotate % 2 == 0 else (height, width), hw.fail_next)
        hw.fail_next = 0
        opened.append({"model": model, "bus": bus, "address": address, "rotate": rotate, "dev": dev})
        return Panel(dev)

    class HW:
        pass

    hw = HW()
    hw.tree, hw.opened, hw.fail_next = tree, opened, 0
    hw.make = lambda stop, cli=None, environ=None: App(
        ConfigLoader([], environ=environ or {}, cli=cli or {}),
        stop=stop,
        panel_factory=panel_factory,
        sysfs=tree.sys,
        procfs=tree.proc,
        dev=tree.dev,
        smbus_factory=factory({(0, 0x3C): "ack"}),
        now=lambda: datetime(2026, 11, 6, 13, 37),
        environ=environ or {},
    )
    return hw


def flag(key, value):
    return {key: (value, f"flag:{key}")}


def test_steady_state_does_no_detection(hw):
    stop = StopAfter(9)  # 3 cycles of status + logo + ... (screen_off 0 adds no wait)
    app = hw.make(stop)
    assert app.run() == EXIT_OK
    assert app.collector.detections == {"thermal": 1, "iface": 1, "storage": 1, "fan": 1}
    assert stop.waits == [10.0, 5.0] * 4 + [10.0]
    dev = hw.opened[0]["dev"]
    assert dev.calls[-1] == "cleanup"


def test_frame_diff_skips_identical_frames():
    dev = FakeDevice((128, 64))
    p = Panel(dev)
    img = Image.new("1", (128, 64))
    assert p.show(img) and not p.show(img)
    img.putpixel((1, 1), 1)
    assert p.show(img)
    assert dev.calls == ["display", "display"]


def test_failures_redetect_only_affected_item(hw, monkeypatch):
    app = hw.make(StopAfter(1000))
    assert app.wait_for_panel()
    from sys_oled.stats import Collector

    app.collector = c = Collector(app.cfg, hw.tree.sys, hw.tree.proc)
    base = dict(c.detections)
    c.snapshot(2)
    assert c.detections == base
    (hw.tree.sys / "class" / "thermal" / "thermal_zone0" / "temp").write_text("")
    c.snapshot(2)
    assert c.detections == {**base, "thermal": base["thermal"] + 1}

    c.iface = "nonexistent0"
    c.snapshot(2)
    assert c.detections["iface"] == base["iface"] + 1 and c.iface == "end0"

    c.disks = [storage.Storage("usb", "/mnt/usb", "/mnt/usb", st_dev=99)]
    monkeypatch.setattr(storage, "_st_dev", lambda p: 1)
    c.snapshot(2)
    assert c.detections["storage"] == base["storage"] + 1


def test_change_events_trigger_exactly_one_redetect(hw):
    app = hw.make(StopAfter(1000))
    from sys_oled.stats import Collector

    c = Collector(app.cfg, hw.tree.sys, hw.tree.proc)

    class Once:
        def __init__(self, inner=None):
            self.fired, self.inner = False, inner

        def changed(self):
            fired, self.fired = not self.fired, True
            return fired

        def read(self):
            return ""

    base = dict(c.detections)
    c.mount_watch, c.route_watch = Once(), Once()
    c.snapshot(2)
    c.snapshot(2)
    assert c.detections == {**base, "storage": base["storage"] + 1, "iface": base["iface"] + 1}


def test_screen_off_hides_then_shows(hw):
    stop = StopAfter(4)  # status, logo, off, status
    app = hw.make(stop, cli=flag("cycle.screen_off", "30"))
    app.run()
    assert stop.waits == [10.0, 5.0, 30.0, 10.0]
    assert hw.opened[0]["dev"].calls == ["display", "display", "hide", "show", "display", "cleanup"]


def test_sigterm_during_off_phase_exits_cleanly(hw):
    stop = StopAfter(3)
    app = hw.make(stop, cli=flag("cycle.screen_off", "300"))
    assert app.run() == EXIT_OK
    assert stop.waits[-1] == 300.0
    assert hw.opened[0]["dev"].calls[-2:] == ["hide", "cleanup"]


def test_missing_display_exit_and_wait(hw):
    hw.tree.dev.joinpath("i2c-0").unlink()
    assert hw.make(StopAfter(1)).run() == EXIT_NO_DISPLAY
    stop = StopAfter(3)
    assert hw.make(stop, cli=flag("host.missing_display", "wait")).run() == EXIT_OK
    assert stop.waits == [60.0, 60.0, 60.0]


def test_display_write_error_reprobes(hw):
    hw.fail_next = 1
    stop = StopAfter(3)
    hw.make(stop).run()
    assert len(hw.opened) == 2  # first re-probe succeeded right away, no backoff wait
    assert stop.waits == [10.0, 5.0, 10.0]


def test_node_display_change_reopens_panel(hw):
    app = hw.make(StopAfter(1000))
    assert app.wait_for_panel()
    app._on_node({"metadata": {"labels": {"sys-oled/display.rotate": "0"}}})
    app._on_node({"metadata": {"labels": {"sys-oled/display.rotate": "0"}}})  # unchanged: no-op
    app.apply_pending()
    assert [o["rotate"] for o in hw.opened] == [2, 0]
    assert hw.opened[0]["dev"].calls[-1] == "cleanup"
    app._on_node({"metadata": {"labels": {"sys-oled/display.rotate": "0", "sys-oled/cycle.refresh": "3"}}})
    app.apply_pending()
    assert len(hw.opened) == 2 and app.cfg.cycle.refresh == 3


def test_pages_order_skip_and_logo_seconds(hw):
    from .conftest import add_fan

    stop = StopAfter(4)
    app = hw.make(
        stop, cli={**flag("cycle.pages", "[temp, kubernetes, fan, load, logo]"), **flag("cycle.logo_seconds", "2")}
    )
    app.run()
    assert stop.waits == [10.0, 10.0, 2.0, 10.0]  # temp, (no k8s), (no fan), load, logo; then temp again
    add_fan(hw.tree, "pwmfan", {1: 1500})
    stop = StopAfter(3)
    app = hw.make(stop, cli=flag("cycle.pages", "[fan, logo]"))
    app.run()
    assert stop.waits == [10.0, 5.0, 10.0]


def test_no_applicable_page_falls_back_to_status(hw):
    stop = StopAfter(2)
    hw.make(stop, cli=flag("cycle.pages", "[fan, kubernetes]")).run()
    assert stop.waits == [10.0, 10.0]


def test_temp_alert_flashes_and_clears(hw):
    zone = hw.tree.sys / "class" / "thermal" / "thermal_zone0" / "temp"
    shown = []

    def on_wait(n):
        if n == 1:
            zone.write_text("85000\n")  # overheats during the first status wait chunk
        if n == 5:
            zone.write_text("78500\n")  # within hysteresis (limit 80, clears below 77)
        if n == 7:
            zone.write_text("60000\n")

    stop = StopAfter(9, on_wait)
    app = hw.make(stop, cli=flag("alerts.temp_max", "80"))
    orig = app._show
    app._show = lambda img: (shown.append(img.getpixel((0, 0))), orig(img))
    app.run()
    assert stop.waits[:8] == [5.0, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 5.0]  # chunked wait, flashing, back to status
    assert shown[1:7] == [0, 255, 0, 255, 0, 255]  # inverted every other frame
    assert app.alerting is False


def test_fan_alert_wakes_panel_from_screen_off(hw):
    from .conftest import add_fan

    h = add_fan(hw.tree, "pwmfan", {1: 1500})

    def on_wait(n):
        if n == 4:  # first check during screen-off (status 5+5, logo 5, off 5...)
            (h / "fan1_input").write_text("0\n")

    stop = StopAfter(6, on_wait)
    app = hw.make(
        stop, cli={**flag("alerts.fan_min", "500"), **flag("alerts.flash", "false"), **flag("cycle.screen_off", "60")}
    )
    app.run()
    assert stop.waits == [5.0, 5.0, 5.0, 5.0, 0.5, 0.5]  # alert after the first off chunk
    calls = hw.opened[0]["dev"].calls
    assert calls.index("hide") < calls.index("show")


def test_alerts_off_means_plain_waits(hw):
    stop = StopAfter(2)
    hw.make(stop).run()
    assert stop.waits == [10.0, 5.0]
