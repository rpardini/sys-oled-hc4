#!/bin/bash

set -e 

usage()
{
  echo "proxpn-lockdown.sh ipaddr"
  echo "  ipaddr	: Is the ip address of the OpenVPN server you're connected to."
  echo "		  This should be printed out from ./proxpn.sh."
  exit 1
}

if [ $# -ne 1 ]; then
    usage
fi

SERVERIP=$1

#MY_SERVER=jiminger.com
#GATEWAY_SUBNET=172.16.2
#GATEWAY_IP=$GATEWAY_SUBNET.1
#
## Resolve the IP address of myserver.com
#MYSERVERIP=$(dig +short $MY_SERVER)
#INTERFACE="$(ip addr | grep "$GATEWAY_SUBNET." | sed -e "s/^.* //1")"
# had to change this when enx changed to eth. Hopefully the above is more robust
#  see also teardown-vpn.sh
#INTERFACE="$(ip link show | grep enx | sed -e "s/^[0-9]: //1" | sed -e "s/: <.*$//1")"
#echo "Wired network interface: $INTERFACE"
#
## Ensure it's not empty
#if [ -z "$MYSERVERIP" ]; then
#    echo "Failed to resolve the IP address for myserver.com"
#    exit 1
#fi
#
#echo "Circumventing VPN for IP addr updating here: $MYSERVERIP"
#sudo ip route add $MYSERVERIP via $GATEWAY_IP dev $INTERFACE
#
## Route traffic to myserver.com directly through the Ethernet interface
#sudo iptables-legacy -A OUTPUT -o $INTERFACE -d $MYSERVERIP -j ACCEPT
#
## Explicitly drop any such traffic on the VPN interface
#sudo iptables-legacy -A OUTPUT -o tun0 -d $MYSERVERIP -j DROP

sudo iptables-legacy -A INPUT -i lo -j ACCEPT
sudo iptables-legacy -A OUTPUT -o lo -j ACCEPT #allow loopback access

#make sure you can communicate with any DHCP server
sudo iptables-legacy -A OUTPUT -d 255.255.255.255 -j ACCEPT 
sudo iptables-legacy -A INPUT -s 255.255.255.255 -j ACCEPT

#make sure that you can communicate within your own network
sudo iptables-legacy -A INPUT -s 172.16.1.0/16 -d 172.16.1.0/16 -j ACCEPT
sudo iptables-legacy -A OUTPUT -s 172.16.1.0/16 -d 172.16.1.0/16 -j ACCEPT
#make sure that you can communicate within your own network
sudo iptables-legacy -A INPUT -s 172.16.2.0/16 -d 172.16.2.0/16 -j ACCEPT 
sudo iptables-legacy -A OUTPUT -s 172.16.2.0/16 -d 172.16.2.0/16 -j ACCEPT

sudo iptables-legacy -A FORWARD -i eth+ -o tun+ -j ACCEPT
sudo iptables-legacy -A FORWARD -i tun+ -o eth+ -j ACCEPT # make sure that eth+ and tun+ can communicate
sudo iptables-legacy -t nat -A POSTROUTING -o tun+ -j MASQUERADE # in the POSTROUTING chain of the NAT table, map the tun+ interface outgoing packet IP address, cease examining rules and let the header be modified, so that we don't have to worry about ports or any other issue - please check this rule with care if you have already a NAT table in your chain

sudo iptables-legacy -A OUTPUT -o eth+ ! -d $SERVERIP -j DROP # if destination for outgoing packet on eth+ is NOT a.b.c.d, drop the packet, so that nothing leaks if VPN disconnects

#sudo iptables-legacy -A OUTPUT -d $MYSERVERIP -j LOG --log-prefix "OUTBOUND TO MYSERVER: "

