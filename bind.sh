#!/bin/bash
# =============================================================================
# Preamble
# =============================================================================
set -e
MAIN_DIR="$(dirname "$BASH_SOURCE")"
cd "$MAIN_DIR"
SCRIPTDIR="$(pwd -P)"
cd - >/dev/null

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

BIND_RESPONSE="$(curl -sGk --data-urlencode "payload=${PAYLOAD}" --data-urlencode "signature=${SIGNATURE}" https://$PIAVPN_GATEWAY:19999/bindPort | jq -c -M '.')"
if [ "OK" != "$(echo "$BIND_RESPONSE" | jq -c -M -r '.status')" ]; then
    echo "Bind response failed to return an OK status" >&2
    echo "The full bind response was: $BIND_RESPONSE" >&2
    exit 1
fi
