# AGENTS.md

System status on SSD1306-class I2C OLEDs (SH1106/SSD1305/SSD1309/SSD1315 too) for any Linux board with `/dev/i2c-*`.
The repo name `sys-oled-hc4` is historical: keep it, including in the image and chart paths. The program, unit and
config are all named `sys-oled`. The README is the user-facing reference.

## Hard rules

- **The runtime uses distro apt packages only:** `python3-luma.oled python3-psutil python3-pil python3-smbus2 python3-yaml`.
  No pip or venv on targets, and no new runtime dependency unless it's packaged in Debian Trixie **and** Ubuntu 25.04.
  The k8s client is stdlib `urllib` on purpose.
- **Floors** are Python 3.13 and Trixie's package versions (luma.oled 3.10, luma.core 2.4.2, psutil 5.9.8 from Ubuntu
  25.04, pillow 11.1, smbus2 0.4.3, pyyaml 6.0.2). Don't use APIs newer than those.
- **Detect once, then keep each cycle cheap.** Re-detect only on failure, or on kernel change events (mountinfo `POLLPRI`,
  netlink). Never add periodic rescans or per-cycle logging. `tests/test_app.py` asserts zero steady-state detections.
  The only periodic extra work is the alert check while waiting (temp + fan reads every 5s), and only when alerts are on.
- **One config schema:** `SCHEMA` in `src/sys_oled/config.py` generates YAML validation, CLI flags, env names
  (`SYS_OLED_<SECTION>_<KEY>`), k8s node keys (`sys-oled/<section>.<key>`) and the template. After changing it, run
  `uv run sys-oled config --defaults > etc/sys-oled.yaml` and update the template block in the README; a test checks the
  shipped file. Keep legacy aliases (old INI keys, luma flags `--display/--i2c-port/--rotate/...`) working.
- **The systemd unit carries no options.** Settings go in `/etc/sys-oled.yaml` and `/etc/sys-oled.d/*.yaml`.
- **Installs must keep migrating:** pre-2.0 installs, including those made by the oldskool `armbian_ssd1306.sh`, which
  had `--i2c-port` hardcoded and `sed`-patched the script. `install.sh` must stay idempotent, shellcheck-clean, and free
  of distro codename lists (it gates on apt package availability).

## Layout

- `src/sys_oled/`:
  - `cli.py`: subcommands run/detect/render/config.
  - `app.py`: main loop (`cycle.pages`, screen-off), alerts and recovery.
  - `config.py`
  - `detect.py`: I2C probe, default route.
  - `thermal.py`: tiered sensor heuristic.
  - `fan.py`: hwmon fan tachometers.
  - `storage.py`: mountinfo ranking.
  - `watch.py`: kernel events.
  - `stats.py`: per-cycle collector.
  - `screen.py`: pure PIL rendering (status, k8s, logo, big-font and alert pages).
  - `device.py`: luma wrapper with frame diffing.
  - `kube.py`: node watch and the lazy k8s page.
- `bin/sys-oled`: launcher used by git-clone deployments (`#!/usr/bin/python3`, adds `../src` to `sys.path`).
- `install.sh`: `curl | sudo bash` installer; also takes `--uninstall` and `--purge`.
- `assets-src/logo-armbian.svg` → `scripts/build_assets.py` (dev-only, resvg) → 1-bit PNGs in `src/sys_oled/assets/`.
- `Dockerfile` (trixie-slim + the same apt packages), `deploy/compose.yaml`, and `charts/sys-oled/` (a DaemonSet on
  nodes labeled `sys-oled/enabled=true`; per-node config from node labels and annotations).
- `.github/workflows/`:
  - `ci.yml`: lint, tests (uv and distro containers), shellcheck, installer scenarios, helm lint and kubeconform.
  - `container.yml`: per-arch build by digest, then manifest merge, then the chart pushed to
    `oci://ghcr.io/rpardini/sys-oled-hc4/charts`.

## Commands

```
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest -q
uv run scripts/build_assets.py --check          # logo bitmaps up to date
uv run sys-oled -c etc/sys-oled.yaml render --sample --page status|kubernetes|logo|load|temp|fan|alert --png /tmp/x.png --scale 4
scripts/test-installer.sh [image...]            # docker: fresh install, pre-2.0 migration, uninstall/purge
helm lint charts/sys-oled
```

To test against the real distro packages, as CI does:
`docker run --rm -v "$PWD:/src:ro" debian:trixie bash -c 'apt-get update -qq && apt-get install -y -qq python3-luma.oled python3-psutil python3-pil python3-smbus2 python3-yaml python3-pytest >/dev/null && cp -r /src /w && cd /w && rm -rf .venv && python3 -m pytest -q -p no:cacheprovider tests'`

## Testing notes

- Tests never touch real hardware. `tests/conftest.py::SysTree` builds fake sysfs/procfs/dev trees. The I2C probe takes
  an `smbus_factory`, `App` takes `panel_factory`/`stop`/`now`, `tests/test_kube.py` runs a fake API server, and
  `StopAfter` drives the loop without sleeping.
- `MountWatcher` is Linux-only (macOS reports `POLLPRI` on regular files). Linux-only paths still get exercised by the
  distro-container runs.
- After changing pages, regenerate the captures with `render --sample --scale 2` into `capture/`.

## CI gotchas

- `astral-sh/setup-uv` publishes no floating major tags, so pin a full version. The other actions use major tags.
- Image tags: branch, `sha-<7>`, semver and `latest`. Master pushes produce dev charts `0.0.0-main.<run>+<sha>` (install
  with `--devel`); `vX.Y.Z` tags produce releases.

## Releasing

1. Bump the version in **both** `pyproject.toml` and `src/sys_oled/__init__.py`, then run `uv lock`.
   `tests/test_screen.py::test_versions_in_sync` checks they match.
2. Commit, then make a signed tag: `git tag -s vX.Y.Z -m "sys-oled X.Y.Z" -m "- bullets"` (gpg has no passphrase).
   The user pushes.
3. The tag push makes `container.yml` publish image `X.Y.Z`/`X.Y` and chart `X.Y.Z`. Check them with
   `docker buildx imagetools inspect ghcr.io/rpardini/sys-oled-hc4:X.Y.Z` and
   `helm show chart oci://ghcr.io/rpardini/sys-oled-hc4/charts/sys-oled --version X.Y.Z`.
4. `gh release create vX.Y.Z --verify-tag --latest --notes-file ...`. Call out breaking changes, e.g. dropped distros.
5. Never rewrite history at or before a released tag. Follow-ups go in new commits.
