"""Command line: run (default) | detect | render | config. Setting flags are generated from the config schema."""

import argparse
import io
import json
import logging
import os
import signal
import sys
import threading
from datetime import datetime
from pathlib import Path

from . import __version__, detect, fan, storage, thermal
from .app import EXIT_CONFIG, EXIT_NO_DISPLAY, EXIT_OK, App
from .config import (
    BY_KEY,
    LEGACY_FILE,
    PAGES,
    SCHEMA,
    ConfigError,
    ConfigLoader,
    Layer,
    read_legacy_ini,
    render,
    render_effective,
    template,
)
from .device import logical_size

log = logging.getLogger("sys_oled")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-c",
        "--config",
        action="append",
        type=Path,
        default=argparse.SUPPRESS,
        help="config file(s) to use instead of /etc/sys-oled.yaml + /etc/sys-oled.d/*.yaml",
    )
    common.add_argument("-v", "--verbose", action="count", default=argparse.SUPPRESS, help="more logging")
    settings = common.add_argument_group("settings (override config files and env; see 'sys-oled config --defaults')")
    for s in SCHEMA:
        settings.add_argument(
            s.flag,
            *s.legacy_flags,
            dest=s.dest,
            metavar="VALUE",
            default=argparse.SUPPRESS,
            help=s.help.replace("%", "%%"),
        )

    parser = argparse.ArgumentParser(
        prog="sys-oled",
        parents=[common],
        allow_abbrev=False,
        description="System status on SSD1306-class I2C OLED displays.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="{run,detect,render,config}")
    sub.add_parser("run", parents=[common], allow_abbrev=False, help="drive the display (default)")
    sub.add_parser(
        "detect",
        parents=[common],
        allow_abbrev=False,
        help="show detected display, sensors, network and storage; exit 3 without a display",
    )
    rp = sub.add_parser("render", parents=[common], allow_abbrev=False, help="render a page to PNG, no hardware")
    rp.add_argument("--png", required=True, type=Path, help="output file, - for stdout")
    rp.add_argument("--page", choices=(*PAGES, "alert"), default="status")
    rp.add_argument("--sample", action="store_true", help="use fixed sample data instead of this machine's")
    rp.add_argument("--kube-fixture", type=Path, help="dir with version/node/nodes/pods(/cluster_pods).json")
    rp.add_argument("--scale", type=int, default=1, help="integer upscale factor for the PNG")
    cp = sub.add_parser(
        "config",
        parents=[common],
        allow_abbrev=False,
        help="print the effective configuration with the source of every value",
    )
    mode = cp.add_mutually_exclusive_group()
    mode.add_argument("--defaults", action="store_true", help="print the commented default template")
    mode.add_argument("--migrate", action="store_true", help="convert a legacy INI config to YAML")
    cp.add_argument("--from", dest="src", type=Path, default=LEGACY_FILE, help="legacy INI (default %(default)s)")
    cp.add_argument("--to", dest="dst", type=Path, help="write YAML here instead of stdout (refuses to overwrite)")
    return parser


def cli_layer(ns: argparse.Namespace) -> Layer:
    return {s.key: (getattr(ns, s.dest), f"flag:{s.flag}") for s in SCHEMA if hasattr(ns, s.dest)}


def setup_logging(verbosity: int) -> None:
    level = logging.DEBUG if verbosity else logging.INFO
    fmt = "%(levelname)s %(message)s" if os.environ.get("JOURNAL_STREAM") else "%(asctime)s %(levelname)s %(message)s"
    logging.basicConfig(level=level, format=fmt, stream=sys.stderr)


def cmd_run(loader: ConfigLoader) -> int:
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    return App(loader, stop=stop).run()


def cmd_detect(loader: ConfigLoader) -> int:
    cfg = loader.build()
    d = cfg.display
    found = detect.find_display(d.i2c_bus, d.i2c_address)
    print("display:")
    for line in found.log:
        print(f"  {line}")
    print(f"  => {f'i2c-{found.bus} 0x{found.address:02x} ({d.model})' if found.bus is not None else 'NOT FOUND'}")
    sensors = thermal.scan()
    picked, why = thermal.select(sensors, cfg.sensors.thermal)
    print(f"thermal: ({why})")
    for s in sorted(sensors, key=lambda s: (s.tier < 0, s.tier, s.name)):
        mark = "*" if s in picked else " "
        tier = thermal.TIERS[s.tier][0] if s.tier >= 0 else "excluded"
        temp = f"{s.temp:.1f}C" if s.temp is not None else "n/a"
        print(f"  {mark} {s.name:<32} {tier:<12} {temp}")
    iface = detect.default_route_iface()
    fans = fan.select(fan.scan(), cfg.sensors.fan)
    print(f"fans: {', '.join(f'{f.name}={f.rpm}rpm' for f in fans) or 'none'}")
    print(f"network: default route via {iface or 'none'}; configured: {cfg.sensors.network}")
    root = cfg.host.root
    mountinfo = Path("/proc") / ("1" if root not in ("", "/") else "self") / "mountinfo"
    try:
        text = mountinfo.read_text()
    except OSError:
        text = ""
    print("storage:")
    for st in storage.rank(text, root, cfg.storage.mounts, cfg.storage.auto):
        print(f"  {st.label:<6} {st.mountpoint:<24} {st.total / (1 << 30):8.1f} GiB")
    return EXIT_OK if found.bus is not None else EXIT_NO_DISPLAY


def _kube_data(fixture: Path | None) -> dict:
    from .kube import short_version, summarize

    if fixture is None:
        return {
            "node": "hc4-1",
            "version": "v1.34",
            "control_plane": True,
            "status": "Ready",
            "pods_running": 23,
            "pods_total": 25,
            "pods_bad": 2,
            "nodes_ready": 3,
            "nodes_total": 3,
            "cluster_bad": 4,
        }

    def load(name: str):
        path = fixture / f"{name}.json"
        return json.loads(path.read_text()) if path.exists() else None

    cluster = load("cluster_pods")
    return summarize(
        load("node"),
        load("nodes")["items"],
        load("pods")["items"],
        cluster["items"] if cluster else None,
        short_version(load("version")["gitVersion"]),
        datetime.now().timestamp(),
    )


def cmd_render(loader: ConfigLoader, ns: argparse.Namespace) -> int:
    from .screen import Renderer, disk_slots
    from .stats import Collector, sample_snapshot

    cfg = loader.build()
    d = cfg.display
    r = Renderer(logical_size(d.width, d.height, d.rotate), cfg.cycle.logo)
    if ns.page == "kubernetes":
        image = r.kubernetes(_kube_data(ns.kube_fixture))
        return _save(image, ns)
    if ns.page == "alert":
        return _save(r.alert("HOT 85C", "limit 80C"), ns)
    snap = sample_snapshot() if ns.sample else Collector(cfg).snapshot(disk_slots(r.height))
    if ns.page == "logo":
        now = datetime(2026, 11, 6, 13, 37) if ns.sample else datetime.now()
        image = r.logo_page(now, snap.hostname)
    elif ns.page == "load":
        image = r.big("CPU load", f"{snap.load:.0f}%", " ".join(f"{v:.2f}" for v in snap.loadavg))
    elif ns.page == "temp":
        image = r.big("CPU temp", f"{snap.temp:.0f}C" if snap.temp is not None else "--")
    elif ns.page == "fan":
        image = r.big("Fan", str(snap.fan_rpm) if snap.fan_rpm is not None else "--", "rpm")
    else:
        image = r.status(snap)
    return _save(image, ns)


def _save(image, ns: argparse.Namespace) -> int:
    if ns.scale > 1:
        image = image.resize((image.width * ns.scale, image.height * ns.scale))
    if str(ns.png) in ("-", "/dev/stdout"):
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        sys.stdout.buffer.write(buf.getvalue())
    else:
        image.save(ns.png, format="PNG")
    return EXIT_OK


def cmd_config(loader: ConfigLoader | None, ns: argparse.Namespace) -> int:
    if ns.defaults:
        sys.stdout.write(template())
        return EXIT_OK
    if ns.migrate:
        layer, _ = read_legacy_ini(ns.src)
        values = {k: BY_KEY[k].parse(v) for k, (v, _) in layer.items()}
        comments = {k: f"migrated from {loc}" for k, (_, loc) in layer.items()}
        text = render(values, comments, active=values)
        if ns.dst is None:
            sys.stdout.write(text)
        else:
            if ns.dst.exists():
                print(f"{ns.dst} already exists, not overwriting", file=sys.stderr)
                return 1
            ns.dst.write_text(text)
            print(f"wrote {ns.dst} from {ns.src}", file=sys.stderr)
        return EXIT_OK
    cfg = loader.build()
    for w in cfg.warnings:
        print(f"warning: {w}", file=sys.stderr)
    sys.stdout.write(render_effective(cfg))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    setup_logging(getattr(ns, "verbose", 0))
    command = ns.command or "run"
    try:
        if command == "config" and (ns.defaults or ns.migrate):
            return cmd_config(None, ns)
        loader = ConfigLoader(getattr(ns, "config", None), cli=cli_layer(ns))
        if command == "detect":
            return cmd_detect(loader)
        if command == "render":
            return cmd_render(loader, ns)
        if command == "config":
            return cmd_config(loader, ns)
        return cmd_run(loader)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return EXIT_CONFIG
