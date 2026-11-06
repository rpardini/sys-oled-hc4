"""luma.oled device wrapper: frame-diffed sends, panel on/off, clean shutdown."""

import atexit
import logging

from PIL import Image

log = logging.getLogger(__name__)


def logical_size(width: int, height: int, rotate: int) -> tuple[int, int]:
    return (width, height) if rotate % 2 == 0 else (height, width)


class Panel:
    def __init__(self, device):
        self.device = device
        self.size = device.size
        self._last: bytes | None = None
        self.sent = 0

    @classmethod
    def open(cls, model: str, bus: int, address: int, width: int, height: int, rotate: int, contrast: int) -> "Panel":
        from luma.core.interface.serial import i2c
        from luma.oled import device as oled

        factory = getattr(oled, model, None)
        if factory is None:
            raise ValueError(f"display model '{model}' is not supported by this luma.oled version")
        dev = factory(i2c(port=bus, address=address), width=width, height=height, rotate=rotate)
        dev.contrast(contrast)
        log.info("opened %s %dx%d on i2c-%d 0x%02x rotate=%d", model, width, height, bus, address, rotate)
        return cls(dev)

    def show(self, image: Image.Image) -> bool:
        """Send the frame only if it differs from the last one sent. Returns True if sent."""
        data = image.tobytes()
        if data == self._last:
            return False
        self.device.display(image)
        self._last = data
        self.sent += 1
        return True

    def off(self) -> None:
        self.device.hide()

    def on(self) -> None:
        self.device.show()
        self._last = None  # force a redraw after waking

    def close(self) -> None:
        """Blank the panel and release the bus (luma's cleanup: hide + clear unless persist)."""
        try:
            self.device.cleanup()
        except Exception as e:  # best effort on shutdown, the bus may already be gone
            log.debug("panel close: %s", e)
        atexit.unregister(self.device.cleanup)
