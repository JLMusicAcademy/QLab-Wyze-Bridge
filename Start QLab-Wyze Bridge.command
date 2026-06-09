#!/usr/bin/env bash
# Double-click this file in Finder to start the bridge in the background.
cd "$(dirname "$0")"
./bridgectl start
echo
echo "You can close this window. The bridge keeps running in the background."
echo "To stop it later, double-click 'Stop QLab-Wyze Bridge.command'."
