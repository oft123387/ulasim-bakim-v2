"""Tanı betiği — "bu araçta neden bakım açılmıyor?" sorusunu adım adım cevaplar.

services.check_and_open_maintenances'ın YAPTIĞI KONTROLLERİN AYNISINI, aynı
sırayla ve her adımın sonucunu yazdırarak tekrar eder. Hiçbir şey değiştirmez,
yalnızca okur.

KULLANIM
    docker compose exec app python teshis_arac.py 3881
    docker compose exec app python teshis_arac.py 3881 3872 3801     (birden fazla)

    Docker'sız: cd backend && python teshis_arac.py 3881
"""

import math
import sys

from app import models, services
from app.database import SessionLocal


def _fmt(n) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}".replace(",", ".")


def teshis(db, kod: str) -> None:
    v = db.query(models.Vehicle).filter_by(code=kod).first()
    if not v:
        print(f"\n### {kod}: ARAÇ BULUNAMADI")
        return

    print("\n" + "=" * 78)
    print(f"ARAÇ {v.code}  ({v.name or 'isimsiz'})")
    print("=" * 78)
    print(f"Model          : {v.model or '(belirtilmemiş)'}")
    print(f"Mevcut km      : {_fmt(v.current_km)}")
    print(f"Açılış km      : {_fmt(v.baseline_km)}")

    km = v.current_km

    # --- 1) Bu araca uygulanabilir planlar -------------------------------
    plans = db.query(models.MaintenancePlan).filter_by(is_active=True).all()
    active_plans = [
        p for p in plans
        if p.interval_km > 0
        and not p.is_catalog_only
        and services._model_matches(p.vehicle_model, v.model)
    ]
    print(f"\n1) Uygulanabilir aktif km planı: {len(active_plans)} adet")
    if not active_plans:
        print("   ✗ SEBEP BULUNDU: bu araç modeline uyan aktif km planı yok.")
        print("     (plan.vehicle_model ile araç modeli eşleşmiyor olabilir)")
        return
    for p in sorted(active_plans, key=lambda x: x.interval_km):
        print(f"   • {p.name}  (periyot {_fmt(p.interval_km)} km)")

    # --- 2) Temel birim ve aday hedefler ---------------------------------
    base_unit = active_plans[0].interval_km
    for p in active_plans[1:]:
        base_unit = math.gcd(base_unit, p.interval_km)
    print(f"\n2) Temel birim (tüm periyotların EBOB'u): {_fmt(base_unit)} km")

    candidates = set()
    candidates.add((km // base_unit) * base_unit)
    candidates.add(((km // base_unit) + 1) * base_unit)
    candidates.discard(0)
    print(f"   Aday hedefler (geçilen son + bir sonraki nokta): "
          f"{', '.join(_fmt(c) for c in sorted(candidates))}")
    print("   NOT: sistem yalnızca bu iki noktaya bakar; daha eski hedefler")
    print("        hiçbir zaman aday olmaz.")

    # --- 3) Her aday için tek tek kontrol --------------------------------
    print("\n3) Adayların tek tek kontrolü:")
    acilabilir = 0
    for target in sorted(candidates):
        print(f"\n   ── Hedef {_fmt(target)} km " + "─" * 40)
        fark = km - target
        if fark >= 0:
            print(f"      Araç bu hedefi {_fmt(fark)} km GEÇMİŞ.")
        else:
            print(f"      Hedefe {_fmt(-fark)} km VAR.")

        applicable = [p for p in active_plans if target % p.interval_km == 0]
        if not applicable:
            print("      ✗ Hiçbir plan bu hedefe tam bölünmüyor → aday değil.")
            continue
        governing = max(applicable, key=lambda p: p.interval_km)
        print(f"      Bölünen planlar : "
              f"{', '.join(_fmt(p.interval_km) for p in sorted(applicable, key=lambda x: x.interval_km))}")
        print(f"      Yöneten plan    : {governing.name} ({_fmt(governing.interval_km)} km)")

        # 3a) açılış km kontrolü
        if v.baseline_km is not None and target <= v.baseline_km:
            print(f"      ✗ ENGEL: hedef ({_fmt(target)}) açılış km'sinin "
                  f"({_fmt(v.baseline_km)}) altında/eşit.")
            print("        → Sistemden önce yapılmış sayılıyor, asla açılmaz.")
            print("        ÇÖZÜM: açılış km'sini düşür (set_opening_km.py --geri N)")
            continue

        # 3b) tolerans penceresi
        lower, upper = services.plan_window(governing, target)
        print(f"      Tolerans penceresi: {_fmt(lower)} – {_fmt(upper)} km")
        if km < lower:
            print(f"      ✗ ENGEL: araç ({_fmt(km)}) pencerenin ALTINDA — henüz çok erken.")
            print(f"        → Pencereye girmesine {_fmt(lower - km)} km var, sonra kendiliğinden açılır.")
            continue
        if km > upper:
            asim = km - upper
            print(f"      ✗ ENGEL: araç ({_fmt(km)}) pencerenin ÜSTÜNDE — "
                  f"{_fmt(asim)} km fazla geçmiş.")
            print(f"        Tolerans tavanı MAX_TOLERANCE_KM = "
                  f"{_fmt(services.MAX_TOLERANCE_KM)} km.")
            print("        → Bir hedef bu kadar geride kaldıysa OTOMATİK açılmaz.")
            print("        ÇÖZÜM: Bakımlar → Başlatılmayı Bekleyenler listesinde")
            print("               'gecikti' olarak görünür, oradan elle başlatılır;")
            print("               ya da araç sayfasından 'Yeni Bakım Başlat'.")
            continue

        # 3c) zaten var mı
        exists = (
            db.query(models.Maintenance)
            .filter_by(vehicle_id=v.id, plan_id=governing.id, target_km=target)
            .first()
        )
        if exists:
            durum = "başlatılmış" if exists.is_activated else "BAŞLATILMAYI BEKLİYOR"
            print(f"      ✗ ENGEL: bu bakım zaten açılmış — iş emri #{exists.id}, "
                  f"durum: {exists.status} ({durum}).")
            if not exists.is_activated:
                print("        → Bakımlar → 'Başlatılmayı Bekleyenler' sekmesinde,")
                print("          'Başlat' denmeden diğer listelerde GÖRÜNMEZ.")
            continue

        print("      ✓ Tüm kontrolleri geçiyor — bu bakım AÇILABİLİR durumda.")
        print("        (bir sonraki km güncellemesinde ya da açılışta açılır)")
        acilabilir += 1

    # --- 4) Bu araçta hâlihazırda ne var ---------------------------------
    mevcut = (
        db.query(models.Maintenance)
        .filter_by(vehicle_id=v.id)
        .order_by(models.Maintenance.target_km)
        .all()
    )
    print(f"\n4) Bu araçtaki mevcut iş emirleri: {len(mevcut)} adet")
    for m in mevcut:
        hedef = _fmt(m.target_km) + " km" if m.target_km and m.target_km > 0 else str(m.target_date)
        print(f"   • #{m.id}  hedef {hedef:>16}  durum {m.status:<12} "
              f"{'başlatıldı' if m.is_activated else 'BAŞLATILMAYI BEKLİYOR'}")

    print(f"\nSONUÇ: {acilabilir} aday açılabilir durumda.")


def main() -> None:
    kodlar = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not kodlar:
        raise SystemExit(
            "Kullanım: python teshis_arac.py <arac_kodu> [arac_kodu ...]\n"
            "Örnek   : python teshis_arac.py 3881"
        )
    db = SessionLocal()
    try:
        for kod in kodlar:
            teshis(db, kod)
    finally:
        db.close()


if __name__ == "__main__":
    main()
