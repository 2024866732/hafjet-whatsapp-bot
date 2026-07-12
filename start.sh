#!/bin/bash
cd /home/site/wwwroot

# Clear any stale Python bytecode cache — prevents old code from running
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
find . -name "*.pyc" -delete 2>/dev/null

# Install dependencies (no antenv — use system Python directly)
pip3 install --no-cache-dir -r requirements.txt 2>&1 || python3 -m pip install --no-cache-dir -r requirements.txt 2>&1

# Start gunicorn
exec gunicorn -w 1 -k uvicorn.workers.UvicornWorker webhook_listener:app --bind 0.0.0.0:8000 --timeout 600
