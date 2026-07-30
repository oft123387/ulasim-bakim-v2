"""Kritiklik eşiklerinin, planın periyoduna (interval_km) ORANLI olduğunu
doğrular: kalan km periyodun %15'inin altına düşünce "kritik" (urgent),
%5'inin altına düşünce "çok kritik" (critical) olmalı — sabit km/gün
eşikleri DEĞİL (bkz. services.KRITIK_PCT_OF_INTERVAL / COK_KRITIK_PCT_OF_INTERVAL).

Kullanıcının onayladığı somut örnek: 1M (10.000 km) periyodunda 1500 km
kalınca kritik, 500 km kalınca çok kritik. Bu test hem 1M hem de 6M (60.000
km, tam 6 katı) için aynı ORANIN geçerli olduğunu (mutlak km eşiğinin değil)
doğrular.

NOT: doğrudan bir bakım iş emri (manuel açılış) üzerinden test ediyoruz —
henüz açılmamış (yalnızca "yaklaşan" önizleme) adaylar ayrı, dar bir
önizleme penceresinden (PREVIEW_LOWER/UPPER_PCT, bkz. services.py) geçtiği
için gerçek bir iş emrinin (Bekliyor) tüm ömrü boyunca eşiklerin doğru
davrandığını görmek için bu daha gerçekçi ve doğrudan bir yoldur.

ÖNEMLİ: bu testler, oluşturdukları araç/planı sonunda MUTLAKA silerler
(finally bloğu). Paylaşılan (session scope) test veritabanında çalıştıkları
için temizlenmezlerse, /api/dashboard gibi TÜM aktif araç/planı tarayan
geniş kapsamlı testlerin (ör. test_workflow.py) "yaklaşanlar" penceresi
sıralamasını/limitini bozarlar (küçük remaining_km'li bu test araçları
sıralamada öne geçip gerçek testin beklediği aracı top-8'den düşürebilir)."""

from app import services
from app.database import SessionLocal
from app import models

from .conftest import auth


def _open_maintenance(client, admin, code, model, interval_km, current_km):
    r = client.post(
        "/api/vehicles", headers=auth(admin),
        json={"code": code, "name": "Kritiklik Test Aracı", "current_km": current_km, "model": model},
    )
    assert r.status_code == 200, r.text
    vehicle = r.json()

    r = client.post(
        "/api/plans", headers=auth(admin),
        json={"name": f"{model} Planı", "plan_type": "km", "interval_km": interval_km, "vehicle_model": model},
    )
    assert r.status_code == 200, r.text
    plan = r.json()

    r = client.post(
        "/api/maintenances/manual", headers=auth(admin),
        json={"vehicle_id": vehicle["id"], "plan_id": plan["id"], "target_km": interval_km},
    )
    assert r.status_code == 200, r.text
    return vehicle, plan


def _cleanup(client, admin, vehicles, plans):
    for v in vehicles:
        client.delete(f"/api/vehicles/{v['id']}", headers=auth(admin))
    for p in plans:
        client.delete(f"/api/plans/{p['id']}", headers=auth(admin))


def test_thresholds_are_proportional_not_fixed(client, admin_token):
    admin = admin_token

    # 1M periyot (10.000 km): kalan 1600 km -> ne kritik ne çok kritik.
    # kalan 1400 km -> kritik (urgent) ama çok kritik değil (%15=1500, %5=500).
    # kalan 400 km -> çok kritik (critical) (%5=500 altında).
    v_ok, p_ok = _open_maintenance(client, admin, "CT-OK", "Kritiklik1MModelA", 10_000, 10_000 - 1_600)
    v_kritik, p_kritik = _open_maintenance(client, admin, "CT-KR", "Kritiklik1MModelB", 10_000, 10_000 - 1_400)
    v_cok_kritik, p_cok_kritik = _open_maintenance(client, admin, "CT-CK", "Kritiklik1MModelC", 10_000, 10_000 - 400)

    try:
        db = SessionLocal()
        try:
            vehicles = [
                db.query(models.Vehicle).filter_by(id=v_ok["id"]).one(),
                db.query(models.Vehicle).filter_by(id=v_kritik["id"]).one(),
                db.query(models.Vehicle).filter_by(id=v_cok_kritik["id"]).one(),
            ]
            plans = (
                db.query(models.MaintenancePlan)
                .filter(models.MaintenancePlan.vehicle_model.in_(
                    ["Kritiklik1MModelA", "Kritiklik1MModelB", "Kritiklik1MModelC"]
                ))
                .all()
            )
            results = services.compute_upcoming_maintenances(db, vehicles=vehicles, plans=plans, include_open=True)
            by_vehicle = {r["vehicle"].id: r for r in results}

            r_ok = by_vehicle[v_ok["id"]]
            assert r_ok["urgent"] is False
            assert r_ok["critical"] is False

            r_kritik = by_vehicle[v_kritik["id"]]
            assert r_kritik["urgent"] is True
            assert r_kritik["critical"] is False

            r_cok_kritik = by_vehicle[v_cok_kritik["id"]]
            assert r_cok_kritik["urgent"] is True
            assert r_cok_kritik["critical"] is True
        finally:
            db.close()
    finally:
        _cleanup(client, admin, [v_ok, v_kritik, v_cok_kritik], [p_ok, p_kritik, p_cok_kritik])


def test_thresholds_scale_with_period_6x_period_uses_same_percentage(client, admin_token):
    admin = admin_token

    # 6M periyot (60.000 km): %15 = 9000 km, %5 = 3000 km.
    # Sabit (1M'deki mutlak) eşikler olsaydı (1500/500), kalan 8000 km
    # "kritik değil" sayılırdı — oranlı kuralda ise kritik OLMALI.
    v_scaled, p_scaled = _open_maintenance(client, admin, "CT-6M", "Kritiklik6MModel", 60_000, 60_000 - 8_000)

    try:
        db = SessionLocal()
        try:
            vehicle = db.query(models.Vehicle).filter_by(id=v_scaled["id"]).one()
            plan = db.query(models.MaintenancePlan).filter_by(vehicle_model="Kritiklik6MModel").one()
            results = services.compute_upcoming_maintenances(
                db, vehicles=[vehicle], plans=[plan], include_open=True
            )
            r = next(x for x in results if x["vehicle"].id == v_scaled["id"])
            # 8000 km kalan, 1M'nin sabit eşiğiyle (1500) kritik OLMAZDI;
            # oranlı kuralda (%15 * 60.000 = 9000) kritik OLMALI.
            assert r["urgent"] is True
            assert r["critical"] is False  # %5*60.000=3000 altında değil
        finally:
            db.close()
    finally:
        _cleanup(client, admin, [v_scaled], [p_scaled])


def test_very_critical_stays_true_once_past_the_5pct_threshold(client, admin_token):
    """Çok kritik durumu kalıcıdır: km yalnızca artabildiği için remaining_km
    de yalnızca azalır — bir kez %5 eşiğinin altına düşünce bir daha
    yukarı çıkamaz (araç km'si azalmadığı sürece)."""
    admin = admin_token
    vehicle, plan = _open_maintenance(client, admin, "CT-STK", "KritiklikStickyModel", 10_000, 10_000 - 300)

    try:
        r = client.put(
            f"/api/vehicles/{vehicle['id']}/km", headers=auth(admin),
            json={"current_km": vehicle["current_km"] + 50},
        )
        assert r.status_code == 200, r.text

        db = SessionLocal()
        try:
            v = db.query(models.Vehicle).filter_by(id=vehicle["id"]).one()
            p = db.query(models.MaintenancePlan).filter_by(id=plan["id"]).one()
            results = services.compute_upcoming_maintenances(db, vehicles=[v], plans=[p], include_open=True)
            r_now = next(x for x in results if x["vehicle"].id == vehicle["id"])
            assert r_now["critical"] is True
        finally:
            db.close()
    finally:
        _cleanup(client, admin, [vehicle], [plan])
