"""backend/.env dosyasını okur (varsa). Ek kütüphane gerektirmez.

Öncelik sırası: gerçek ortam değişkeni > .env dosyası > varsayılan değer.
"""

import os
from pathlib import Path

_env_file = Path(__file__).resolve().parent.parent / ".env"

if _env_file.is_file():
    for line in _env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def get(key: str, default: str) -> str:
    return os.environ.get(key, default)
