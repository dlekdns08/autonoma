#!/bin/bash
set -euo pipefail

WEB_DOMAIN="autonoma.letskoala.com"
API_DOMAIN="api.letskoala.com"
EMAIL="${1:-admin@letskoala.com}"
PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Autonoma SSL Setup ==="
echo "Web domain: $WEB_DOMAIN"
echo "API domain: $API_DOMAIN"
echo "Email:      $EMAIL"
echo "Dir:        $PROJECT_DIR"
echo ""

# Step 1: Use HTTP-only nginx config
echo "[1/6] Switching to HTTP-only nginx config..."
cp "$PROJECT_DIR/nginx/nginx.init.conf" "$PROJECT_DIR/nginx/active.conf"

# Step 2: Start services with HTTP-only
echo "[2/6] Starting services (HTTP only)..."
cd "$PROJECT_DIR"
NGINX_CONF=nginx/active.conf docker compose up -d nginx web api

echo "Waiting for nginx to be ready..."
sleep 5

# Step 3: Request certificate for web domain
echo "[3/6] Requesting SSL certificate for $WEB_DOMAIN..."
docker compose run --rm certbot certonly \
  --webroot \
  --webroot-path=/var/www/certbot \
  --email "$EMAIL" \
  --agree-tos \
  --no-eff-email \
  -d "$WEB_DOMAIN"

# Step 4: Request certificate for API domain
echo "[4/6] Requesting SSL certificate for $API_DOMAIN..."
docker compose run --rm certbot certonly \
  --webroot \
  --webroot-path=/var/www/certbot \
  --email "$EMAIL" \
  --agree-tos \
  --no-eff-email \
  -d "$API_DOMAIN"

# Step 5: Switch to full HTTPS config
echo "[5/6] Switching to HTTPS nginx config..."
cp "$PROJECT_DIR/nginx/nginx.conf" "$PROJECT_DIR/nginx/active.conf"

# Step 6: Restart nginx with HTTPS
echo "[6/6] Restarting nginx with SSL..."
docker compose restart nginx

echo ""
echo "=== Done! ==="
echo "https://$WEB_DOMAIN is now live."
echo "https://$API_DOMAIN is now live."
