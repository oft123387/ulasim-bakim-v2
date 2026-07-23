"""Merkezi loglama (Faz 23 — Güvenlik sertleştirme: Ayrıntılı hata logları +
Merkezi loglama).

Hem konsola (docker logs ile anlık izlenir) hem de kalıcı bir volume'deki
dönen (rotating) bir dosyaya yazar; böylece konteyner yeniden başlasa/
yeniden oluşturulsa bile geçmiş loglar (10MB x 5 dosya ~ 50MB) korunur.
LOG_DIR ortam değişkeniyle konumu değiştirilebilir (bkz. docker-compose.yml)."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from . import config

_configured = False


def configure_logging() -> logging.Logger:
    global _configured
    logger = logging.getLogger("ulasim_bakim")
    if _configured:
        return logger
    _configured = True

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    log_dir = Path(config.get("LOG_DIR", str(Path(__file__).resolve().parent.parent / "logs")))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "app.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except OSError:
        # Dosya sistemine yazılamıyorsa (izin vb.) uygulama yine de açılsın
        # diye yalnızca konsol logu ile devam edilir.
        logger.warning("Log dosyasına yazılamıyor (%s) — yalnızca konsola loglanacak.", log_dir)

    logger.propagate = False
    return logger


logger = configure_logging()
