#!/bin/bash
set -e

sgrep() {
    set +e
    grep "$@"
    set -e
}

spkill() {
    set +e
    sudo pkill "$@"
    set -e
}

spkill transmission

TMP="$(ps -ef | sgrep transmission | sgrep -v grep)"
if [ "$TMP" != "" ]; then
    echo "Waiting for transmission to shut down"
    while [ "$TMP" != "" ]; do
        printf "."
        sleep 1
        spkill transmission
        TMP="$(ps -ef | sgrep transmission | sgrep -v grep)"
    done
fi

spkill -f '/run-bind\.sh'      # anchor to the path; SIGTERM not SIGKILL
sleep 3
spkill -f '/bind\.sh'          # leading slash prevents matching run-bind.sh
sleep 1
spkill -f 'curl.*:19999/bindPort'   # tighter: include the port path

spkill openvpn

sudo iptables-legacy -F
sudo iptables-legacy -t nat -F
sudo iptables-legacy -X
sudo iptables-legacy -t nat -X
sudo iptables-legacy -Z
sudo iptables-legacy -t nat -Z

sudo iptables-legacy -t mangle -F
sudo iptables-legacy -t mangle -X
sudo iptables-legacy -t mangle -Z

# -------------------------------------------
# This undoes the SSH routing.
# Remove ip rule
sudo ip rule del fwmark 1 table 100 || true

# Remove custom route
sudo ip route flush table 100 2>/dev/null || true

# Remove ssh_table entry from rt_tables
sudo sed -i '/100 ssh_table/d' /etc/iproute2/rt_tables
# -------------------------------------------

echo ""
echo "Sgrepping process list for openvpn:"
ps -ef | sgrep openvpn | sgrep -v grep

echo ""
echo "Showing IPTables:"
sudo iptables-legacy -L

