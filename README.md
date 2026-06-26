# HAFJET WhatsApp Bot v2.0

## Python Dependencies
- fastapi
- uvicorn
- httpx
- python-dotenv
- gunicorn

## Environment Variables (do NOT commit .env)
- WHATSAPP_ACCESS_TOKEN
- WHATSAPP_PHONE_ID
- APP_SECRET
- VERIFY_TOKEN
- OPENROUTER_API_KEY
- OPENROUTER_MODEL
- OPENROUTER_BASE_URL
- AI_TIMEOUT

## Deployment
1. Zip files: `python3 -c "import zipfile; z=zipfile.ZipFile('deploy.zip','w'); [z.write(f) for f in ['webhook_listener.py','hermes_ai.py','repair_db.py','requirements.txt','startup.txt']]; z.close()"`
2. Deploy: `az webapp deploy --resource-group hafjet-bot-rg --name hafjet-whatsapp-bot --src-path deploy.zip`

## API Endpoints
- GET /webhook — Meta verification
- POST /webhook — Message processor
- GET /health — Health check

## Changelog
### v2.0.0 (2026-06-26)
- Added canonical greeting for consistent replies
- Added message dedup (5-min window)
- Reduced AI max_tokens 300→150, temperature 0.7→0.3
- Added _is_greeting() extended detection (24 patterns)
- Added anti-duplicate send protection
- Added _is_too_long() check (max 500 chars)
- WhatsApp app live on Meta Developer Console
