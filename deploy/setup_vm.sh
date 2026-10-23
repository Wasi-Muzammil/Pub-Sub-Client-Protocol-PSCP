#!/usr/bin/env bash
# Install / update the PSCP broker on an Ubuntu cloud VM.
# Run from the repo root on the VM:  sudo bash deploy/setup_vm.sh
set -euo pipefail

apt-get update -qq
apt-get install -y -qq python3 tcpdump

id pscp >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin pscp
mkdir -p /opt/pscp
rm -rf /opt/pscp/pscp
cp -r pscp /opt/pscp/
chown -R pscp:pscp /opt/pscp

install -m 644 deploy/pscp-broker.service /etc/systemd/system/pscp-broker.service
systemctl daemon-reload
systemctl enable --now pscp-broker
systemctl restart pscp-broker

# Host firewall (the cloud security group must ALSO allow TCP 5050 inbound)
if command -v ufw >/dev/null && ufw status | grep -q active; then
    ufw allow 5050/tcp
fi

systemctl --no-pager status pscp-broker | head -n 5
echo "Private address(es) for the report:"; ip -4 addr show scope global | grep inet
