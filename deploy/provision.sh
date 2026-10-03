#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 host for docker-compose.prod.yml. Run as root:
#   ssh root@HOST 'bash -s' < deploy/provision.sh
# Safe to run again. Then put .env in /opt/serpsense and run scripts/deploy.sh
# (docs/operations.md#production).
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
APP_DIR=/opt/serpsense
BACKUP_DIR=/var/backups/serpsense

apt-get update -q
apt-get upgrade -yq
apt-get install -yq ca-certificates curl ufw fail2ban unattended-upgrades rsync

# Docker Engine and the Compose plugin from Docker's apt repository.
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc]" \
    "https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -yq docker-ce docker-ce-cli containerd.io docker-buildx-plugin \
    docker-compose-plugin
fi
systemctl enable --now docker

# 2 GB of swap, so an image build can't run a 4 GB host out of memory.
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# Host firewall, matching the cloud firewall. Published Docker ports bypass ufw; only Caddy
# publishes any (80 and 443).
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443
ufw --force enable

# SSH with keys only.
cat > /etc/ssh/sshd_config.d/10-serpsense.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
sshd -t
systemctl reload ssh

# Security updates install themselves.
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
systemctl enable --now fail2ban unattended-upgrades

# The deploy user owns the app and runs Compose; root's authorised keys let the same people in.
if ! id deploy >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash --groups docker deploy
fi
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
install -m 600 -o deploy -g deploy /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
install -d -m 750 -o deploy -g deploy "$APP_DIR"
install -d -m 700 -o deploy -g deploy "$BACKUP_DIR"

# Nightly database dump at 02:30 UTC, kept 14 days (deploy/backup.sh).
cat > /etc/cron.d/serpsense-backup <<EOF
30 2 * * * deploy $APP_DIR/deploy/backup.sh >> $BACKUP_DIR/backup.log 2>&1
EOF

echo "Provisioned. Next: .env in $APP_DIR (owner deploy, mode 600), then scripts/deploy.sh."
