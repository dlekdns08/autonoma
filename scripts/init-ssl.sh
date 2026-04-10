#!/bin/bash
set -euo pipefail

DOMAIN="autonoma.koala.ai.kr"
EMAIL="${1:-admin@koala.ai.kr}"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Autonoma SSL Setup ==="
echo "Domain: $DOMAIN"
echo "Email:  $EMAIL"
echo "Dir:    $PROJECT_DIR"
echo ""

# Step 1: Use HTTP-only nginx config
echo "[1/5] Switching to HTTP-only nginx config..."
cp "$PROJECT_DIR/nginx/nginx.init.conf" "$PROJECT_DIR/nginx/active.conf"

# Step 2: Start services with HTTP-only
echo "[2/5] Starting services (HTTP only)..."
cd "$PROJECT_DIR"
NGINX_CONF=nginx/active.conf docker compose up -d nginx web api

echo "Waiting for nginx to be ready..."
sleep 5

# Step 3: Request certificate
echo "[3/5] Requesting SSL certificate..."
docker compose run --rm certbot certonly \
  --webroot \
  --webroot-path=/var/www/certbot \
  --email "$EMAIL" \
  --agree-tos \
  --no-eff-email \
  -d "$DOMAIN"

# Step 4: Switch to full HTTPS config
echo "[4/5] Switching to HTTPS nginx config..."
cp "$PROJECT_DIR/nginx/nginx.conf" "$PROJECT_DIR/nginx/active.conf"

# Step 5: Restart nginx with HTTPS
echo "[5/5] Restarting nginx with SSL..."
docker compose restart nginx

echo ""
echo "=== Done! ==="
echo "https://$DOMAIN is now live."
