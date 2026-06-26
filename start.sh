#!/bin/bash
# Start HAFJET WhatsApp Bot Webhook Listener
cd /home/hafizi145/.hermes/whatsapp-bot
mkdir -p /home/hafizi145/.hermes/logs
python3 webhook_listener.py
