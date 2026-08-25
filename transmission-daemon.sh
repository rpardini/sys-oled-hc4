#!/bin/bash
set -e
MAIN_DIR="$(dirname "$BASH_SOURCE")"
cd "$MAIN_DIR"
SCRIPTDIR="$(pwd -P)"
cd - >/dev/null

source "$SCRIPTDIR"/env.sh

if [ "$PORTOPT" != "" ]; then
    echo "Starting transmission-daemon with a port forward: $PORTOPT"
else
    echo "Starting transmission-daemon without a port forward"
fi

transmission-daemon $PORTOPT -a 172.16.1.*,172.16.2.*,172.16.3.* -c "$DOWNLOADS_DIR"/transmission-torrents -w "$DOWNLOADS_DIR"
