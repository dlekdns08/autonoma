#!/bin/bash
# Initial SSL certificate setup for autonoma.koala.ai.kr
# Run this ONCE on the server before starting with HTTPS.
#
# Prerequisites:
#   - DNS A record for autonoma.koala.ai.kr pointing to this server
#   - Port 80 open
#
# Usage: ./scripts/init-ssl.sh [email]

set -euo pipefail

DOMAIN="autonoma.koala.ai.kr"
EMAIL="${1:-admin@koala.ai.kr}"

echo "=== Autonoma SSL Setup ==="
echo "Domain: $DOMAIN"
echo "Email:  $EMAIL"
echo ""

# Step 1: Start nginx with HTTP-only config for ACME challenge
echo "[1/4] Creating temporary HTTP-only nginx config..."
mkdir -p nginx
cat > nginx/nginx.conf.tmp <<'NGINX'
server {
    listen 80;
    server_name autonoma.koala.ai.kr;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 200 'Waiting for SSL setup...';
        add_header Content-Type text/plain;
    }
}
NGINX

# Step 2: Start nginx with temp config
echo "[2/4] Starting nginx for ACME challenge..."
docker compose run -d --rm \
  -v "$(pwd)/nginx/nginx.conf.tmp:/etc/nginx/conf.d/default.conf:ro" \
  -p 80:80 \
  nginx || docker run -d --name autonoma-certbot-nginx \
  -v "$(pwd)/nginx/nginx.conf.tmp:/etc/nginx/conf.d/default.conf:ro" \
  -v "autonoma_certbot-webroot:/var/www/certbot" \
  -p 80:80 \
  nginx:alpine

sleep 3

# Step 3: Request certificate
echo "[3/4] Requesting certificate from Let's Encrypt..."
docker run --rm \
  -v "autonoma_certbot-webroot:/var/www/certbot" \
  -v "autonoma_certbot-certs:/etc/letsencrypt" \
  certbot/certbot certonly \
    --webroot \
    --webroot-path=/var/www/certbot \
    --email "$EMAIL" \
    --agree-tos \
    --no-eff-email \
    -d "$DOMAIN"

# Step 4: Cleanup temp nginx
echo "[4/4] Cleaning up..."
docker stop autonoma-certbot-nginx 2>/dev/null || true
docker rm autonoma-certbot-nginx 2>/dev/null || true
rm -f nginx/nginx.conf.tmp

echo ""
echo "=== SSL certificate obtained! ==="
echo "Now run: docker compose up -d"
echo "Site will be available at: https://$DOMAIN"
