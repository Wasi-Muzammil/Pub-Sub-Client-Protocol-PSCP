#!/usr/bin/env bash
# Capture PSCP traffic on the broker VM for Wireshark (R5).
#   sudo bash deploy/capture.sh [seconds]      -> results/captures/broker-<time>.pcapng
# Copy it to a laptop:  scp <user>@<vm-ip>:<repo>/results/captures/*.pcapng .
set -euo pipefail
secs=${1:-60}
mkdir -p results/captures
out="results/captures/broker-$(date +%Y%m%d-%H%M%S).pcapng"
echo "capturing tcp port 5050 for ${secs}s -> $out"
timeout "$secs" tcpdump -i any -s 0 -w "$out" 'tcp port 5050' || true
echo "done: $out"
