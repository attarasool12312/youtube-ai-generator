#!/usr/bin/env bash
set -euo pipefail

DOMAIN="${1:-${APP_DOMAIN:-}}"
EMAIL="${2:-${CERTBOT_EMAIL:-}}"

if [ -z "$DOMAIN" ] || [ -z "$EMAIL" ]; then
    echo "Usage: ./scripts/setup_domain.sh DOMAIN CERTBOT_EMAIL"
    echo "Example: ./scripts/setup_domain.sh example.com admin@example.com"
    exit 2
fi

echo "=== DOMAIN SETUP: $DOMAIN ==="

sudo apt-get update
sudo apt-get install -y nginx snapd

sudo systemctl enable nginx
sudo systemctl start nginx

if ! command -v certbot >/dev/null 2>&1; then
    sudo snap install core || true
    sudo snap refresh core || true
    sudo snap install --classic certbot
    sudo ln -sf /snap/bin/certbot /usr/bin/certbot
fi

TMP="/tmp/${DOMAIN}.nginx"

cat > "$TMP" <<EOF
server {
    listen 80;
    listen [::]:80;

    server_name ${DOMAIN} www.${DOMAIN};

    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;

        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        proxy_read_timeout 3600;
        proxy_send_timeout 3600;
    }
}
EOF

sudo cp "$TMP" "/etc/nginx/sites-available/${DOMAIN}"
sudo ln -sf \
    "/etc/nginx/sites-available/${DOMAIN}" \
    "/etc/nginx/sites-enabled/${DOMAIN}"

sudo rm -f /etc/nginx/sites-enabled/default

sudo nginx -t
sudo systemctl restart nginx

echo
echo "HTTP proxy ready."
echo "DNS A records for @ and www MUST point to this VM before Certbot."
echo

# Only add www for root domains (subdomains have more than 1 dot)
DOTCOUNT=$(echo "$DOMAIN" | tr -cd '.' | wc -c)
if [ "$DOTCOUNT" -gt 1 ]; then
    echo "Subdomain detected, skipping www variant"
    sudo certbot --nginx \
        -d "$DOMAIN" \
        --non-interactive \
        --agree-tos \
        --email "$EMAIL" \
        --no-eff-email \
        --redirect
else
    sudo certbot --nginx \
        -d "$DOMAIN" \
        -d "www.$DOMAIN" \
        --non-interactive \
        --agree-tos \
        --email "$EMAIL" \
        --no-eff-email \
        --redirect
fi

sudo nginx -t
sudo systemctl restart nginx

echo "DOMAIN_SETUP_COMPLETE"
