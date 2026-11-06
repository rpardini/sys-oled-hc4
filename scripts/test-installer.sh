#!/bin/bash
# Exercise install.sh in throwaway distro containers (no systemd, no I2C: detection fails, SYS_OLED_FORCE=1).
# Usage: scripts/test-installer.sh [image ...]   (default: debian:trixie ubuntu:25.04 ubuntu:26.04)
# Scenarios per image: fresh install, migration of a simulated pre-2.0 (oldskool) install, uninstall/purge cycle.
# Uses the working tree (committed or not) as the git repo the installer clones from.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGES=("$@")
[[ ${#IMAGES[@]} -gt 0 ]] || IMAGES=(debian:trixie ubuntu:25.04 ubuntu:26.04)

# shellcheck disable=SC2016 # expanded inside the container
SCENARIOS='
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq > /dev/null && apt-get install -y -qq git > /dev/null
cp -r /src /tmp/repo && cd /tmp/repo && rm -rf .venv .git
git init -q -b master && git add -A && git -c user.email=t@t -c user.name=t commit -qm test
cd /
fail() { echo "FAIL: $*"; exit 1; }
run() { SYS_OLED_REPO=/tmp/repo SYS_OLED_FORCE=1 bash /tmp/repo/install.sh "$@" > /tmp/log 2>&1 || { cat /tmp/log; fail "install.sh $*"; }; }

echo "-- fresh install"
run
[[ -L /usr/local/bin/sys-oled ]] || fail "no launcher symlink"
grep -q "^ExecStart=/usr/local/bin/sys-oled$" /etc/systemd/system/sys-oled.service || fail "unit"
cmp -s /etc/sys-oled.yaml /tmp/repo/etc/sys-oled.yaml || fail "default config"
sys-oled render --sample --png /tmp/x.png || fail "render"
run --purge

echo "-- migrate pre-2.0 install"
git clone -q /tmp/repo /usr/src/sys-oled-hc4
mkdir -p /usr/local/share/sys-oled /etc/systemd/system
echo "# old copied script" > /usr/local/bin/sys-oled
printf "[main]\ncontrast = 200\nshow_logo = no\nrefresh = 15\n\n[device]\nnetwork_name = eth0\nstorage1_name = sd\nstorage1_path = /\n" > /etc/sys-oled.conf
printf "[Service]\nExecStart=/usr/bin/python3 /usr/local/bin/sys-oled --display sh1106 --i2c-port 1 --rotate 0\n" > /etc/systemd/system/sys-oled.service
run
run # idempotent
[[ -f /etc/sys-oled.conf.migrated && ! -e /etc/sys-oled.conf ]] || fail "INI not migrated"
[[ ! -e /usr/local/share/sys-oled ]] || fail "old share dir kept"
cfg="$(sys-oled config)"
grep -q "contrast: 200 " <<< "$cfg" && grep -q "logo: none " <<< "$cfg" && grep -q "refresh: 15 " <<< "$cfg" || fail "INI values"
grep -q "model: sh1106 .*50-migrated-unit" <<< "$cfg" && grep -q "rotate: 0 .*50-migrated-unit" <<< "$cfg" || fail "unit flags"
grep -q "i2c_bus: auto" <<< "$cfg" || fail "--i2c-port must not be migrated"

echo "-- uninstall keeps config, purge removes everything"
run --uninstall
[[ ! -e /usr/local/bin/sys-oled && ! -e /usr/src/sys-oled-hc4 && ! -e /etc/systemd/system/sys-oled.service ]] || fail "uninstall leftovers"
[[ -f /etc/sys-oled.yaml && -f /etc/modules-load.d/sys-oled.conf ]] || fail "uninstall removed config"
run --purge
leftover="$(ls -d /etc/sys-oled* /etc/modules-load.d/sys-oled.conf /usr/local/bin/sys-oled /usr/src/sys-oled-hc4 2> /dev/null || true)"
[[ -z "$leftover" ]] || fail "purge leftovers: $leftover"
echo "OK"
'

for image in "${IMAGES[@]}"; do
	echo "=== ${image}"
	docker run --rm -i -v "${ROOT}:/src:ro" "${image}" bash -c "${SCENARIOS}"
done
