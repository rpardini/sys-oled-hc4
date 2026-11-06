"""Pure rendering into one reused 1-bit image. No I/O besides loading assets once."""

import io
import logging
from datetime import datetime
from importlib.resources import files
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .stats import Snapshot

log = logging.getLogger(__name__)
ASSETS = files("sys_oled") / "assets"
FONT = "cnc-red-alert-inet.ttf"
LINE = 12
BODY_TOP = 15


def bytes2human(n: float) -> str:
    symbols = ("K", "M", "G", "T", "P", "E", "Z", "Y")
    for i, s in reversed(list(enumerate(symbols))):
        unit = 1 << (i + 1) * 10
        if n >= unit:
            value = n / unit
            return f"{int(value)}{s}" if s in ("K", "M") else f"{value:.1f}{s}"
    return f"{int(n)}B"


def fmt_uptime(seconds: float) -> str:
    minutes = int(seconds // 60)
    days, rem = divmod(minutes, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}:{mins:02d}"
    return f"{mins}m"


def body_lines(height: int) -> int:
    return max(0, (height - BODY_TOP) // LINE)


def disk_slots(height: int, fan: bool = False) -> int:
    """Storage lines that fit between mem (+ fan) and net (mem is dropped first on short panels)."""
    return max(0, body_lines(height) - 2 - int(fan))


def load_logo(spec: str, width: int) -> Image.Image | None:
    if spec == "none":
        return None
    if spec == "armbian":
        name = "armbian-wordmark-128.png" if width >= 128 else "armbian-wordmark-96.png"
        with (ASSETS / name).open("rb") as f:
            return Image.open(f).convert("1")
    try:
        return Image.open(Path(spec)).convert("1")
    except OSError as e:
        log.warning("cannot load logo %s: %s", spec, e)
        return None


class Renderer:
    def __init__(self, size: tuple[int, int], logo: str = "armbian"):
        self.size = size
        self.width, self.height = size
        with (ASSETS / FONT).open("rb") as f:
            data = f.read()
        self._font_data = data
        self._fonts: dict[int, ImageFont.FreeTypeFont] = {}
        self.font = self._font(12)
        self.font_big = self._font(30 if size[1] >= 64 else 18)
        self.image = Image.new("1", size)
        self.draw = ImageDraw.Draw(self.image)
        self.logo = load_logo(logo, self.width if self.height >= 64 else 96)

    def _font(self, size: int) -> ImageFont.FreeTypeFont:
        if size not in self._fonts:
            self._fonts[size] = ImageFont.truetype(io.BytesIO(self._font_data), size)
        return self._fonts[size]

    def _clear(self, fill: int = 0) -> None:
        self.draw.rectangle((0, 0, self.width, self.height), fill=fill)

    def _fitted(self, text: str, box: tuple[int, int, int, int], fill: int = 255) -> None:
        """Draw text centered in box with the largest font size that fits."""
        x0, y0, x1, y1 = box
        for size in range(64, 9, -2):
            font = self._font(size)
            left, top, right, bottom = self.draw.textbbox((0, 0), text, font=font)
            if right - left <= x1 - x0 and bottom - top <= y1 - y0:
                break
        x = x0 + (x1 - x0 - (right - left)) // 2 - left
        y = y0 + (y1 - y0 - (bottom - top)) // 2 - top
        self.draw.text((x, y), text, font=font, fill=fill)

    def big(self, label: str, value: str, footer: str | None = None) -> Image.Image:
        """Glanceable page: small label, one value as large as fits, optional small footer."""
        self._clear()
        self.draw.text((0, 0), label, font=self.font, fill=255)
        self.draw.line((0, 13, self.width, 13), fill=255)
        bottom = self.height
        if footer and self.height >= 64:
            bottom = self.height - LINE - 1
            fw = self.draw.textlength(footer, font=self.font)
            self.draw.text((max(0, (self.width - fw) // 2), bottom), footer, font=self.font, fill=255)
        self._fitted(value, (0, BODY_TOP, self.width, bottom - 1))
        return self.image

    def alert(self, title: str, detail: str, inverted: bool = False) -> Image.Image:
        fg = 0 if inverted else 255
        self._clear(fill=255 - fg)
        bottom = self.height
        if self.height >= 64:
            bottom = self.height - LINE - 1
            dw = self.draw.textlength(detail, font=self.font)
            self.draw.text((max(0, (self.width - dw) // 2), bottom), detail, font=self.font, fill=fg)
        self._fitted(title, (0, 0, self.width, bottom - 1), fill=fg)
        return self.image

    def _lines(self, header: str, lines: list[str]) -> Image.Image:
        self._clear()
        self.draw.text((0, 0), header, font=self.font, fill=255)
        self.draw.line((0, 13, self.width, 13), fill=255)
        for i, text in enumerate(lines[: body_lines(self.height)]):
            self.draw.text((0, BODY_TOP + i * LINE), text, font=self.font, fill=255)
        return self.image

    def status(self, s: Snapshot) -> Image.Image:
        temp = f"{s.temp:.0f}C" if s.temp is not None else "--"
        header = f"ld: {s.load:.0f}% T: {temp} up: {fmt_uptime(s.uptime)}"
        mem = f"mem: {bytes2human(s.mem_used)} / {bytes2human(s.mem_total)} - {s.mem_percent:.0f}%"
        disks = [f"{label}: {bytes2human(u.used)} / {bytes2human(u.total)} - {u.percent:.0f}%" for label, u in s.disks]
        net = f"{s.iface}: {s.ip or 'no ip'}" if s.iface else "net: --"
        fan = [f"fan: {s.fan_rpm} rpm"] if s.fan_rpm is not None else []
        n = body_lines(self.height)
        lines = [mem, *fan, *disks[: disk_slots(self.height, bool(fan))], net] if n >= 2 + len(fan) else [net][:n]
        return self._lines(header, lines)

    def kubernetes(self, k: dict) -> Image.Image:
        if k.get("error") and not k.get("node"):
            return self._lines("k8s", [f"k8s: api err {k['error']}"])
        star = "*" if k.get("control_plane") else ""
        header = f"k8s {k.get('version', '?')} {k.get('node', '?')}{star}"
        stale = " (stale)" if k.get("stale") else ""
        lines = [
            f"node: {k.get('status', '?')}{stale}",
            f"pods: {k.get('pods_running', '?')} run / {k.get('pods_total', '?')}",
            f"bad: {k.get('pods_bad', '?')}",
            f"nodes {k.get('nodes_ready', '?')}/{k.get('nodes_total', '?')}"
            + (f"  cl-bad {k['cluster_bad']}" if k.get("cluster_bad") is not None else ""),
        ]
        if self.height < 64:
            lines = [f"{lines[0]} {lines[1]}"]
        return self._lines(header, lines)

    def logo_page(self, now: datetime, hostname: str) -> Image.Image:
        self._clear()
        top, bottom = 0, self.height
        if self.logo is not None:
            self.image.paste(self.logo, ((self.width - self.logo.width) // 2, 0))
            top = self.logo.height
        if self.height >= 64:
            bottom = self.height - LINE - 1
            foot = f"{now:%Y-%m-%d}  {hostname}"
            fw = self.draw.textlength(foot, font=self.font)
            self.draw.text((max(0, (self.width - fw) // 2), bottom), foot, font=self.font, fill=255)
        clock = now.strftime("%H:%M")
        left, ctop, right, cbottom = self.draw.textbbox((0, 0), clock, font=self.font_big)
        x = (self.width - (right - left)) // 2 - left
        y = top + (bottom - top - (cbottom - ctop)) // 2 - ctop
        self.draw.text((x, y), clock, font=self.font_big, fill=255)
        return self.image
