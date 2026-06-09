#!/usr/bin/env bash
# Double-click this file in Finder to stop the background bridge.
cd "$(dirname "$0")"
./bridgectl stop
echo
echo "You can close this window."
