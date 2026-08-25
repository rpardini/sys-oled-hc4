#!/bin/bash
# =============================================================================
# Preamble
# =============================================================================
MAIN_DIR="$(dirname "$BASH_SOURCE")"
cd "$MAIN_DIR"
SCRIPTDIR="$(pwd -P)"

if [ "$SIGNATURE" = "" ]; then
    echo "Failed to supply the SIGNATURE" >&2
    exit 1
fi

if [ "$PAYLOAD" = "" ]; then
    echo "Failed to supply the PAYLOAD" >&2
    exit 1
fi

if [ "$PIAVPN_GATEWAY" = "" ]; then
    echo "Failed to supply the PIAVPN_GATEWAY" >&2
    exit 1
fi

while [ true ]; do
    SIGNATURE="$SIGNATURE" PAYLOAD="$PAYLOAD" PIAVPN_GATEWAY="$PIAVPN_GATEWAY" ./bind.sh

    if [ $? -ne 0 ]; then
        echo "FAILED"
    else
        echo "OK"
    fi

    # sleep for 10 minutes
    sleep 600
done
