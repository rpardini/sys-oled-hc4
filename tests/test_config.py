import textwrap

import pytest

from sys_oled import config as c
from sys_oled.cli import build_parser, cli_layer


def write(path, text):
    path.write_text(textwrap.dedent(text))
    return path


def loader(tmp_path, files=(), env=None, cli=None):
    return c.ConfigLoader([*files], environ=env or {}, cli=cli or {})


def test_defaults(tmp_path):
    cfg = loader(tmp_path).build()
    assert cfg.display.rotate == 2 and cfg.display.i2c_bus == "auto" and cfg.storage.mounts == []
    assert set(cfg.sources.values()) == {"default"}


def test_precedence_all_layers(tmp_path):
    main = write(
        tmp_path / "a.yaml",
        """
        display: {rotate: 1, contrast: 10, width: 64, height: 32, model: sh1106}
        cycle: {refresh: 5, screen_off: 1}
    """,
    )
    dropin = write(tmp_path / "b.yaml", "display:\n  contrast: 20\n")
    ld = loader(
        tmp_path,
        [main, dropin],
        env={"SYS_OLED_DISPLAY_WIDTH": "96", "SYS_OLED_CYCLE_REFRESH": "7"},
        cli={"cycle.screen_off": ("9", "flag:--cycle-screen-off")},
    )
    p = "sys-oled/"
    ld.set_node_metadata(
        {p + "display.height": "64", p + "display.model": "ssd1309", p + "cycle.refresh": "8", "other/x": "1"},
        {p + "display.model": "ssd1305", p + "cycle.screen_off": "100"},
    )
    cfg = ld.build()
    assert cfg.display.rotate == 1 and cfg.sources["display.rotate"].endswith("a.yaml:2:11")
    assert cfg.display.contrast == 20  # drop-in over main
    assert cfg.display.width == 96  # env over files
    assert cfg.cycle.refresh == 8  # label over env
    assert cfg.display.height == 64  # label
    assert cfg.display.model == "ssd1305"  # annotation over label
    assert cfg.cycle.screen_off == 9  # flag over everything
    assert cfg.sources["display.model"] == "node-annotation:sys-oled/display.model"


def test_lists_replace_and_yaml_values_from_env_and_annotations(tmp_path):
    f = write(tmp_path / "a.yaml", "storage:\n  mounts: [{label: a, path: /a}, {label: b, path: /b}]\n")
    ld = loader(tmp_path, [f], env={"SYS_OLED_STORAGE_MOUNTS": "[{label: md0, path: /mnt/md0}]"})
    assert ld.build().storage.mounts == [{"label": "md0", "path": "/mnt/md0"}]
    ld.set_node_metadata(
        {},
        {
            "sys-oled/storage.mounts": "- {label: z, path: /z}",
            "sys-oled/sensors.thermal": "re:^bigcore",
        },
    )
    cfg = ld.build()
    assert cfg.storage.mounts == [{"label": "z", "path": "/z"}]
    assert cfg.sensors.thermal == "re:^bigcore"


def test_errors_have_location(tmp_path):
    f = write(tmp_path / "bad.yaml", "display:\n  rotate: 7\n")
    with pytest.raises(c.ConfigError, match=r"bad.yaml:2:3: display.rotate: 7 out of range"):
        loader(tmp_path, [f])
    f = write(tmp_path / "broken.yaml", "display:\n  rotate: [\n")
    with pytest.raises(c.ConfigError, match=r"broken.yaml:3:1: invalid YAML"):
        loader(tmp_path, [f])
    with pytest.raises(c.ConfigError, match="env:SYS_OLED_DISPLAY_MODEL"):
        loader(tmp_path, env={"SYS_OLED_DISPLAY_MODEL": "crt"})


def test_unknown_keys_warn(tmp_path):
    f = write(tmp_path / "a.yaml", "display:\n  rotat: 1\nbogus: {}\n")
    cfg = loader(tmp_path, [f]).build()
    assert any("a.yaml:2:3: unknown key 'display.rotat'" in w for w in cfg.warnings)
    assert any("unknown section 'bogus'" in w for w in cfg.warnings)


def test_invalid_node_values_ignored(tmp_path):
    ld = loader(tmp_path)
    labels = {
        "sys-oled/display.rotate": "9",
        "sys-oled/enabled": "true",
        "sys-oled/nope.x": "1",
    }
    assert ld.set_node_metadata(labels, None)
    cfg = ld.build()
    assert cfg.display.rotate == 2
    assert any("out of range" in w and "ignored" in w for w in cfg.warnings)
    assert any("unknown key 'nope.x'" in w for w in cfg.warnings)
    assert not any("enabled" in w for w in cfg.warnings)
    assert not ld.set_node_metadata({**labels, "unrelated": "x"}, {})  # only our prefix counts


def test_address_and_bus_parsing(tmp_path):
    f = write(tmp_path / "a.yaml", "display: {i2c_address: 0x3d, i2c_bus: 3}\n")
    cfg = loader(tmp_path, [f], env={}).build()
    assert cfg.display.i2c_address == 0x3D and cfg.display.i2c_bus == 3
    assert "i2c_address: 0x3d" in c.render_effective(cfg)


def test_effective_dump_round_trips(tmp_path):
    text = "display: {rotate: 0, i2c_address: 0x3c}\nstorage: {mounts: [{label: x, path: /x}]}\n"
    f = write(tmp_path / "a.yaml", text)
    cfg = loader(tmp_path, [f]).build()
    dump = write(tmp_path / "dump.yaml", c.render_effective(cfg))
    assert loader(tmp_path, [dump]).build().values == cfg.values


def test_shipped_template_is_current():
    from pathlib import Path

    assert (Path(__file__).parent.parent / "etc" / "sys-oled.yaml").read_text() == c.template()
    assert (
        c.resolve([c.read_yaml(Path(__file__).parent.parent / "etc" / "sys-oled.yaml")[0]]).sources
        == c.resolve([]).sources
    )


LEGACY = """\
[main]
display_model = ssd1306
contrast = 200
show_logo = no
refresh = 15

[device]
network_name = eth0
storage1_name = sd
storage1_path = /
storage2_name= md0
storage2_path= /mnt/md0
"""


def test_legacy_ini_and_migrate_equivalent(tmp_path, capsys):
    from sys_oled.cli import main

    ini = write(tmp_path / "sys-oled.conf", LEGACY)
    legacy = loader(tmp_path, [ini]).build()
    assert legacy.cycle.logo == "none" and legacy.display.contrast == 200 and legacy.sensors.network == "eth0"
    assert legacy.storage.mounts == [{"label": "sd", "path": "/"}, {"label": "md0", "path": "/mnt/md0"}]
    assert any("deprecated" in w for w in legacy.warnings)
    out = tmp_path / "sys-oled.yaml"
    assert main(["config", "--migrate", "--from", str(ini), "--to", str(out)]) == 0
    assert loader(tmp_path, [out]).build().values == legacy.values
    assert main(["config", "--migrate", "--from", str(ini), "--to", str(out)]) == 1  # never overwrites


def test_legacy_luma_flags():
    ns = build_parser().parse_args(["--display", "sh1106", "--i2c-port", "1", "--rotate", "0"])
    layer = cli_layer(ns)
    cfg = c.resolve([layer])
    assert (cfg.display.model, cfg.display.i2c_bus, cfg.display.rotate) == ("sh1106", 1, 0)
    ns = build_parser().parse_args(["render", "--png", "x.png", "--display-rotate", "3"])
    assert c.resolve([cli_layer(ns)]).display.rotate == 3


def test_pages_and_logo_seconds(tmp_path):
    cfg = loader(
        tmp_path, env={"SYS_OLED_CYCLE_PAGES": "status, temp, logo", "SYS_OLED_CYCLE_LOGO_SECONDS": "3"}
    ).build()
    assert cfg.cycle.pages == ["status", "temp", "logo"] and cfg.cycle.logo_seconds == 3
    with pytest.raises(c.ConfigError, match="unknown page"):
        loader(tmp_path, env={"SYS_OLED_CYCLE_PAGES": "[status, disco]"})
    assert loader(tmp_path).build().cycle.logo_seconds == "auto"
