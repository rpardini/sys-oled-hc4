"""Unified configuration.

A single schema (SCHEMA) drives everything: YAML files, env vars, Kubernetes node labels/annotations,
CLI flags, the shipped template and the effective-config dump. Precedence, lowest to highest:
defaults < /etc/sys-oled.yaml < /etc/sys-oled.d/*.yaml < env < node labels < node annotations < CLI flags.
"""

import configparser
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

AUTO = "auto"
MAIN_FILE = Path("/etc/sys-oled.yaml")
DROPIN_DIR = Path("/etc/sys-oled.d")
LEGACY_FILE = Path("/etc/sys-oled.conf")
ENV_PREFIX = "SYS_OLED_"
RESERVED_NODE_KEYS = {"enabled"}


class ConfigError(Exception):
    pass


# --- value parsers: accept native YAML values and raw strings (env, labels, flags) ---


def _yaml_scalar(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return yaml.safe_load(value)
        except yaml.YAMLError as e:
            raise ValueError(f"invalid value {value!r}") from e
    return value


def p_int(lo: int, hi: int | None = None) -> Callable[[Any], int]:
    def parse(value: Any) -> int:
        v = _yaml_scalar(value)
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"expected an integer, got {value!r}")
        if v < lo or (hi is not None and v > hi):
            raise ValueError(f"{v} out of range {lo}..{hi if hi is not None else ''}")
        return v

    return parse


def p_float(lo: float) -> Callable[[Any], float]:
    def parse(value: Any) -> float:
        v = _yaml_scalar(value)
        if isinstance(v, bool) or not isinstance(v, int | float):
            raise ValueError(f"expected a number, got {value!r}")
        if v < lo:
            raise ValueError(f"{v} must be >= {lo}")
        return float(v)

    return parse


def p_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("yes", "true", "on", "1"):
        return True
    if isinstance(value, str) and value.strip().lower() in ("no", "false", "off", "0"):
        return False
    raise ValueError(f"expected a boolean, got {value!r}")


def p_str(value: Any) -> str:
    if value is None or isinstance(value, list | dict):
        raise ValueError(f"expected a string, got {value!r}")
    return str(value)


def p_choice(*options: str) -> Callable[[Any], str]:
    def parse(value: Any) -> str:
        v = str(value).strip().lower()
        if v not in options:
            raise ValueError(f"expected one of {', '.join(options)}, got {value!r}")
        return v

    return parse


def p_auto_or(inner: Callable[[Any], Any]) -> Callable[[Any], Any]:
    def parse(value: Any) -> Any:
        if isinstance(value, str) and value.strip().lower() == AUTO:
            return AUTO
        return inner(value)

    return parse


def p_address(value: Any) -> int:
    v = value
    if isinstance(v, str):
        try:
            v = int(v.strip(), 0)
        except ValueError as e:
            raise ValueError(f"expected an I2C address like 0x3c, got {value!r}") from e
    if isinstance(v, bool) or not isinstance(v, int) or not 0x03 <= v <= 0x77:
        raise ValueError(f"I2C address out of range 0x03..0x77: {value!r}")
    return v


def p_mounts(value: Any) -> list[dict[str, str]]:
    v = _yaml_scalar(value)
    if v is None:
        return []
    if not isinstance(v, list):
        raise ValueError("expected a list like [{label: md0, path: /mnt/md0}]")
    out = []
    for item in v:
        if not isinstance(item, dict) or set(item) != {"label", "path"}:
            raise ValueError(f"each mount needs exactly 'label' and 'path', got {item!r}")
        out.append({"label": str(item["label"]), "path": str(item["path"])})
    return out


PAGES = ("status", "kubernetes", "logo", "load", "temp", "fan")


def p_pages(value: Any) -> list[str]:
    v = _yaml_scalar(value)
    if isinstance(v, str):
        v = [p.strip() for p in v.split(",") if p.strip()]
    if not isinstance(v, list) or not v:
        raise ValueError(f"expected a non-empty list of pages ({', '.join(PAGES)})")
    bad = [p for p in v if p not in PAGES]
    if bad:
        raise ValueError(f"unknown page(s) {', '.join(map(str, bad))}; expected {', '.join(PAGES)}")
    return [str(p) for p in v]


def _fmt_address(value: Any) -> str:
    return value if value == AUTO else f"0x{value:02x}"


@dataclass(frozen=True)
class Setting:
    key: str
    default: Any
    parse: Callable[[Any], Any]
    help: str
    legacy_flags: tuple[str, ...] = ()
    fmt: Callable[[Any], str] | None = None

    @property
    def section(self) -> str:
        return self.key.split(".")[0]

    @property
    def name(self) -> str:
        return self.key.split(".")[1]

    @property
    def env(self) -> str:
        return ENV_PREFIX + self.key.replace(".", "_").upper()

    @property
    def flag(self) -> str:
        return "--" + self.key.replace(".", "-").replace("_", "-")

    @property
    def dest(self) -> str:
        return "cfg__" + self.key.replace(".", "__")

    def format(self, value: Any) -> str:
        if self.fmt:
            return self.fmt(value)
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return yaml.safe_dump(value, default_flow_style=True, width=10_000).removesuffix("\n...\n").strip()


MODELS = ("ssd1306", "ssd1305", "ssd1309", "ssd1315", "sh1106")

SCHEMA: tuple[Setting, ...] = (
    Setting("display.model", "ssd1306", p_choice(*MODELS), " | ".join(MODELS), ("--display",)),
    Setting("display.i2c_bus", AUTO, p_auto_or(p_int(0)), "auto | bus number N (/dev/i2c-N)", ("--i2c-port",)),
    Setting("display.i2c_address", AUTO, p_auto_or(p_address), "auto | 0x3c | 0x3d", ("--i2c-address",), _fmt_address),
    Setting("display.width", 128, p_int(8, 256), "pixels", ("--width",)),
    Setting("display.height", 64, p_int(8, 128), "pixels; 64 or 32", ("--height",)),
    Setting("display.rotate", 2, p_int(0, 3), "0-3, quarter turns (luma semantics)", ("--rotate",)),
    Setting("display.contrast", 255, p_int(0, 255), "0-255"),
    Setting("cycle.refresh", 10.0, p_float(1), "seconds per page (the logo page uses logo_seconds)"),
    Setting(
        "cycle.pages",
        ["status", "kubernetes", "logo"],
        p_pages,
        "status kubernetes logo, big-font load temp fan; inapplicable ones skipped",
    ),
    Setting("cycle.logo", "armbian", p_str, "armbian | none | /path/to/1bit.png"),
    Setting("cycle.logo_seconds", AUTO, p_auto_or(p_float(0)), "seconds on the logo page; auto = refresh/2"),
    Setting("cycle.screen_off", 0.0, p_float(0), "seconds the panel stays off each cycle; 0 = never"),
    Setting("sensors.network", AUTO, p_str, "auto (default route) | interface name"),
    Setting("sensors.thermal", AUTO, p_str, 'auto | sensor name | "re:<regex>"'),
    Setting("sensors.fan", AUTO, p_str, 'auto (all hwmon fans) | none | "re:<regex>" on hwmon:fanN'),
    Setting("alerts.temp_max", 0.0, p_float(0), "C; above it the panel wakes and flashes a warning; 0 = off"),
    Setting("alerts.fan_min", 0, p_int(0), "rpm; any fan below it triggers the warning; 0 = off"),
    Setting("alerts.flash", True, p_bool, "invert the warning page every 0.5s (false: steady)"),
    Setting("storage.auto", True, p_bool, "fill free lines with ranked auto-detected mounts"),
    Setting("storage.mounts", [], p_mounts, "explicit, shown first: [{label: md0, path: /mnt/md0}]"),
    Setting("host.root", "/", p_str, "host / as seen by sys-oled; containers use /host"),
    Setting("host.missing_display", "exit", p_choice("exit", "wait"), "exit (code 3) | wait (re-probe every 60s)"),
    Setting("kubernetes.enabled", AUTO, p_auto_or(p_bool), "auto (in-cluster service account present) | true | false"),
    Setting("kubernetes.prefix", "sys-oled", p_str, "node label/annotation prefix"),
    Setting("kubernetes.refresh", 60.0, p_float(5), "max age (s) of k8s page data; fetched only when the page shows"),
    Setting("kubernetes.cluster_pods", True, p_bool, "also count not-running pods cluster-wide"),
)
BY_KEY = {s.key: s for s in SCHEMA}
SECTIONS = tuple(dict.fromkeys(s.section for s in SCHEMA))

# Values from one source: key -> (raw value, location)
Layer = dict[str, tuple[Any, str]]


@dataclass
class Config:
    values: dict[str, Any]
    sources: dict[str, str]
    warnings: list[str] = field(default_factory=list)

    def __getattr__(self, section: str) -> SimpleNamespace:
        if section not in SECTIONS:
            raise AttributeError(section)
        prefix = section + "."
        return SimpleNamespace(**{k[len(prefix) :]: v for k, v in self.values.items() if k.startswith(prefix)})

    def get(self, key: str) -> Any:
        return self.values[key]

    def section_values(self, section: str) -> dict[str, Any]:
        return {k: v for k, v in self.values.items() if k.startswith(section + ".")}


# --- sources ---


def _construct(node: yaml.Node) -> Any:
    return yaml.SafeLoader("").construct_object(node, deep=True)


def read_yaml(path: Path) -> tuple[Layer, list[str]]:
    try:
        text = path.read_text()
    except OSError as e:
        raise ConfigError(f"{path}: {e.strerror}") from e
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f"{path}:{mark.line + 1}:{mark.column + 1}" if mark else str(path)
        raise ConfigError(f"{where}: invalid YAML: {getattr(e, 'problem', e)}") from e
    layer: Layer = {}
    warnings: list[str] = []
    if root is None or (isinstance(root, yaml.ScalarNode) and root.value == ""):
        return layer, warnings
    if not isinstance(root, yaml.MappingNode):
        raise ConfigError(f"{path}:{root.start_mark.line + 1}: top level must be a mapping of sections")
    for sec_node, body in root.value:
        where = f"{path}:{sec_node.start_mark.line + 1}:{sec_node.start_mark.column + 1}"
        if sec_node.value not in SECTIONS:
            warnings.append(f"{where}: unknown section '{sec_node.value}'")
            continue
        if isinstance(body, yaml.ScalarNode) and body.tag.endswith(":null"):
            continue
        if not isinstance(body, yaml.MappingNode):
            raise ConfigError(f"{where}: section '{sec_node.value}' must be a mapping")
        for key_node, val_node in body.value:
            key = f"{sec_node.value}.{key_node.value}"
            loc = f"{path}:{key_node.start_mark.line + 1}:{key_node.start_mark.column + 1}"
            if key not in BY_KEY:
                warnings.append(f"{loc}: unknown key '{key}'")
                continue
            layer[key] = (_construct(val_node), loc)
    return layer, warnings


def read_legacy_ini(path: Path) -> tuple[Layer, list[str]]:
    """Read the pre-2.0 INI config through the legacy alias table."""
    ini = configparser.ConfigParser()
    try:
        ini.read_string(path.read_text(), source=str(path))
    except (OSError, configparser.Error) as e:
        raise ConfigError(f"{path}: {e}") from e
    layer: Layer = {}

    def take(section: str, option: str, key: str, convert: Callable[[str], Any] = str) -> None:
        if ini.has_option(section, option):
            layer[key] = (convert(ini.get(section, option).strip()), f"{path}:[{section}] {option}")

    take("main", "display_model", "display.model")
    take("main", "contrast", "display.contrast")
    take("main", "refresh", "cycle.refresh")
    take("main", "show_logo", "cycle.logo", lambda v: "armbian" if p_bool(v) else "none")
    take("device", "network_name", "sensors.network")
    mounts = []
    for n in range(1, 10):
        if ini.has_option("device", f"storage{n}_name") and ini.has_option("device", f"storage{n}_path"):
            mounts.append(
                {
                    "label": ini.get("device", f"storage{n}_name").strip(),
                    "path": ini.get("device", f"storage{n}_path").strip(),
                }
            )
    if mounts:
        layer["storage.mounts"] = (mounts, f"{path}:[device] storageN_*")
    return layer, [f"{path}: legacy INI config is deprecated; run 'sys-oled config --migrate'"]


def file_layers(paths: Iterable[Path] | None = None) -> tuple[list[Layer], list[str]]:
    layers: list[Layer] = []
    warnings: list[str] = []
    if paths is None:
        paths = [MAIN_FILE] if MAIN_FILE.exists() else []
        if not paths and LEGACY_FILE.exists():
            layer, warns = read_legacy_ini(LEGACY_FILE)
            layers.append(layer)
            warnings += warns
        if DROPIN_DIR.is_dir():
            paths += sorted(DROPIN_DIR.glob("*.yaml"))
    for path in paths:
        layer, warns = read_legacy_ini(path) if path.suffix == ".conf" else read_yaml(path)
        layers.append(layer)
        warnings += warns
    return layers, warnings


def env_layer(environ: Mapping[str, str]) -> Layer:
    return {s.key: (environ[s.env], f"env:{s.env}") for s in SCHEMA if s.env in environ}


def node_layer(metadata: Mapping[str, str] | None, prefix: str, kind: str) -> tuple[Layer, list[str]]:
    layer: Layer = {}
    warnings: list[str] = []
    for full, value in (metadata or {}).items():
        if not full.startswith(prefix + "/"):
            continue
        key = full[len(prefix) + 1 :]
        if key in RESERVED_NODE_KEYS:
            continue
        if key not in BY_KEY:
            warnings.append(f"node-{kind}:{full}: unknown key '{key}'")
            continue
        layer[key] = (value, f"node-{kind}:{full}")
    return layer, warnings


def resolve(strict: list[Layer], lenient: list[Layer] = (), warnings: list[str] | None = None) -> Config:
    """Apply layers in order. Errors in strict layers raise; errors in lenient (node) layers only warn."""
    values = {s.key: s.parse(s.default) for s in SCHEMA}
    sources = dict.fromkeys(values, "default")
    warnings = list(warnings or [])
    for layers, is_strict in ((strict, True), (lenient, False)):
        for layer in layers:
            for key, (raw, loc) in layer.items():
                try:
                    values[key] = BY_KEY[key].parse(raw)
                    sources[key] = loc
                except ValueError as e:
                    if is_strict:
                        raise ConfigError(f"{loc}: {key}: {e}") from e
                    warnings.append(f"{loc}: {key}: {e} (ignored)")
    return Config(values, sources, warnings)


class ConfigLoader:
    """Holds the static layers (files, env, flags) and rebuilds when node metadata changes."""

    def __init__(
        self, paths: Iterable[Path] | None = None, environ: Mapping[str, str] | None = None, cli: Layer | None = None
    ):
        self.file_layers, self.file_warnings = file_layers(paths)
        self.env = env_layer(os.environ if environ is None else environ)
        self.cli = cli or {}
        self.labels: dict[str, str] = {}
        self.annotations: dict[str, str] = {}
        # Validate the static layers right away so bad files fail at startup.
        self.base = resolve([*self.file_layers, self.env, self.cli], warnings=self.file_warnings)

    def build(self) -> Config:
        prefix = self.base.get("kubernetes.prefix")
        labels, w1 = node_layer(self.labels, prefix, "label")
        annotations, w2 = node_layer(self.annotations, prefix, "annotation")
        # Node layers sit between env and CLI flags; CLI flags are re-applied on top.
        cfg = resolve([*self.file_layers, self.env], [labels, annotations], warnings=[*self.file_warnings, *w1, *w2])
        for key, (raw, loc) in self.cli.items():
            cfg.values[key] = BY_KEY[key].parse(raw)
            cfg.sources[key] = loc
        return cfg

    def set_node_metadata(self, labels: Mapping[str, str] | None, annotations: Mapping[str, str] | None) -> bool:
        """Returns True if anything under our prefix changed."""
        prefix = self.base.get("kubernetes.prefix") + "/"

        def ours(d: Mapping[str, str] | None) -> dict[str, str]:
            return {k: v for k, v in (d or {}).items() if k.startswith(prefix)}

        new_labels, new_annotations = ours(labels), ours(annotations)
        changed = new_labels != self.labels or new_annotations != self.annotations
        self.labels, self.annotations = new_labels, new_annotations
        return changed


# --- rendering ---

HEADER = """\
# sys-oled configuration (YAML).
# Every key is optional; commented values are the built-in defaults.
# Precedence (lowest to highest): defaults < /etc/sys-oled.yaml < /etc/sys-oled.d/*.yaml
#   < env SYS_OLED_<SECTION>_<KEY> < k8s node labels < k8s node annotations < CLI flags.
# 'sys-oled config' shows the effective configuration and where each value came from.
"""


def render(
    values: Mapping[str, Any] | None = None,
    comments: Mapping[str, str] | None = None,
    active: Iterable[str] = (),
    header: str = HEADER,
) -> str:
    """Render YAML: keys in `active` are written as values, everything else commented out at its default."""
    values = values or {}
    comments = comments or {}
    active = set(active)
    lines = [header] if header else []
    for section in SECTIONS:
        lines.append(f"{section}:")
        for s in (s for s in SCHEMA if s.section == section):
            on = s.key in active
            body = f"{s.name}: {s.format(values.get(s.key, s.default) if on else s.default)}"
            prefix = "  " if on else "  # "
            lines.append(f"{prefix}{body:<30} # {comments.get(s.key, s.help)}".rstrip())
    return "\n".join(lines) + "\n"


def render_effective(cfg: Config) -> str:
    return render(cfg.values, cfg.sources, active=cfg.values, header="")


def template() -> str:
    return render()
