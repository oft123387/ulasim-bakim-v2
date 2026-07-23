@echo off
REM Ulasim Bakim Yonetim Sistemi - baslatma betigi
cd /d "%~dp0backend"

if not exist ulasim_bakim.db (
    echo Ilk kurulum: ornek veri yukleniyor...
    python seed.py
)

echo Sunucu baslatiliyor: http://localhost:8000
start "" http://localhost:8000
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
