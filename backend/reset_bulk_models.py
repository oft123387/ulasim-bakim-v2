"""Bozankaya/Sirio ile ilgili TÜM bakım planlarını, ağaçlarını ve iş
emirlerini temizler (baştan test için) — araçların kendisine DOKUNMAZ,
"Genel" (vehicle_model="") planlara da DOKUNMAZ.

Model adı TAM eşleşme değil, NORMALİZE edilmiş (Türkçe karakter/boşluk/
büyük-küçük harf duyarsız) alt dize eşleşmesiyle bulunur — böylece hem
doğru çözümlenmiş ("Sirio", "Bozankaya Tip-1", "Bozankaya Tip-2") hem de
eski/hatalı çözümlenmiş ("BOZANKAYA", "SİRİO" gibi ham Excel token'ları)
TÜMÜ tek seferde temizlenir.

Kullanım (konteyner içinde):
    docker exec -it ulasim-bakim python reset_bulk_models.py

Ekstra anahtar kelime eklemek isterseniz (varsayılan: bozankaya, sirio):
    docker exec -it ulasim-bakim python reset_bulk_models.py bozankaya sirio caf
"""

import sys

from app.database import SessionLocal
from app import models
from app.excel_utils import _norm

KEYWORDS = [k.lower() for k in (sys.argv[1:] or ["bozankaya", "sirio"])]

db = SessionLocal()

all_plans = db.query(models.MaintenancePlan).all()
target_plans = [
    p for p in all_plans
    if p.vehicle_model and any(kw in _norm(p.vehicle_model) for kw in KEYWORDS)
]

print(f"Anahtar kelimeler: {KEYWORDS}")
print(f"Eşleşen plan sayısı: {len(target_plans)}")
for p in target_plans:
    print(f"  - [{p.vehicle_model!r}] {p.name} (interval_km={p.interval_km}, is_catalog_only={p.is_catalog_only})")

if not target_plans:
    print("Silinecek plan bulunamadı.")
    db.close()
    sys.exit(0)

plan_ids = [p.id for p in target_plans]

maint_count = db.query(models.Maintenance).filter(models.Maintenance.plan_id.in_(plan_ids)).count()
for m in db.query(models.Maintenance).filter(models.Maintenance.plan_id.in_(plan_ids)).all():
    db.delete(m)
db.flush()

for p in target_plans:
    db.delete(p)
db.flush()
db.commit()

remaining = (
    db.query(models.MaintenancePlan)
    .filter(models.MaintenancePlan.id.in_(plan_ids))
    .count()
)
print(f"Silindi: {len(plan_ids)} plan, {maint_count} iş emri (ağaç/parça eşleşmeleri kaskad silindi).")
print(f"Kontrol — kalan (0 olmalı): {remaining}")

db.close()
