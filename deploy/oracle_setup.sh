#!/usr/bin/env bash
# Install Docker and start the PSCP broker on an Oracle Cloud Ubuntu VM.
# Run from the repo root on the VM:  sudo bash deploy/oracle_setup.sh
# The VCN security list must ALSO allow ingress TCP 5050 (see docs/DEPLOYMENT.md).
set -euo pipefail

if ! command -v docker >/dev/null; then
    apt-get update -qq
    apt-get install -y -qq ca-certificates curl
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    . /etc/os-release
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi
apt-get install -y -qq tcpdump iptables-persistent >/dev/null || true

# Oracle's Ubuntu images ship iptables rules that REJECT everything except SSH.
# Host networking means the broker is reached through INPUT, so open 5050 there (before the REJECT).
if ! iptables -C INPUT -p tcp -m state --state NEW --dport 5050 -j ACCEPT 2>/dev/null; then
    pos=$(iptables -L INPUT --line-numbers -n | awk '$2 == "REJECT" { print $1; exit }')
    if [ -n "$pos" ]; then
        iptables -I INPUT "$pos" -p tcp -m state --state NEW --dport 5050 -j ACCEPT
    else
        iptables -A INPUT -p tcp -m state --state NEW --dport 5050 -j ACCEPT
    fi
    netfilter-persistent save
fi

docker compose up -d --build
sleep 2
docker compose ps
docker compose logs --tail 5 broker
echo "Private address(es) for the report:"; ip -4 addr show scope global | grep inet
