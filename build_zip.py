import zipfile, os
src = os.path.expanduser("~/.hermes/whatsapp-bot")
dst = os.path.join(src, "deploy-hafjet-bot.zip")

exclude_patterns = [
    ".git", "__pycache__", ".venv", ".env", ".env.", "node_modules",
    ".bak", ".backup", "test_", ".zip", "logs",
    ".backup", "hermes_ai.py.backup", "hermes_ai_new.py",
    "update_fallback.py", "fix_fallback.py",
    ".db",  # exclude all database files (bot_data.db, etc.)
    # ── Pre-deploy artifact hygiene (patch 2026-07-16) ──
    ".tar.gz",                      # db / file backups
    "backup",                       # *backup* (db-backup, azure-settings-backup, etc.)
    "AGENTS.md",                    # auto-generated context doc
    "DEPLOYMENT",                   # DEPLOYMENT*.md / DEPLOYMENT_NOTES.md
    "oracle-",                      # oracle-deployment-plan-*.md
    ".user.js",                     # spx_phone_agent*.user.js
    "business_info.txt",            # untracked loose copy (loaded at runtime from disk anyway)
    "check_", "upload_", "debug_", "verify_", "monitor_", "fix_",  # temp/debug scripts
    "intent_rules.json", "media_map.json",
    "start_local.sh", "apply_all_patches.py", "run_fetch_phones.py", "s2f5_test.py",
    "azure-settings-backup",        # azure-settings-backup-*.json
]

def should_exclude(name):
    for pat in exclude_patterns:
        if pat in name:
            return True
    return False

with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(src):
        # Skip excluded dirs
        dirs[:] = [d for d in dirs if not should_exclude(d)]
        for fn in files:
            if should_exclude(fn):
                continue
            full = os.path.join(root, fn)
            arcname = os.path.relpath(full, src)
            zf.write(full, arcname)

print(f"ZIP created: {dst}")
print(f"Size: {os.path.getsize(dst)} bytes")
print("Files:")
with zipfile.ZipFile(dst) as zf:
    for info in zf.infolist():
        print(f"  {info.filename} ({info.file_size} bytes)")