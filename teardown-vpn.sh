#!/bin/bash

sudo pkill transmission

sudo pkill openvpn

sudo iptables-legacy -F
sudo iptables-legacy -t nat -F
sudo iptables-legacy -X
sudo iptables-legacy -t nat -X
sudo iptables-legacy -Z
sudo iptables-legacy -t nat -Z

# -------------------------------------------
# This undoes the SSH routing.
# Remove ip rule
sudo ip rule del fwmark 1 table ssh_table

#   Determin the appropriate interface
GATEWAY_SUBNET=172.16.2
#   Resolve the network interface bound to the gateway
INTERFACE="$(ip -o -4 addr show | grep "$GATEWAY_SUBNET" | awk '{print $2}')"

# Remove custom route
sudo ip route del default via 172.16.2.1 dev $INTERFACE table ssh_table

# Remove ssh_table entry from rt_tables
sudo sed -i '/100 ssh_table/d' /etc/iproute2/rt_tables
# -------------------------------------------

echo "Grepping process list for transmission:"
ps -ef | grep transmission | grep -v grep

echo ""
echo "Grepping process list for openvpn:"
ps -ef | grep openvpn | grep -v grep

echo ""
echo "Showing IPTables:"
sudo iptables-legacy -L

#GATEWAY_SUBNET=172.16.2
#GATEWAY_IP=$GATEWAY_SUBNET.1
#MY_SERVER=jiminger.com
#MYSERVERIP=$(dig +short $MY_SERVER)
#INTERFACE="$(ip addr | grep "$GATEWAY_SUBNET." | sed -e "s/^.* //1")"
#
#sudo ip route del $MYSERVERIP via $GATEWAY_IP dev $INTERFACE
