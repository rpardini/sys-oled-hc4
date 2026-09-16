#!/bin/bash
# =============================================================================
# Preamble
# =============================================================================
set -eo pipefail
MAIN_DIR="$(dirname "$BASH_SOURCE")"
cd "$MAIN_DIR"
SCRIPTDIR="$(pwd -P)"

cidr_to_mask() {   # "172.16.0.0/16" -> "172.16.0.0 255.255.0.0"
    local p=${1#*/} m
    m=$(( 0xFFFFFFFF << (32 - p) & 0xFFFFFFFF ))
    echo "${1%/*} $((m>>24&255)).$((m>>16&255)).$((m>>8&255)).$((m&255))"
}

source "$SCRIPTDIR"/env.sh

SCRIPTDIR=`dirname "$BASH_SOURCE"`
TMPFILE="/tmp/piavpn-tmp"
CONFDIR="$SCRIPTDIR/piavpn-conf"
SCRATCH="$SCRIPTDIR/piavpn-scratch"
CRTURL1="https://www.privateinternetaccess.com/openvpn/openvpn.zip"
CRTURL2="https://www.privateinternetaccess.com/openvpn/openvpn-strong.zip"
SERVERPAGE="https://www.privateinternetaccess.com/pages/network/"
UPDATESERVERS=

usage()
{
  echo "piavpn.sh [-u] [--help] [-s selection]"
  echo "  -u	: Dowload the CRT and network server selection before running the vpn."
  echo "  -us	: Dowload the strong CRT and network server selection before running the vpn."
  exit 1
}

makedir()
{
    if [ ! -d $1 ]; then
        mkdir $1
        if [ $? -ne 0 ]; then
            echo "Couldn't create the directory \"$1\""
            exit 1
        fi
    fi
}

SELECTION=
CRTURL=
while [ $# -gt 0 ]; do
    case $1 in
        "-u")
            UPDATESERVERS="true"
            CRTURL="$CRTURL1"
            shift
            ;;
        "-us")
            UPDATESERVERS="true"
            CRTURL="$CRTURL2"
            shift
            ;;
        "-s")
            shift
            SELECTION=$1
            shift
            ;;
        *)
            usage
            shift
            ;;
    esac
done

if [ "$UPDATESERVERS" != "" ]; then
    makedir $SCRATCH
    makedir $CONFDIR

    curl -s $CRTURL > $SCRATCH/crt.zip
    if [ $? -ne 0 ]; then
        echo "Couldn't get current server information."
        exit 1
    fi

    cd $SCRATCH

    unzip crt.zip 
    if [ $? -ne 0 ]; then
        echo "Couldn't open the source code file or extract the necessary files."
        exit 1
    fi

    if [ "`ls ca.*.crt 2>/dev/null`" = ""  ]; then
        echo "The zip file doesn't appear to contain the certificate 'ca.crt'"
        exit 1
    fi

    cd - > /dev/null

    mv $SCRATCH/crl.*.pem $CONFDIR
    mv $SCRATCH/ca.*.crt $CONFDIR
    mv $SCRATCH/*.ovpn $CONFDIR

    if [ ! -f $CONFDIR/login.conf ]; then
        echo "Config is set up. If you want to save off your username and password yoou need to add the file \"$CONFDIR/login.conf\" and it needs to contain:"
        echo "username"
        echo "password"
    fi
else
    if [ ! -d $CONFDIR ]; then
        echo "Missing configuration directory. Rerun the script with the -u option."
        usage
    fi
fi

if [ -d $SCRATCH ]; then
    rm -rf $SCRATCH
fi

cd $CONFDIR

SELECTIONS=`ls *.ovpn | cat | sed -e "s/ /XXX|XXX/g"`
SELINDEX=1
for selection in $SELECTIONS; do
    sel=`echo "$selection" | sed -e "s/XXX|XXX/ /g"`
    echo "$SELINDEX) $sel"
    SELINDEX=`expr $SELINDEX + 1`
done
if [ "$SELECTION" = "" ]; then
    read SERVER_SELECTION
else
    SERVER_SELECTION=$SELECTION
    echo "selecting \"$SERVER_SELECTION\""
fi

CONFFILE=`echo "$SELECTIONS" | head -$SERVER_SELECTION | tail -1 | sed -e "s/XXX|XXX/ /g"`

echo "Selected config:\"$CONFFILE\""

SERVER=`egrep -e "^remote " "$CONFFILE" | awk '{ print $2 }'`
echo "Server:$SERVER"

if [ -f login.conf ]; then
    # we edit the ovpn file if we need to
    HAS_LOGIN=`grep "auth-user-pass login.conf" "$CONFFILE"`
    if [ "$HAS_LOGIN" = "" ]; then
        echo "auth-user-pass login.conf" >> "$CONFFILE"
    fi
fi

SERVERIP=`nslookup $SERVER | grep -A1 $SERVER | grep Address | awk '{ print $2 }'`

echo "*************************************************************************"
echo "Use \"./vpn-lockdown.sh $SERVERIP\" to prevent leaks if the vpn drops."
echo "*************************************************************************"

#sudo openvpn --config "$CONFFILE"
#sudo openvpn --route 172.16.2.0 --route 182.16.3.0 --config "$CONFFILE"
#sudo openvpn --route 172.16.2.0 255.255.255.0 172.16.1.1 --config "$CONFFILE"
if [ "$ALL_SUBNETS_NETMASK" = "" ]; then
    echo "You must set ALL_SUBNETS_NETMASK in env.sh. e.g.: export ALL_SUBNETS_NETMASK=172.16.0.0/16"
    exit 1
fi
read -r RT_NET RT_MASK <<< "$(cidr_to_mask "$ALL_SUBNETS_NETMASK")"
sudo openvpn --route "$RT_NET" "$RT_MASK" net_gateway \
             --route $(dig +short jiminger.com) 255.255.255.255 net_gateway \
             --config "$CONFFILE"

