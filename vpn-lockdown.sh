#!/bin/bash
# =============================================================================
# Preamble
# =============================================================================
set -e
MAIN_DIR="$(dirname "$BASH_SOURCE")"
cd "$MAIN_DIR"
SCRIPTDIR="$(pwd -P)"

source "$SCRIPTDIR"/env.sh

usage()
{
  echo "vpn-lockdown.sh ipaddr"
  echo "  ipaddr	: Is the ip address of the OpenVPN server you're connected to."
  echo "		  This should be printed out from ./proxpn.sh."
  exit 1
}

if [ $# -ne 1 ]; then
    usage
fi

SERVERIP=$1

sudo iptables-legacy -A INPUT -i lo -j ACCEPT

sudo iptables-legacy -A OUTPUT -o lo -j ACCEPT #allow loopback access

#make sure you can communicate with any DHCP server
sudo iptables-legacy -A OUTPUT -d 255.255.255.255 -j ACCEPT 
sudo iptables-legacy -A INPUT -s 255.255.255.255 -j ACCEPT

#make sure that you can communicate within your own network
if [ "$ALL_SUBNETS_NETMASK" = "" ]; then
    echo "You must set ALL_SUBNETS_NETMASK in env.sh. e.g.: export ALL_SUBNETS_NETMASK=172.16.0.0/16"
    exit 1
fi
sudo iptables-legacy -A INPUT  -s $ALL_SUBNETS_NETMASK -d $ALL_SUBNETS_NETMASK -j ACCEPT
sudo iptables-legacy -A OUTPUT -s $ALL_SUBNETS_NETMASK -d $ALL_SUBNETS_NETMASK -j ACCEPT

if [ "$SUBNET_NETMASK" = "" ]; then
    echo "You must set SUBNET_NETMASK in env.sh. e.g.: export SUBNET_NETMASK=172.16.2.0/24"
    exit 1
fi
INTERFACE="$(ip -o -4 addr show to $SUBNET_NETMASK | awk '{print $2; exit}')"
if [ -z "$INTERFACE" ]; then
    echo "ERROR: no interface bound to $SUBNET_NETMASK"
    exit 1
fi
sudo iptables-legacy -A FORWARD -i $INTERFACE -o tun+ -j ACCEPT
sudo iptables-legacy -A FORWARD -i tun+ -o $INTERFACE -j ACCEPT # make sure that $INTERFACE and tun+ can communicate
sudo iptables-legacy -t nat -A POSTROUTING -o tun+ -j MASQUERADE # in the POSTROUTING chain of the NAT table, map the tun+ interface outgoing packet IP address, cease examining rules and let the header be modified, so that we don't have to worry about ports or any other issue - please check this rule with care if you have already a NAT table in your chain

# if destination for outgoing packet on $INTERFACE is NOT a.b.c.d, drop the packet, so that nothing leaks if VPN disconnects
sudo iptables-legacy -A OUTPUT -o $INTERFACE ! -d $SERVERIP -j DROP

#sudo iptables-legacy -A OUTPUT -d $MYSERVERIP -j LOG --log-prefix "OUTBOUND TO MYSERVER: "

