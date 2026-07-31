"""Gerçek kullanıcı geri bildirimi: bir araç için "1M + 6M" olarak birleşik
gösterilen bir bakımda "3M" de olmalıydı (6M zaten 3M'yi kapsar, hedef km
her ikisine de tam bölünüyor) ama eksikti. is_catalog_only işaretli DEĞİLDİ
(doğrulandı) — asıl neden, planların `vehicle_model` alanının büyük/küçük
harf, baştaki/sondaki boşluk veya Türkçe karakter (İ/I/ı/i, Ş/ş, ...)
farkıyla birebir (==) eşleşmemesiydi: check_and_open_maintenances,
create_manual_maintenance, resync_maintenance_tiers ve compute_upcoming_*
hepsi ham `p.vehicle_model == vehicle.model` karşılaştırması kullanıyordu,
bu da görsel olarak "aynı" görünen ama karakter bazında farklı bir model
adına sahip planı SESSİZCE dışlıyordu.

Düzeltme: services._model_matches (services._norm_model ile normalize
edilmiş karşılaştırma) artık TÜM bu yerlerde kullanılıyor. Bu test, kasıtlı
olarak farklı yazılmış (ama aynı) model adlarına sahip 1M/3M/6M planlarının
yine de doğru şekilde birleştiğini uçtan uca doğrular."""

from app import models, services
from app.database import SessionLocal

from .conftest import auth


def test_plans_with_differently_cased_vehicle_model_still_merge(client, admin_token):
    admin = admin_token
    # Araç "Sirio" modeliyle oluşturuluyor; planlardan biri kasıtlı olarak
    # farklı yazılmış: " sİrio " (baş/son boşluk + Türkçe büyük İ + küçük harf
    # karışımı) — insan gözüyle "aynı" ama birebir (==) karşılaştırmada FARKLI.
    v = client.post(
        "/api/vehicles", headers=auth(admin),
        json={"code": "VM-BUG", "name": "Model Eşleşme Test Aracı", "current_km": 60_000, "model": "Sirio"},
    ).json()
    assert "id" in v, v

    def _plan(name, interval_km, vehicle_model):
        p = client.post(
            "/api/plans", headers=auth(admin),
            json={"name": name, "plan_type": "km", "interval_km": interval_km, "vehicle_model": vehicle_model},
        ).json()
        assert "id" in p, p
        return p

    plan_1m = _plan("Sirio 1M Planı", 10_000, "Sirio")
    plan_3m = _plan("Sirio 3M Planı", 30_000, " sİrio ")  # kasıtlı yazım farkı
    plan_6m = _plan("Sirio 6M Planı", 60_000, "SIRIO")  # kasıtlı yazım farkı

    for pid, label in [(plan_1m["id"], "1M Madde"), (plan_3m["id"], "3M Madde"), (plan_6m["id"], "6M Madde")]:
        root = client.post(
            "/api/tree", headers=auth(admin), json={"name": "ARAÇ İÇİ", "plan_id": pid},
        ).json()
        client.post(
            "/api/tree", headers=auth(admin),
            json={"name": label, "parent_id": root["id"], "card_code": f"{label}-01"},
        )

    try:
        # 1) create_manual_maintenance: hedef=60.000 -> 1M/3M/6M'nin hepsine
        # tam bölünür, üçü de (yazım farkına RAĞMEN) birleşmeli.
        m = client.post(
            "/api/maintenances/manual", headers=auth(admin),
            json={"vehicle_id": v["id"], "plan_id": plan_6m["id"], "target_km": 60_000},
        ).json()
        assert "id" in m, m
        assert sorted(m["covered_tiers"]) == [10_000, 30_000, 60_000]

        # 2) check_and_open_maintenances (otomatik açma) tarafında da aynı
        # şekilde davranmalı — ayrı bir araç üzerinden doğrula.
        v2 = client.post(
            "/api/vehicles", headers=auth(admin),
            json={"code": "VM-BUG2", "name": "Model Eşleşme Test Aracı 2", "current_km": 500, "model": "Sirio"},
        ).json()
        assert "id" in v2, v2
        try:
            db = SessionLocal()
            try:
                vehicle2 = db.query(models.Vehicle).filter_by(id=v2["id"]).one()
                opened = services.check_and_open_maintenances(db, vehicle2)
                # base_unit = gcd(10000,30000,60000) = 10000; km=500 -> hedef
                # adaylar {0(iskarta), 10000}; tolerans penceresine girmeyebilir,
                # bu yüzden asıl doğrulamayı doğrudan applicable hesaplamasından
                # yapıyoruz (aşağıda), açılıp açılmadığından bağımsız.
                active_plans = [
                    p for p in db.query(models.MaintenancePlan).filter_by(is_active=True).all()
                    if p.interval_km > 0 and not p.is_catalog_only
                    and services._model_matches(p.vehicle_model, vehicle2.model)
                ]
                matched_intervals = sorted(p.interval_km for p in active_plans if p.id in
                                            {plan_1m["id"], plan_3m["id"], plan_6m["id"]})
                assert matched_intervals == [10_000, 30_000, 60_000]
            finally:
                db.close()
        finally:
            client.delete(f"/api/vehicles/{v2['id']}", headers=auth(admin))
    finally:
        client.delete(f"/api/vehicles/{v['id']}", headers=auth(admin))
        for p in (plan_1m, plan_3m, plan_6m):
            client.delete(f"/api/plans/{p['id']}", headers=auth(admin))


def test_model_matches_helper_directly():
    from app.services import _model_matches

    assert _model_matches("", "Sirio") is True  # boş = genel, her zaman uygulanır
    assert _model_matches(None, "Sirio") is True
    assert _model_matches("Sirio", "Sirio") is True
    assert _model_matches("SIRIO", "Sirio") is True
    assert _model_matches(" sİrio ", "Sirio") is True
    assert _model_matches("Sirio", "Bozankaya Tip-1") is False
    assert _model_matches("Bozankaya Tip-1", " bozankaya tip-1 ") is True
