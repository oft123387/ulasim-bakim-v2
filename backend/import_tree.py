"""Gerçek koruyucu bakım listesini (Excel) veritabanına aktarır.

Kullanım: python import_tree.py [xlsx_yolu]
Varsayılan dosya: data/koruyucu_bakim.xlsx

Her bakım periyodu (25.000 / 50.000 / 100.000 / ... km) için bir
MaintenancePlan ve o plana özel bakım ağacı (TreeNode) oluşturur.
Tekrar çalıştırmak güvenlidir: aynı periyodun ağacı varsa silinip
Excel'deki güncel haliyle yeniden oluşturulur.
"""

import sys

from app.database import Base, SessionLocal, engine
from app.excel_utils import import_maintenance_tree

Base.metadata.create_all(bind=engine)

path = sys.argv[1] if len(sys.argv) > 1 else "data/koruyucu_bakim.xlsx"

db = SessionLocal()
result = import_maintenance_tree(db, path)
db.close()

print(f"{len(result['plans'])} bakım planı işlendi, {result['nodes_created']} düğüm oluşturuldu:")
for p in result["plans"]:
    print(f"  - {p['name']} (interval_km={p['interval_km']})")
