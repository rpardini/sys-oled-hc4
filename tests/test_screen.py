from datetime import datetime

from sys_oled.screen import Renderer, bytes2human, disk_slots, fmt_uptime
from sys_oled.stats import sample_snapshot


def test_bytes2human():
    assert bytes2human(512) == "512B"
    assert bytes2human(2048) == "2K"
    assert bytes2human(5 * (1 << 20) + 1) == "5M"
    assert bytes2human(int(1.25 * (1 << 30))) == "1.2G"
    assert bytes2human(7.2 * (1 << 40)) == "7.2T"


def test_fmt_uptime():
    assert fmt_uptime(59) == "0m"
    assert fmt_uptime(12 * 60) == "12m"
    assert fmt_uptime(4 * 3600 + 5 * 60) == "4:05"
    assert fmt_uptime(3 * 86400 + 4 * 3600) == "3d4h"


def test_layout_slots():
    assert disk_slots(64) == 2
    assert disk_slots(32) == 0


def test_pages_render_at_both_heights():
    for size in ((128, 64), (128, 32)):
        r = Renderer(size)
        for img in (
            r.status(sample_snapshot()),
            r.logo_page(datetime(2026, 1, 1), "host"),
            r.kubernetes({"node": "n", "error": 403}),
        ):
            assert img.size == size and img.mode == "1" and img.getbbox() is not None


def test_logo_none():
    r = Renderer((128, 64), "none")
    assert r.logo is None
