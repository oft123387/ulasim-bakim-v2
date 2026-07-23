#!/bin/sh
set -e

# Veritabanı ve yüklenen dosyalar kalıcı volume'lerde tutulur.
mkdir -p /app/data /app/backend/uploads/models /app/backend/uploads/diagrams

cd /app/backend

# İlk kurulumda örnek veriyi yükle (kullanıcılar, araçlar, gerçek bakım ağacı,
# başlangıç 3D modeli). Veritabanı zaten varsa atlanır — veri kaybı olmaz.
DB_FILE="/app/data/ulasim_bakim.db"
if [ ! -f "$DB_FILE" ]; then
  echo "[docker] İlk kurulum: örnek veri yükleniyor..."
  python seed.py || echo "[docker] Uyarı: seed.py çalıştırılamadı, boş veritabanıyla devam ediliyor."
else
  echo "[docker] Mevcut veritabanı bulundu, seed atlanıyor."
fi

echo "[docker] Sunucu başlatılıyor: http://localhost:8000"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
