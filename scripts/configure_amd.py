"""Interactive local-only credential setup. Never echoes or logs the key."""
import getpass
import os
from pathlib import Path

path = Path('.env')
if path.exists():
    raise SystemExit('.env already exists; edit it locally to avoid overwriting your configuration.')
key = getpass.getpass('AMD Token Factory API key (hidden): ').strip()
if not key or any(c.isspace() for c in key) or any(c in key for c in ['"', "'", '#', '$', '\\']):
    raise SystemExit('Unexpected key format; no file written.')
rpm = int(input('API key RPM limit (read your Token Factory dashboard): '))
concurrency = int(input('API key concurrent-request limit: '))
if not 1 <= rpm <= 100000 or not 1 <= concurrency <= 64:
    raise SystemExit('Invalid limits; no file written.')
text = f'INFERFLOW_MODE=amd\nINFERFLOW_AMD_API_KEY={key}\nINFERFLOW_RPM={rpm}\nINFERFLOW_CONCURRENCY={concurrency}\nINFERFLOW_LIMITS_CONFIRMED=true\nINFERFLOW_DAILY_BUDGET=10\nINFERFLOW_DB_PATH=data/amd.sqlite3\n'
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as file:
    file.write(text)
print('Saved local .env with mode 0600. Key was not printed. Restart the gateway.')
