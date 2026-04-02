#!/bin/bash
# Deploy AlphaAgent backend to GCP VM (ssh-social)
#
# Prerequisites:
#   1. DNS: Point alphaagent-api.skdev.one → 34.23.158.39
#   2. .env file with OPENROUTER_API_KEY in project root
#
# Usage: bash deploy/deploy.sh

set -e

VM="ssh-social"
REMOTE_DIR="/home/rsumit123/alphaagent"
PORT="8030"
DOMAIN="alphaagent-api.skdev.one"

echo "=== AlphaAgent Backend Deployment ==="
echo ""

# 1. Clean up disk space on VM
echo "[1/6] Cleaning up Docker on VM..."
ssh $VM "docker system prune -f 2>/dev/null || true"

# 2. Create remote directory
echo "[2/6] Setting up remote directory..."
ssh $VM "mkdir -p $REMOTE_DIR/sessions $REMOTE_DIR/data $REMOTE_DIR/learnings $REMOTE_DIR/logs"

# 3. Sync project files to VM (excluding frontend, venv, data)
echo "[3/6] Syncing project files..."
rsync -avz --delete \
  --exclude='.venv/' \
  --exclude='frontend/' \
  --exclude='node_modules/' \
  --exclude='sessions/' \
  --exclude='data/' \
  --exclude='learnings/' \
  --exclude='logs/' \
  --exclude='__pycache__/' \
  --exclude='.git/' \
  --exclude='.env.local' \
  ./ $VM:$REMOTE_DIR/

# 4. Copy .env file
echo "[4/6] Copying .env..."
scp .env $VM:$REMOTE_DIR/.env

# 5. Build and start Docker container
echo "[5/6] Building and starting container..."
ssh $VM "cd $REMOTE_DIR && docker compose up -d --build"

# 6. Set up nginx (if not already configured)
echo "[6/6] Checking nginx config..."
ssh $VM "
if [ ! -f /etc/nginx/sites-enabled/alphaagent ]; then
  sudo cp $REMOTE_DIR/deploy/nginx-alphaagent.conf /etc/nginx/sites-enabled/alphaagent
  sudo nginx -t && sudo systemctl reload nginx
  echo 'Nginx configured. Run: sudo certbot --nginx -d $DOMAIN'
else
  echo 'Nginx config already exists.'
fi
"

echo ""
echo "=== Deployment complete ==="
echo "  Backend: http://$DOMAIN (run certbot for HTTPS)"
echo "  Port: $PORT"
echo "  Container: alphaagent-backend"
echo ""
echo "Next steps:"
echo "  1. Ensure DNS points $DOMAIN → 34.23.158.39"
echo "  2. SSH to VM and run: sudo certbot --nginx -d $DOMAIN"
echo "  3. Verify: curl https://$DOMAIN/api/sessions"
