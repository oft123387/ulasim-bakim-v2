"""Örnek veri yükler. Çalıştırma: python seed.py

Kullanıcılar + demo araçlar oluşturur, ardından data/koruyucu_bakim.xlsx
dosyasındaki gerçek koruyucu bakım listesini (25.000 / 50.000 / 100.000 /
200.000 / 300.000 / 500.000 km planları + bakım ağaçları) içe aktarır.
Son olarak, henüz bir 3D model yüklenmemişse, SketchUp'tan çıkarılan
başlangıç tramvay modelini (.glb) sisteme ekler — admin bu model üzerinde
tıklanabilir parçalar tanımlayıp ağaç maddeleriyle eşleştirebilir.
"""

import os
import shutil
import uuid

from app.auth import hash_password
from app.database import Base, SessionLocal, engine
from app import models
from app.excel_utils import import_maintenance_tree

Base.metadata.create_all(bind=engine)
db = SessionLocal()

if not db.query(models.User).first():
    db.add_all(
        [
            models.User(username="admin", full_name="Sistem Yöneticisi", role="admin",
                        password_hash=hash_password("admin123")),
            models.User(username="muhendis", full_name="Beyza Erdem", role="engineer",
                        password_hash=hash_password("muhendis123")),
            models.User(username="usta", full_name="Ahmet Yılmaz", role="technician",
                        password_hash=hash_password("usta123")),
        ]
    )
    print("Kullanıcılar oluşturuldu.")
else:
    # Admin hesabı yanlışlıkla pasife alınmış/silinmiş olabilir; garanti altına al.
    admin = db.query(models.User).filter_by(username="admin").first()
    if admin and not admin.is_active:
        admin.is_active = True
        db.commit()
        print("Admin hesabı yeniden aktifleştirildi.")
    elif not admin:
        db.add(models.User(username="admin", full_name="Sistem Yöneticisi", role="admin",
                            password_hash=hash_password("admin123")))
        db.commit()
        print("Admin hesabı yeniden oluşturuldu.")
    else:
        print("Kullanıcılar zaten mevcut, atlandı.")

if not db.query(models.Vehicle).first():
    # Araç kodları 3801-3880 aralığında numaralandırılır (80 araca kadar
    # eklenebilir), böylece kod formatı tutarlı ve filtrelenebilir kalır.
    db.add_all(
        [
            models.Vehicle(code="3801", name="Tramvay 3801", current_km=1_000_000),
            models.Vehicle(code="3802", name="Tramvay 3802", current_km=487_000),
        ]
    )
    print("Örnek araçlar oluşturuldu.")
else:
    print("Araçlar zaten mevcut, atlandı.")

db.commit()

xlsx_path = os.path.join(os.path.dirname(__file__), "data", "koruyucu_bakim.xlsx")
if os.path.isfile(xlsx_path):
    result = import_maintenance_tree(db, xlsx_path)
    print(f"Gerçek bakım ağacı içe aktarıldı: {len(result['plans'])} plan, "
          f"{result['nodes_created']} düğüm.")
else:
    print("Uyarı: data/koruyucu_bakim.xlsx bulunamadı, bakım ağacı içe aktarılmadı.")

# ---------------------------------------------------------------- 3D Model
default_glb_src = os.path.join(os.path.dirname(__file__), "data", "tram_model.glb")
if os.path.isfile(default_glb_src) and not db.query(models.Model3D).first():
    models_dir = os.path.join(os.path.dirname(__file__), "uploads", "models")
    os.makedirs(models_dir, exist_ok=True)
    fname = f"{uuid.uuid4().hex}.glb"
    shutil.copyfile(default_glb_src, os.path.join(models_dir, fname))
    db.add(models.Model3D(label="Tramvay 3D Model", filename=fname, order_index=0))
    db.commit()
    print("Başlangıç 3D tramvay modeli eklendi (parçaları Bakım Ağacı "
          "sayfasından ağaç maddeleriyle eşleştirebilirsiniz).")
elif not os.path.isfile(default_glb_src):
    print("Uyarı: data/tram_model.glb bulunamadı, başlangıç 3D modeli eklenmedi.")
else:
    print("3D model zaten mevcut, atlandı.")

db.close()
print("Seed tamamlandı.")
