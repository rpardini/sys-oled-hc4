"""Main display loop: status -> k8s -> logo -> screen-off, with failure-driven recovery."""

import logging
import os
import socket
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import psutil

from . import detect
from .config import Config, ConfigLoader
from .device import Panel, logical_size
from .screen import Renderer, disk_slots
from .stats import Collector

log = logging.getLogger(__name__)
EXIT_OK, EXIT_CONFIG, EXIT_NO_DISPLAY = 0, 2, 3
MISSING_RETRY = 60.0
ALERT_CHECK = 5.0  # seconds between alert checks while waiting (two sysfs reads)
FLASH_INTERVAL = 0.5


class App:
    def __init__(
        self,
        loader: ConfigLoader,
        stop: threading.Event | None = None,
        panel_factory: Callable[..., Panel] = Panel.open,
        sysfs: Path = Path("/sys"),
        procfs: Path = Path("/proc"),
        dev: Path = Path("/dev"),
        smbus_factory=None,
        now: Callable[[], datetime] = datetime.now,
        environ=os.environ,
    ):
        self.loader = loader
        self.stop = stop or threading.Event()
        self.panel_factory, self.smbus_factory = panel_factory, smbus_factory
        self.sysfs, self.procfs, self.dev, self.now, self.environ = sysfs, procfs, dev, now, environ
        self.cfg = self._build()
        self._pending: Config | None = None
        self._lock = threading.Lock()
        self.panel: Panel | None = None
        self.panel_off = False
        self.alerting = False
        self.collector: Collector | None = None
        self.renderer: Renderer | None = None
        self.kpage = None
        self.watcher = None

    def _build(self) -> Config:
        cfg = self.loader.build()
        for w in cfg.warnings:
            log.warning("config: %s", w)
        return cfg

    # --- kubernetes ---

    def kube_enabled(self) -> bool:
        from .kube import in_cluster

        enabled = self.cfg.kubernetes.enabled
        return in_cluster(self.environ) if enabled == "auto" else bool(enabled)

    def setup_kube(self, client=None) -> None:
        from .kube import ApiError, KubeClient, KubePage, NodeWatcher

        client = client or KubeClient(environ=self.environ)
        node_name = self.environ.get("NODE_NAME") or socket.gethostname()
        self.watcher = NodeWatcher(client, node_name, self._on_node, self.stop)
        try:  # before the display is opened, so bus/model/rotation from node metadata apply
            self.watcher.fetch(timeout=3.0)
        except ApiError as e:
            log.warning("k8s: cannot read node %s yet (%s); using file/env config until it answers", node_name, e)
        self.watcher.start()
        self.kpage = KubePage(client, node_name, self.watcher)

    def _on_node(self, node: dict) -> None:
        md = node.get("metadata", {})
        if self.loader.set_node_metadata(md.get("labels"), md.get("annotations")):
            log.info("k8s: node configuration changed")
            cfg = self._build()
            with self._lock:
                self._pending = cfg

    def apply_pending(self) -> None:
        with self._lock:
            cfg, self._pending = self._pending, None
        if cfg is None:
            return
        old, self.cfg = self.cfg, cfg
        if self.collector:
            self.collector.apply_config(cfg)
        if self.panel and old.section_values("display") != cfg.section_values("display"):
            log.info("display configuration changed, reopening")
            self.panel.close()
            self.panel = None
            self._recover()
        elif self.renderer and old.cycle.logo != cfg.cycle.logo:
            self._make_renderer()

    # --- display ---

    def open_panel(self) -> bool:
        d = self.cfg.display
        found = detect.find_display(d.i2c_bus, d.i2c_address, self.sysfs, self.dev, self.smbus_factory)
        for line in found.log:
            log.debug("probe: %s", line)
        if found.bus is None:
            log.warning("no display found%s", f": {found.hint}" if found.hint else "")
            return False
        try:
            self.panel = self.panel_factory(d.model, found.bus, found.address, d.width, d.height, d.rotate, d.contrast)
        except (OSError, ValueError) as e:
            log.warning("cannot open display: %s", e)
            return False
        self.panel_off = False
        self._make_renderer()
        return True

    def _make_renderer(self) -> None:
        d = self.cfg.display
        self.renderer = Renderer(logical_size(d.width, d.height, d.rotate), self.cfg.cycle.logo)

    def wait_for_panel(self) -> bool:
        while not self.stop.is_set():
            if self.open_panel():
                return True
            if self.cfg.host.missing_display == "exit":
                return False
            log.info("waiting for a display, re-probing in %.0fs", MISSING_RETRY)
            if self.stop.wait(MISSING_RETRY):
                return False
            self.apply_pending()
        return False

    def _recover(self) -> None:
        backoff = 5.0
        while not self.stop.is_set():
            if self.open_panel():
                return
            if self.stop.wait(backoff):
                return
            backoff = min(backoff * 2, 60.0)

    def _show(self, image) -> None:
        try:
            if self.panel_off:
                self.panel.on()
                self.panel_off = False
            self.panel.show(image)
        except OSError as e:
            log.warning("display write failed (%s), re-probing", e)
            self.panel.close()
            self.panel = None
            self._recover()

    # --- loop ---

    # --- alerts ---

    def alerts_enabled(self) -> bool:
        a = self.cfg.alerts
        return a.temp_max > 0 or (a.fan_min > 0 and bool(self.collector.fans))

    def check_alert(self) -> tuple[str, str] | None:
        """Cheap: one thermal read and the fan files. Hysteresis keeps a hot alert until 3C below the limit."""
        a = self.cfg.alerts
        if a.temp_max > 0:
            t = self.collector.read_temp()
            if t is not None and t > a.temp_max - (3 if self.alerting else 0):
                return f"HOT {t:.0f}C", f"limit {a.temp_max:.0f}C"
        if a.fan_min > 0 and self.collector.fans:
            rpms = self.collector.read_fans()
            if rpms and min(rpms) < a.fan_min:
                return f"FAN {min(rpms)}", f"rpm, minimum {a.fan_min}"
        return None

    def alert_loop(self) -> None:
        self.alerting = True
        inverted = False
        first = True
        while not self.stop.is_set() and self.panel is not None:
            alert = self.check_alert()
            if alert is None:
                break
            if first:
                log.warning("alert: %s (%s)", *alert)
                first = False
            self._show(self.renderer.alert(*alert, inverted=inverted))
            inverted = not inverted and self.cfg.alerts.flash
            self.stop.wait(FLASH_INTERVAL)
        if not first:
            log.info("alert cleared")
        self.alerting = False

    def _wait(self, seconds: float) -> bool:
        """Sleep; with alerts enabled, check every ALERT_CHECK seconds. Returns True if an alert fired."""
        if not self.alerts_enabled():
            self.stop.wait(seconds)
            return False
        remaining = seconds
        while remaining > 0:
            chunk = min(ALERT_CHECK, remaining)
            if self.stop.wait(chunk):
                return False
            remaining -= chunk
            if self.check_alert():
                return True
        return False

    # --- loop ---

    def _page(self, page: str):
        """Render a page, or None if it does not apply here (no k8s, no fan, logo: none)."""
        r, c = self.renderer, self.collector
        if page == "status":
            return r.status(c.snapshot(disk_slots(r.height, bool(c.fans))))
        if page == "kubernetes":
            if not self.kpage:
                return None
            k = self.cfg.kubernetes
            return r.kubernetes(self.kpage.get(k.refresh, k.cluster_pods))
        if page == "logo":
            return r.logo_page(self.now(), c.hostname) if r.logo is not None else None
        if page == "load":
            one, five, fifteen = os.getloadavg()
            return r.big("CPU load", f"{psutil.cpu_percent(interval=None):.0f}%", f"{one:.2f} {five:.2f} {fifteen:.2f}")
        if page == "temp":
            t = c.read_temp()
            return r.big("CPU temp", f"{t:.0f}C" if t is not None else "--", None)
        if page == "fan":
            if not c.fans:
                return None
            rpms = c.read_fans()
            return r.big("Fan", str(max(rpms)) if rpms else "--", "rpm")
        return None

    def _seconds(self, page: str) -> float:
        c = self.cfg.cycle
        if page == "logo":
            return c.refresh / 2 if c.logo_seconds == "auto" else c.logo_seconds
        return c.refresh

    def cycle(self) -> None:
        self.apply_pending()
        c = self.cfg.cycle
        if self.alerts_enabled() and self.check_alert():
            self.alert_loop()
            return
        shown = False
        for page in c.pages:
            if self.stop.is_set() or self.panel is None:
                return
            image = self._page(page)
            if image is None:
                continue
            shown = True
            self._show(image)
            if self._wait(self._seconds(page)):
                self.alert_loop()
                return
        if not shown:  # nothing applicable configured: never spin
            self._show(self._page("status"))
            if self._wait(c.refresh):
                self.alert_loop()
                return
        if c.screen_off > 0 and self.panel and not self.stop.is_set():
            try:
                self.panel.off()
                self.panel_off = True
            except OSError as e:
                log.warning("display off failed: %s", e)
            if self._wait(c.screen_off):
                self.alert_loop()

    def run(self) -> int:
        if self.kube_enabled():
            self.setup_kube()
        self.apply_pending()
        if not self.wait_for_panel():
            return EXIT_OK if self.stop.is_set() else EXIT_NO_DISPLAY
        self.collector = Collector(self.cfg, self.sysfs, self.procfs)
        try:
            while not self.stop.is_set():
                self.cycle()
        finally:
            if self.panel:
                self.panel.close()
        return EXIT_OK
