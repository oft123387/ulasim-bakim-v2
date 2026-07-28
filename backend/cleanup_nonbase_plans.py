"""Mevcut veritabanındaki SAHTE (temel periyodun katı olan) bakım planlarını
temizler — ör. "1.200.000 km Bakımı", "400.000 km Bakımı" gibi, aslında ayrı
bir bakım olmayıp bir temel periyodun (200.000 vb.) katı olan planlar.

Ne yapar (bkz. excel_utils.purge_nonbase_plans):
  * Tamamlanmış (geçmiş) iş emirlerini o periyodu bölen EN BÜYÜK temel planın
    (ör. 200.000 km Bakımı) altına TAŞIR — bakım geçmişi kaybolmaz.
  * Açık (Bekliyor/Devam Ediyor) iş emirlerini siler — km güncellemesinde
    temel plandan yeniden açılırlar.
  * Plana ait ağacı ve 3D parça eşleşmelerini siler, planı siler (taşınacak
    temel plan yoksa ve geçmişi varsa yalnızca pasife alır).
  * AYRICA: yüzde bazlı tüm planların tolerans aralığını -%5 / +%10 yapar.

Temel periyot kümeleri (excel_utils içinde tanımlı):
  SİRİO   : 10 / 30 / 60 / 100 / 200 / 500 bin km
  Diğerleri: 25 / 100 / 200 / 300 / 500 bin km

Kullanım (Docker ile çalışan kurulumda, backend konteynerinde):
    docker compose exec backend python cleanup_nonbase_plans.py           # önizleme (kuru çalışma)
    docker compose exec backend python cleanup_nonbase_plans.py --apply   # gerçekten uygula

Doğrudan (yerel) çalıştırma:
    cd backend && python cleanup_nonbase_plans.py --apply

ÖNEMLİ: Uygulamadan önce otomatik yedek alınır (backup_utils varsa). Yine de
--apply öncesi önce yedeğinizin olduğundan emin olun.
"""

import sys

from app.database import SessionLocal
from app import models
from app.excel_utils import purge_nonbase_plans, _base_intervals_for, canonical_base_interval


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def main() -> None:
    apply = "--apply" in sys.argv
    db = SessionLocal()
    try:
        vehicle_models = [
            row[0]
            for row in db.query(models.MaintenancePlan.vehicle_model).distinct().all()
        ]
        vehicle_models = sorted({vm or "" for vm in vehicle_models})

        print("=" * 72)
        print("SAHTE (kat) BAKIM PLANI TEMİZLEME")
        print("Mod:", "UYGULA (--apply)" if apply else "ÖNİZLEME (kuru çalışma)")
        print("=" * 72)

        # Önce ne yapılacağını göster (her modelde hangi planlar kat sayılıyor)
        any_found = False
        for vm in vehicle_models:
            bases = _base_intervals_for(vm)
            plans = (
                db.query(models.MaintenancePlan)
                .filter(models.MaintenancePlan.vehicle_model == vm)
                .all()
            )
            hits = []
            for p in plans:
                ptype = getattr(p, "plan_type", None) or "km"
                if ptype != "km" or not p.interval_km or p.interval_km <= 0:
                    continue
                if p.interval_km in bases:
                    continue
                canon = canonical_base_interval(vm, p.interval_km)
                if canon is None:
                    continue
                hits.append((p, canon))
            if hits:
                any_found = True
                label = vm or "(Genel)"
                print(f"\nModel: {label}  — temel periyotlar: "
                      f"{', '.join(_fmt(b) for b in sorted(bases))}")
                for p, canon in hits:
                    print(f"   • #{p.id} '{p.name}' ({_fmt(p.interval_km)} km) "
                          f"→ {_fmt(canon)} km bakımına katlanacak")

        if not any_found:
            print("\nTemizlenecek sahte plan bulunamadı. Her şey zaten temel periyotlarda.")

        # Tolerans önizlemesi: -%5 / +%10 olmayan yüzde bazlı planlar
        tol_targets = [
            p for p in db.query(models.MaintenancePlan).all()
            if (getattr(p, "threshold_type", "percent") or "percent") != "km"
            and (p.lower_pct != 5.0 or p.upper_pct != 10.0)
        ]
        if tol_targets:
            print(f"\nTolerans -%5 / +%10 yapılacak plan sayısı: {len(tol_targets)}")

        if not apply:
            print("\n" + "-" * 72)
            print("Bu bir ÖNİZLEMEDİR — hiçbir değişiklik yapılmadı.")
            print("Gerçekten uygulamak için sonuna --apply ekleyin.")
            return

        # Uygula — 1) sahte planları temizle
        total = []
        for vm in vehicle_models:
            total.extend(purge_nonbase_plans(db, vm, commit=False))

        # 2) Tolerans aralığını düzelt: yüzde bazlı tüm planları -%5 / +%10 yap.
        tol_fixed = 0
        for p in db.query(models.MaintenancePlan).all():
            ttype = getattr(p, "threshold_type", "percent") or "percent"
            if ttype != "km":
                if p.lower_pct != 5.0 or p.upper_pct != 10.0:
                    p.lower_pct = 5.0
                    p.upper_pct = 10.0
                    tol_fixed += 1
        db.commit()

        print("\n" + "-" * 72)
        print(f"TAMAMLANDI — {len(total)} sahte plan işlendi:")
        for a in total:
            print(f"   • #{a['plan_id']} '{a['name']}' ({_fmt(a['interval_km'])} km) "
                  f"→ {a['action']} "
                  f"(taşınan tamamlanmış: {a['moved_completed']}, "
                  f"silinen açık iş emri: {a['removed_open']}, "
                  f"temel: {_fmt(a['canonical_km'])} km)")
        print(f"\nTolerans aralığı -%5 / +%10 yapıldı: {tol_fixed} plan güncellendi.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
