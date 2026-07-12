import zipfile, os
src = os.path.expanduser("~/.hermes/whatsapp-bot")
dst = os.path.join(src, "deploy-hafjet-bot.zip")

exclude_patterns = [
    ".git", "__pycache__", ".venv", ".env", ".env.", "node_modules",
    ".bak", ".backup", "test_", ".zip", "logs",
    ".backup", "hermes_ai.py.backup", "hermes_ai_new.py",
    "update_fallback.py", "fix_fallback.py",
    ".db",  # exclude all database files (bot_data.db, etc.)
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