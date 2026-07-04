#!/bin/bash
# Azure B1 Deployment Script — HAFJET WhatsApp Bot
# Reads settings directly from backup file to avoid value redaction

set -e

RG="hafjet-bot-rg"
APP="hafjet-whatsapp-bot"
PLAN="hafjet-bot-plan"
ZIP="/home/hafizi145/.hermes/whatsapp-bot/hafjet-prod.zip"
BACKUP="/home/hafizi145/.hermes/whatsapp-bot/azure-settings-backup-2026-06-27.json"

echo "=== Step 1: Create App Service Plan (B1) ==="
az appservice plan create \
  --name "$PLAN" \
  --resource-group "$RG" \
  --sku B1 \
  --is-linux \
  --location southeastasia

echo "=== Step 2: Create Web App (Python 3.11) ==="
az webapp create \
  --name "$APP" \
  --resource-group "$RG" \
  --plan "$PLAN" \
  --runtime "PYTHON:3.11"

echo "=== Step 3: Set all app settings from backup + DASHBOARD_API_KEY ==="
# Generate a new DASHBOARD_API_KEY (was set on live app but never backed up)
DASHBOARD_API_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
echo "Generated new DASHBOARD_API_KEY"

# Build settings args from backup JSON + add DASHBOARD_API_KEY
SETTINGS_ARGS=$(python3 -c "
import json, os
with open('$BACKUP') as f:
    data = json.load(f)
args = []
for item in data:
    args.append(f\"{item['name']}={item['value']}\")
args.append('DASHBOARD_API_KEY=' + os.environ.get('DASHBOARD_API_KEY', ''))
print(' '.join(args))
")

az webapp config appsettings set \
  --name "$APP" \
  --resource-group "$RG" \
  --settings $SETTINGS_ARGS \
  "WEBSITES_CONTAINER_START_TIME_LIMIT=600"

echo "=== Step 4: Set startup command ==="
az webapp config set \
  --name "$APP" \
  --resource-group "$RG" \
  --startup-file "bash start.sh"

echo "=== Step 5: Deploy ZIP ==="
az webapp deployment source config-zip \
  --name "$APP" \
  --resource-group "$RG" \
  --src "$ZIP"

echo "=== Step 6: Enable health check ==="
az webapp config set \
  --name "$APP" \
  --resource-group "$RG" \
  --generic-configurations '{"healthCheckPath": "/health"}'

echo "=== Step 7: Wait for app to start ==="
sleep 30

echo "=== Step 8: Tail startup logs ==="
timeout 15 az webapp log tail --name "$APP" --resource-group "$RG" 2>&1 | head -50 || true

echo "=== Step 9: Validate /health ==="
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "https://${APP}.azurewebsites.net/health")
echo "HTTP Status: $HTTP_CODE"

echo "=== Step 10: Validate root ==="
curl -s "https://${APP}.azurewebsites.net/" | head -5

echo "=== DEPLOYMENT COMPLETE ==="
