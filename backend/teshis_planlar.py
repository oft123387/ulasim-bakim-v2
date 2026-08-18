"""Tanı betiği — bakım planlarını denetler ve otomatik açmayı bozan planları bulur.

NEDEN GEREKLİ
    services.check_and_open_maintenances aday bakım hedeflerini, o araca
    uygulanan TÜM plan periyotlarının EBOB'una (en büyük ortak bölen) göre
    üretir. Periyotlardan biri diğerlerine tam bölünmüyorsa EBOB beklenenden
    küçük çıkar; adaylar gerçek plan katlarına denk gelmez ve o araçta bakım
    HİÇ açılmaz.

    Örnek: 25.000 / 50.000 / ... periyotlarının arasına 720.000 km'lik bir
    plan girerse EBOB 25.000 → 5.000'e düşer. 205.000 ve 210.000 aday olur,
    ikisi de hiçbir plana bölünmez, 200.000 hedefi hiç değerlendirilmez.

Bu betik hiçbir şey değiştirmez, yalnızca okur ve raporlar.

KULLANIM
    docker compose exec app python teshis_planlar.py
    docker compose exec app python teshis_planlar.py "Bozankaya Tip-2"
"""

import math
import sys

from app import models, services
from app.database import SessionLocal


def _fmt(n) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}".replace(",", ".")


def _gcd_list(vals: list[int]) -> int:
    g = 0
    for v in vals:
        g = math.gcd(g, v)
    return g


def rapor_model(db, model_adi: str, arac_sayisi: int) -> None:
    plans = db.query(models.MaintenancePlan).filter_by(is_active=True).all()
    uygulanan = [
        p for p in plans
        if p.interval_km > 0
        and not p.is_catalog_only
        and services._model_matches(p.vehicle_model, model_adi)
    ]
    print("\n" + "=" * 78)
    print(f"MODEL: {model_adi or '(model belirtilmemiş)'}   —   {arac_sayisi} araç")
    print("=" * 78)
    if not uygulanan:
        print("✗ Bu modele uygulanan aktif km planı YOK — hiç bakım açılmaz.")
        return

    intervals = [p.interval_km for p in uygulanan]
    ebob = _gcd_list(intervals)
    en_kucuk = min(intervals)

    print(f"Uygulanan plan sayısı : {len(uygulanan)}")
    print(f"En küçük periyot      : {_fmt(en_kucuk)} km")
    print(f"Aday üretim birimi    : {_fmt(ebob)} km  (tüm periyotların EBOB'u)")

    if ebob == en_kucuk:
        print("\n✓ SAĞLIKLI — EBOB en küçük periyoda eşit, adaylar gerçek plan")
        print("  katlarına denk geliyor. Otomatik açma normal çalışır.")
    else:
        print("\n" + "!" * 78)
        print(f"✗ SORUN: EBOB ({_fmt(ebob)}) en küçük periyottan ({_fmt(en_kucuk)}) KÜÇÜK.")
        print("  Aday hedefler gerçek plan katlarına denk gelmiyor —")
        print("  bu modeldeki araçlarda bakım açılmayabilir.")
        print("!" * 78)

        # Asıl suçlu: periyodu EN KÜÇÜK periyoda tam bölünmeyen plan.
        # (Sadece "çıkarınca EBOB yükseliyor" kriteri yanıltıcı olur — en
        # küçük planı çıkarmak da EBOB'u yükseltir ama o plan masum.)
        suclular = []
        for p in uygulanan:
            if p.interval_km % en_kucuk != 0:
                digerleri = [q.interval_km for q in uygulanan if q.id != p.id]
                suclular.append((p, _gcd_list(digerleri) if digerleri else 0))
        if suclular:
            print("\nEBOB'u düşüren plan(lar):")
            for p, yeni_ebob in suclular:
                print(f"   • #{p.id} '{p.name}'  periyot {_fmt(p.interval_km)} km")
                print(f"     model alanı : "
                      f"{p.vehicle_model or '(boş = TÜM araçlara uygulanır)'}")
                print(f"     plan_type   : {p.plan_type}   "
                      f"is_catalog_only: {p.is_catalog_only}")
                print(f"     → bu plan çıkarılırsa EBOB {_fmt(ebob)} yerine "
                      f"{_fmt(yeni_ebob)} olur")
                if p.interval_km % en_kucuk != 0:
                    print(f"     → {_fmt(p.interval_km)}, en küçük periyoda "
                          f"({_fmt(en_kucuk)}) TAM BÖLÜNMÜYOR — asıl sebep bu.")
        else:
            print("\n(Tek bir suçlu plan bulunamadı — birden fazla plan birlikte "
                  "EBOB'u düşürüyor olabilir.)")

    print("\nBu modele uygulanan planlar:")
    for p in sorted(uygulanan, key=lambda x: x.interval_km):
        isaret = " ←" if p.interval_km % en_kucuk != 0 else ""
        model_txt = p.vehicle_model or "GENEL"
        print(f"   {_fmt(p.interval_km):>12} km  {p.name:<24} [{model_txt}]{isaret}")


def main() -> None:
    hedef_model = sys.argv[1] if len(sys.argv) > 1 else None
    db = SessionLocal()
    try:
        # Filodaki gerçek araç modelleri
        rows = (
            db.query(models.Vehicle.model, )
            .all()
        )
        sayim: dict[str, int] = {}
        for (m,) in rows:
            sayim[m or ""] = sayim.get(m or "", 0) + 1

        print("=" * 78)
        print("BAKIM PLANI DENETİMİ")
        print("=" * 78)
        toplam_plan = db.query(models.MaintenancePlan).count()
        aktif = db.query(models.MaintenancePlan).filter_by(is_active=True).count()
        katalog = db.query(models.MaintenancePlan).filter_by(is_catalog_only=True).count()
        print(f"Toplam plan: {toplam_plan}   aktif: {aktif}   "
              f"yalnızca-katalog: {katalog}")
        ozet = ", ".join(
            "{} ({})".format(k or "(boş)", v) for k, v in sorted(sayim.items())
        )
        print(f"Filodaki araç modelleri: {ozet}")

        for model_adi, adet in sorted(sayim.items()):
            if hedef_model is not None and model_adi != hedef_model:
                continue
            rapor_model(db, model_adi, adet)
    finally:
        db.close()


if __name__ == "__main__":
    main()
