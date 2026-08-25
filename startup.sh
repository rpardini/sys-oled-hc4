#!/bin/bash
# =============================================================================
# Preamble
# =============================================================================
set -e
MAIN_DIR="$(dirname "$BASH_SOURCE")"
cd "$MAIN_DIR"
SCRIPTDIR="$(pwd -P)"

sgrep() {
    set +e
    grep "$@"
    set -e
}

segrep() {
    set +e
    egrep "$@"
    set -e
}

#Make sure we can sudo for a few minutes
sudo echo "Hello - there, you're sudoable now"

# check for transmission
DAEMON_RUNNING=`ps -ef | sgrep transmission-daemon | sgrep -v grep | wc -l`
if [ $DAEMON_RUNNING -ne 0 ]; then
    sudo service transmission-daemon stop
fi

# Sweden
# nohup $SCRIPTDIR/piavpn.sh -s 33 > /tmp/pia.out 2>&1 &
nohup $SCRIPTDIR/piavpn.sh -s 73 > /tmp/pia.out 2>&1 &
# Netherlands
# nohup $SCRIPTDIR/piavpn.sh -s 25 > /tmp/pia.out 2>&1 &

UP=keepgoing
COUNT=0
while [ "$UP" = "keepgoing" ]; do
    UP=`sgrep "Initialization Sequence Completed" /tmp/pia.out | wc -l`
    if [ $UP -ne 1 ]; then
        UP=keepgoing
        COUNT=`expr $COUNT + 1`
        if [ $COUNT -gt 10 ]; then
            UP=giveup
        else
            sleep 3
        fi
    fi
done

if [ $UP -eq 1 ]; then
    echo "VPN is up"
else
    echo "Timed out waiting for VPN to come up."
    exit 1
fi

IP=`sgrep "\[AF_INET\]" /tmp/pia.out | head -1 | sed -e "s/^.*\[AF_INET\]//1" | sed -e "s|:.*$||1"`

echo "VPN external IP address is: $IP"

# double check
#DC_IP="$(curl http://jiminger.com/whatsmyip.php)"
DC_IP="$(curl ifconfig.me)"

if [ "$DC_IP" != "$IP" ]; then
    echo "ERROR: The IP address from the VPN output ($IP) doesn't match the IP address when I check ($DC_IP)"
    exit 1
fi

echo "Setting up port forward"

PIAVPN_GATEWAY="$(ip route s t all | sgrep ' dev tun0' | sgrep '0.0.0.0' | awk '{ print $3 }')"
echo "The Vpn command gateway is: $PIAVPN_GATEWAY"

FULL_SIGNATURE="$(curl -k "https://$PIAVPN_GATEWAY:19999/getSignature?token=$(sudo PIA_USER=$(sed -n 1p piavpn-conf/login.conf) PIA_PASS=$(sed -n 2p piavpn-conf/login.conf) ./manual-connections/get_token.sh | sgrep PIA_TOKEN | sed -e 's/^PIA_TOKEN=//1')" | jq -c -M '.')"

# make sure the response was OK
if [ "OK" != "$(echo "$FULL_SIGNATURE" | jq -c -M -r '.status')" ]; then
    echo "Error. Invalid response attempting to get the signature for the port forward. Expected an OK status. The full response was:"
    echo "$FULL_SIGNATURE"
    echo ""
    echo "$FULL_SIGNATURE" | jq '.'
    exit 1
fi

PAYLOAD="$(echo "$FULL_SIGNATURE" | jq -c -M -r '.payload')"
SIGNATURE="$(echo "$FULL_SIGNATURE" | jq -c -M -r '.signature')"

# these cannot be null
if [ "$PAYLOAD" = "" -o "$SIGNATURE" = "" ]; then
    echo "Failed to parse the signature response. The full response was:"
    echo "$FULL_SIGNATURE"
    echo ""
    echo "$FULL_SIGNATURE" | jq '.'
    echo ""
    echo "However, this resulted in:"
    echo "   payload:   $PAYLOAD"
    echo "   signature: $SIGNATURE"
    echo "but both need to be set."
    exit 1
fi

# we need to know what port we've been assigned.
DECODED_PAYLOAD="$(echo "$PAYLOAD" | base64 -d | jq -c -M '.')"
FWD_PORT="$(echo "$DECODED_PAYLOAD" | jq -c -M -r '.port')"
if [ "$FWD_PORT" = "" ]; then
    echo "Failed to get the forwarded port by parsing the decoded payload"
    echo "The encoded payload was: \"$PAYLOAD\""
    echo "The decoded payload was: $DECODED_PAYLOAD"
    exit 1
fi

echo "The port being forwarded: $FWD_PORT"

echo "$FWD_PORT" > /tmp/pia-fwd-port.txt

SIGNATURE="$SIGNATURE" PAYLOAD="$PAYLOAD" PIAVPN_GATEWAY="$PIAVPN_GATEWAY" nohup ./run-bind.sh >/tmp/run-bind.out 2>&1 &

UP=keepgoing
COUNT=0
while [ "$UP" = "keepgoing" ]; do
    RUN_BIND_RESULTS="$(tail /tmp/run-bind.out)"
    if [ "$RUN_BIND_RESULTS" = "OK" ]; then
        echo "First Bind was successful."
        break
    elif [ "$RUN_BIND_RESULTS" = "FAILED" ]; then
        echo "Bind failed!"
        exit 1
    fi

    UP=keepgoing
    COUNT=`expr $COUNT + 1`
    if [ $COUNT -gt 10 ]; then
        UP=giveup
    else
        sleep 3
    fi
done

# -------------------------------------------
# Allow SSH to work around the VPN for external connections through the port forwarding.
# Use iptables to mark all outgoing traffic originating from port 22:
sudo iptables-legacy -t mangle -A OUTPUT -p tcp --sport 22 -j MARK --set-mark 1
# Set up a custom routing table to handle the marked traffic
# 1. Add a new routing table:
echo "100 ssh_table" | sudo tee -a /etc/iproute2/rt_tables

#   Determin the appropriate interface
GATEWAY_SUBNET=172.16.2
#   Resolve the network interface bound to the gateway
INTERFACE="$(ip -o -4 addr show | grep "$GATEWAY_SUBNET" | awk '{print $2}')"

if [ "$INTERFACE" = "" ]; then
    echo "ERROR: Couldn't determine the interface bound to $GATEWAY_SUBNET"
    exit 1
fi

# 2. Add a default route to send traffic through end0:
sudo ip route add default via 172.16.2.1 dev $INTERFACE table ssh_table

# 3. Tell the system to route packets marked by iptables using the custom routing table:
sudo ip rule add fwmark 1 table ssh_table
# -------------------------------------------

set +e
$SCRIPTDIR/vpn-lockdown.sh $IP
if [ $? -ne 0 ]; then
    echo "Setting up firewall to lock down vpn seems to have failed."
    exit 1
fi

export PORTOPT="-P $FWD_PORT"

$SCRIPTDIR/transmission-daemon.sh
