#!/bin/bash
# sys-oled installer: git clone/update + distro apt packages + systemd unit + migration of older installs.
# Usage: curl -fsSL https://raw.githubusercontent.com/rpardini/sys-oled-hc4/master/install.sh | sudo bash
#        ... | sudo bash -s -- --uninstall   # remove service, launcher and checkout; keep config + i2c-dev at boot
#        ... | sudo bash -s -- --purge       # also remove config, i2c-dev boot loading and pre-2.0 leftovers
# Env: SYS_OLED_DIR, SYS_OLED_REPO, SYS_OLED_REF, SYS_OLED_FORCE=1 (install even without a detected display)
set -euo pipefail

MODE=install
for arg in "$@"; do
	case "${arg}" in
		--uninstall) MODE=uninstall ;;
		--purge) MODE=purge ;;
		*)
			echo "usage: install.sh [--uninstall|--purge]" >&2
			exit 2
			;;
	esac
done

SYS_OLED_DIR="${SYS_OLED_DIR:-/usr/src/sys-oled-hc4}"
SYS_OLED_REPO="${SYS_OLED_REPO:-https://github.com/rpardini/sys-oled-hc4.git}"
SYS_OLED_REF="${SYS_OLED_REF:-master}"
SYS_OLED_FORCE="${SYS_OLED_FORCE:-0}"

RUNTIME_DEPS=(git ca-certificates python3 python3-luma.oled python3-psutil python3-pil python3-smbus2 python3-yaml)
UNIT=/etc/systemd/system/sys-oled.service
BIN=/usr/local/bin/sys-oled

log() { echo "sys-oled: $*" >&2; }
warn() { echo "sys-oled: WARNING: $*" >&2; }
die() {
	echo "sys-oled: ERROR: $*" >&2
	exit 1
}

[[ "$(id -u)" == "0" ]] || die "must run as root (try: curl ... | sudo bash)"

uninstall() {
	# Undo everything the installer did, so another deployment (compose, k8s) can take over the display.
	# Apt packages stay: they are shared distro libraries this script cannot know are unused.
	if [[ -d /run/systemd/system ]] && systemctl cat sys-oled.service &> /dev/null; then
		log "stopping and disabling sys-oled.service (blanks the display)"
		systemctl disable --now sys-oled.service || true
	fi
	if [[ -f "${UNIT}" ]]; then
		log "removing ${UNIT}"
		rm -f "${UNIT}"
	fi
	if [[ -d /run/systemd/system ]]; then
		systemctl daemon-reload
		systemctl reset-failed sys-oled.service &> /dev/null || true
	fi
	if [[ -L "${BIN}" || -f "${BIN}" ]]; then
		log "removing ${BIN}"
		rm -f "${BIN}"
	fi
	if [[ -d "${SYS_OLED_DIR}/.git" && -x "${SYS_OLED_DIR}/bin/sys-oled" ]]; then
		log "removing checkout ${SYS_OLED_DIR}"
		rm -rf "${SYS_OLED_DIR}"
	elif [[ -e "${SYS_OLED_DIR}" ]]; then
		warn "${SYS_OLED_DIR} does not look like a sys-oled checkout; leaving it alone"
	fi
	local path
	if [[ "${MODE}" == "purge" ]]; then
		for path in /etc/sys-oled.yaml /etc/sys-oled.d /etc/sys-oled.conf /etc/sys-oled.conf.migrated \
			/etc/modules-load.d/sys-oled.conf "${UNIT}.d" /usr/local/share/sys-oled; do
			if [[ -e "${path}" ]]; then
				log "purging ${path}"
				rm -rf "${path}"
			fi
		done
	else
		local kept=()
		for path in /etc/sys-oled.yaml /etc/sys-oled.d /etc/modules-load.d/sys-oled.conf; do
			[[ -e "${path}" ]] && kept+=("${path}")
		done
		if [[ ${#kept[@]} -gt 0 ]]; then
			log "kept ${kept[*]} (reinstall picks the config up again; i2c-dev is needed by container/k8s deployments too); use --purge to remove"
		fi
	fi
	log "uninstalled; apt packages (${RUNTIME_DEPS[*]}) were left installed"
}

if [[ "${MODE}" != "install" ]]; then
	uninstall
	exit 0
fi

command -v apt-get > /dev/null || die "apt-get not found; only Debian/Ubuntu and derivatives are supported"

# shellcheck disable=SC1091
. /etc/os-release
log "detected ${PRETTY_NAME:-unknown OS}"

log "checking distro packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
candidate="$(apt-cache policy python3-luma.oled 2> /dev/null | awk '/Candidate:/ {print $2}')"
if [[ -z "${candidate}" || "${candidate}" == "(none)" ]]; then
	die "python3-luma.oled is not available from apt; Debian 13 (Trixie)+ or Ubuntu 25.04+ is required"
fi

log "installing runtime packages: ${RUNTIME_DEPS[*]}"
apt-get install -y -qq --no-install-recommends "${RUNTIME_DEPS[@]}" > /dev/null
python3 -c 'import sys; sys.exit(sys.version_info < (3, 13))' || die "python3 >= 3.13 is required, found $(python3 --version)"

if [[ -d "${SYS_OLED_DIR}/.git" ]]; then
	log "updating ${SYS_OLED_DIR} to ${SYS_OLED_REF}"
	git -C "${SYS_OLED_DIR}" remote set-url origin "${SYS_OLED_REPO}"
	git -C "${SYS_OLED_DIR}" fetch -q --tags --force origin
	if git -C "${SYS_OLED_DIR}" rev-parse -q --verify "origin/${SYS_OLED_REF}" > /dev/null; then
		git -C "${SYS_OLED_DIR}" checkout -q --force -B "${SYS_OLED_REF}" "origin/${SYS_OLED_REF}"
	else # tag or commit
		git -C "${SYS_OLED_DIR}" checkout -q --force --detach "${SYS_OLED_REF}"
	fi
	git -C "${SYS_OLED_DIR}" clean -q -fdx # also drops old in-place sed edits / build leftovers
else
	log "cloning ${SYS_OLED_REPO} (${SYS_OLED_REF}) into ${SYS_OLED_DIR}"
	rm -rf "${SYS_OLED_DIR}"
	git clone -q "${SYS_OLED_REPO}" "${SYS_OLED_DIR}"
	git -C "${SYS_OLED_DIR}" checkout -q --force "${SYS_OLED_REF}"
fi
SYS_OLED="${SYS_OLED_DIR}/bin/sys-oled"
log "version $(git -C "${SYS_OLED_DIR}" describe --always --tags --dirty)"

log "loading i2c-dev"
mkdir -p /etc/modules-load.d
echo "i2c-dev" > /etc/modules-load.d/sys-oled.conf
modprobe i2c-dev 2> /dev/null || warn "could not load i2c-dev now (container?); it will load at boot"

# --- migrations from older installs (all idempotent) ---

if [[ -e "${BIN}" && ! -L "${BIN}" ]]; then
	log "migrating: replacing old copied ${BIN} with a symlink"
fi
ln -sfn "${SYS_OLED}" "${BIN}"

if [[ -d /usr/local/share/sys-oled ]]; then
	log "migrating: removing /usr/local/share/sys-oled (assets now ship inside the package)"
	rm -rf /usr/local/share/sys-oled
fi

mkdir -p /etc/sys-oled.d
if [[ ! -f /etc/sys-oled.yaml ]]; then
	if [[ -f /etc/sys-oled.conf ]]; then
		log "migrating: /etc/sys-oled.conf (INI) -> /etc/sys-oled.yaml"
		"${SYS_OLED}" config --migrate --from /etc/sys-oled.conf --to /etc/sys-oled.yaml
		mv /etc/sys-oled.conf /etc/sys-oled.conf.migrated
	else
		log "installing default /etc/sys-oled.yaml"
		install -m 0644 "${SYS_OLED_DIR}/etc/sys-oled.yaml" /etc/sys-oled.yaml
	fi
else
	log "keeping existing /etc/sys-oled.yaml"
fi

if [[ -f "${UNIT}" ]] && grep -q '^ExecStart=.*--' "${UNIT}"; then
	# Pre-2.0 units carried luma flags. Keep model/rotation; drop --i2c-port (install-time detection that goes
	# stale across kernels; the bus is auto-detected at every start now).
	exec_start="$(grep -m1 '^ExecStart=' "${UNIT}" || true)"
	model="$(sed -n 's/.*--display[ =]\([A-Za-z0-9_]*\).*/\1/p' <<< "${exec_start}")"
	rotate="$(sed -n 's/.*--rotate[ =]\([0-9]\).*/\1/p' <<< "${exec_start}")"
	migrated=""
	[[ -n "${model}" && "${model}" != "ssd1306" ]] && migrated+="  model: ${model}"$'\n'
	[[ -n "${rotate}" && "${rotate}" != "2" ]] && migrated+="  rotate: ${rotate}"$'\n'
	if [[ -n "${migrated}" ]]; then
		log "migrating: old unit flags -> /etc/sys-oled.d/50-migrated-unit.yaml"
		printf '# migrated from the pre-2.0 systemd unit: %s\ndisplay:\n%s' "${exec_start}" "${migrated}" > /etc/sys-oled.d/50-migrated-unit.yaml
	fi
fi
if [[ -d "${UNIT}.d" ]]; then
	warn "${UNIT}.d/ exists; options belong in /etc/sys-oled.yaml or /etc/sys-oled.d/ now"
fi
install -m 0644 "${SYS_OLED_DIR}/system/sys-oled.service" "${UNIT}"

if compgen -G "/usr/local/lib/python3*/dist-packages/luma*" > /dev/null; then
	warn "pip-installed luma packages found under /usr/local/lib/python3*/dist-packages; they may shadow the apt ones"
fi
if command -v docker > /dev/null && docker ps --format '{{.Image}}' 2> /dev/null | grep -q sys-oled; then
	warn "a sys-oled container is running; it and the systemd service would fight over the same display"
fi

# --- detection and service ---

detected=1
"${SYS_OLED}" detect || detected=0
has_systemd=0
[[ -d /run/systemd/system ]] && has_systemd=1
if [[ "${has_systemd}" == "1" ]]; then
	systemctl daemon-reload
fi
if [[ "${detected}" == "0" && "${SYS_OLED_FORCE}" != "1" ]]; then
	die "no display detected; service not enabled (set SYS_OLED_FORCE=1 to enable anyway)"
fi
if [[ "${has_systemd}" == "1" ]]; then
	systemctl enable sys-oled.service
	systemctl restart sys-oled.service
	sleep 1
	systemctl --no-pager --lines=5 status sys-oled.service || true
else
	warn "systemd is not running; service installed but not started"
fi
log "done"
