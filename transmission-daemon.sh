#!/bin/bash
set -e
MAIN_DIR="$(dirname "$BASH_SOURCE")"
cd "$MAIN_DIR"
SCRIPTDIR="$(pwd -P)"
cd - >/dev/null

cidr_to_wildcard() {   # "172.16.0.0/16" -> "172.16.*.*"   (prefix must be 0/8/16/24/32)
    local ip=${1%/*} p=${1#*/} out= i
    local -a o
    IFS=. read -r -a o <<< "$ip"
    if (( p % 8 != 0 )); then
        echo "ERROR: $1: transmission -a wildcards can't express /$p (only /8 /16 /24 /32)" >&2
        return 1
    fi
    for i in 0 1 2 3; do
        (( i < p/8 )) && out+="${o[i]}." || out+="*."
    done
    echo "${out%.}"
}

source "$SCRIPTDIR"/env.sh

if [ "$PORTOPT" != "" ]; then
    echo "Starting transmission-daemon with a port forward: $PORTOPT"
else
    echo "Starting transmission-daemon without a port forward"
fi

if [ "$ALL_SUBNETS_NETMASK" = "" ]; then
    echo "You must set ALL_SUBNETS_NETMASK in env.sh. e.g.: export ALL_SUBNETS_NETMASK=172.16.0.0/16"
    exit 1
fi
WL="$(cidr_to_wildcard "$ALL_SUBNETS_NETMASK")"

transmission-daemon $PORTOPT -a "$WL" -c "$DOWNLOADS_DIR"/transmission-torrents -w "$DOWNLOADS_DIR"
