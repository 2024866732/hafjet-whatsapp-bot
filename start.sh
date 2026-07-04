#!/bin/bash
cd /home/site/wwwroot
/home/site/wwwroot/antenv/bin/gunicorn -w 2 -k uvicorn.workers.UvicornWorker webhook_listener:app --bind 0.0.0.0:8000 --timeout 120
