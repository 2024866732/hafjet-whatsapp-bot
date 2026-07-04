#!/bin/bash
set -e

cd ~/.hermes/whatsapp-bot

echo "=== Building clean production ZIP ==="
python3 << 'EOF'
import zipfile, os, fnmatch

EXCLUDE_PATTERNS = [
    '.env', '.env.*', '*.pyc', '__pycache__',
    '.git', 'node_modules', '.venv', 'venv',
    '.backup', 'app_settings_*.json',
    'azure-settings-backup*', '*.zip', 'startup_debug.log',
    'deploy*.sh',
]

def should_exclude_dir(dirpath, dirname):
    full = os.path.join(dirpath, dirname)
    # Only exclude root-level dist/build, keep dashboard/dist
    if full in ('./dist', './build'):
        return True
    return any(fnmatch.fnmatch(dirname, p) for p in EXCLUDE_PATTERNS)

def should_exclude_file(arcname):
    name = os.path.basename(arcname)
    return any(fnmatch.fnmatch(name, p) for p in EXCLUDE_PATTERNS)

with zipfile.ZipFile('hafjet-prod.zip', 'w', zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk('.'):
        dirs[:] = [d for d in dirs if not should_exclude_dir(root, d)]
        for file in files:
            filepath = os.path.join(root, file)
            arcname = filepath[2:]  # remove ./
            if not should_exclude_file(arcname):
                zf.write(filepath, arcname)

size = os.path.getsize('hafjet-prod.zip')
print(f"ZIP built: {size:,} bytes ({len(zipfile.ZipFile('hafjet-prod.zip').namelist())} files)")

# Verify no leaks
with zipfile.ZipFile('hafjet-prod.zip', 'r') as zf:
    sensitive = [n for n in zf.namelist() if n.startswith('.env') or '.backup' in n or 'azure-settings' in n]
    if sensitive:
        print(f"⚠️  WARNING: sensitive files still in ZIP: {sensitive}")
        raise SystemExit(1)
    print("✅ No sensitive files in ZIP")

    # Verify dashboard/dist is included
    dist_files = [n for n in zf.namelist() if n.startswith('dashboard/dist/')]
    if dist_files:
        print(f"✅ dashboard/dist included ({len(dist_files)} files)")
    else:
        print("⚠️  WARNING: dashboard/dist not in ZIP — dashboard will 404")
EOF

echo "=== Deploying to Azure ==="
az webapp deploy \
  --name hafjet-whatsapp-bot \
  --resource-group hafjet-bot-rg \
  --src-path hafjet-prod.zip \
  --type zip \
  --async false

echo "=== Post-deploy smoke test ==="
sleep 60

BASE="https://hafjet-whatsapp-bot.azurewebsites.net"
FAIL=0

echo -n "  /health          → "
HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "$BASE/health")
if [ "$HTTP" = "200" ]; then echo "✅ $HTTP"; else echo "❌ $HTTP"; FAIL=1; fi

echo -n "  /dashboard       → "
HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "$BASE/dashboard")
if [ "$HTTP" = "200" ]; then echo "✅ $HTTP"; else echo "❌ $HTTP"; FAIL=1; fi

echo -n "  /dashboard/test  → "
HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "$BASE/dashboard/test")
if [ "$HTTP" = "200" ]; then echo "✅ $HTTP"; else echo "❌ $HTTP"; FAIL=1; fi

# Pick first CSS or JS asset from dashboard/dist
ASSET=$(cd dashboard/dist/assets 2>/dev/null && ls *.css *.js 2>/dev/null | head -1)
if [ -n "$ASSET" ]; then
    echo -n "  /dashboard/assets/$ASSET → "
    HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "$BASE/dashboard/assets/$ASSET")
    if [ "$HTTP" = "200" ]; then echo "✅ $HTTP"; else echo "❌ $HTTP"; FAIL=1; fi
fi

echo ""
if [ "$FAIL" = "0" ]; then
    echo "✅ All smoke tests passed"
else
    echo "❌ Some smoke tests failed"
    exit 1
fi

echo "=== Done ==="
