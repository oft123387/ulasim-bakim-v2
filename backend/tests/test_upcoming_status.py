"""Araçlar sayfasındaki 4 durumlu ("sonraki ilk bakım") sınıflandırmasını
doğrular — kullanıcının onayladığı somut tanım:
  Yaklaşıyor    : hedefe kalan km <= 5.000 (APPROACHING_WITHIN_KM), ama
                  henüz "bakım takip periyodu" (plan_window/PREVIEW pct,
                  varsayılan -%5/+%10, MAX_TOLERANCE_KM'de tavanlanır)
                  penceresine girmemiş.
  Planlanacak   : bakım takip periyodu penceresi içinde VE henüz bir iş
                  emri açılıp başlatılmamış (is_activated=False/hiç yok).
  Planlandı     : aynı pencere içinde VE zaten bir iş emri açılıp
                  başlatılmış (is_activated=True).
  Bakım gecikti : pencerenin de ötesine geçilmiş (hedef ciddi şekilde aşılmış).

Süre bazlı (aylık/yıllık) planlarda aynı 4 durum, km yerine gün cinsinden
(CRITICAL_WITHIN_DAYS=pencere yarı genişliği, URGENT_WITHIN_DAYS=Yaklaşıyor
dış sınırı) hesaplanır — bkz. services._classify_upcoming_status.

Bir plan interval_km=10.000 (threshold_type='percent', lower_pct=5/upper_pct=10
varsayılanlarıyla) için pencere [hedef-500, hedef+1000]'dir. Testler bu somut
sayılarla her 4 durumu ve None (henüz hiçbir uyarı yok) durumunu doğrudan
services.compute_upcoming_maintenances üzerinden kontrol eder."""

from app import models, services
from app.database import SessionLocal

from .conftest import auth


def _make_vehicle_plan(client, admin, code, model, current_km, interval_km=10_000):
    v = client.post(
        "/api/vehicles", headers=auth(admin),
        json={"code": code, "name": "Durum Test Aracı", "current_km": current_km, "model": model},
    ).json()
    assert "id" in v, v
    # Bu testler 4 durumlu SINIFLANDIRMAYI izole doğrular. Başlangıç (baseline)
    # km kuralı, hedefi güncel km'nin altında kalan senaryoları (gecikti) gizler;
    # sınıflandırma mantığını baseline'dan bağımsız test edebilmek için baseline
    # sıfırlanır. (Baseline davranışı ayrıca test_baseline_km.py'de doğrulanır.)
    _db = SessionLocal()
    try:
        _db.query(models.Vehicle).filter_by(id=v["id"]).update({models.Vehicle.baseline_km: None})
        _db.commit()
    finally:
        _db.close()
    plan = client.post(
        "/api/plans", headers=auth(admin),
        json={"name": f"{model} Planı", "plan_type": "km", "interval_km": interval_km, "vehicle_model": model},
    ).json()
    assert "id" in plan, plan
    return v, plan


def _cleanup(client, admin, vehicles, plans):
    for v in vehicles:
        client.delete(f"/api/vehicles/{v['id']}", headers=auth(admin))
    for p in plans:
        client.delete(f"/api/plans/{p['id']}", headers=auth(admin))


def _status_for(db, vehicle_id, plan_id):
    v = db.query(models.Vehicle).filter_by(id=vehicle_id).one()
    p = db.query(models.MaintenancePlan).filter_by(id=plan_id).one()
    results = services.compute_upcoming_maintenances(db, vehicles=[v], plans=[p], include_open=True)
    r = next(x for x in results if x["vehicle"].id == vehicle_id)
    return r


def test_status_none_when_far_from_target(client, admin_token):
    """Hedefe kalan km 5.000'in üzerindeyse hiçbir uyarı (status=None).

    NOT: henüz açılmamış (preview) bir aday bu kadar uzaktaysa zaten hiç
    "yaklaşan" listesine girmez (bkz. test_status_none_candidate_not_listed_
    when_far_and_not_started). Bu testte status=None'ı GÖZLEMLEYEBİLMEK için
    (bir dict olarak dönüp status alanının None olduğunu görmek için) elle
    (manuel, dolayısıyla zaten aktif/başlatılmış) bir iş emri kullanıyoruz —
    başlatılmış iş emirleri önizleme penceresinden bağımsız her zaman
    listede kalır (bkz. compute_upcoming_maintenances)."""
    admin = admin_token
    v, plan = _make_vehicle_plan(client, admin, "ST-NONE", "StatusNoneModel", current_km=4_500)
    try:
        m = client.post(
            "/api/maintenances/manual", headers=auth(admin),
            json={"vehicle_id": v["id"], "plan_id": plan["id"], "target_km": 10_000},
        ).json()
        assert "id" in m, m
        db = SessionLocal()
        try:
            r = _status_for(db, v["id"], plan["id"])
            assert r["status"] is None
        finally:
            db.close()
    finally:
        _cleanup(client, admin, [v], [plan])


def test_none_candidate_not_listed_when_far_and_not_started(client, admin_token):
    """Henüz açılmamış (preview) bir aday, hedefe 5.000 km'den daha uzaktaysa
    (APPROACHING_WITHIN_KM dışında) Araçlar sayfası sonuçlarında HİÇ
    görünmemeli (henüz "gösterilecek bir şey yok")."""
    admin = admin_token
    v, plan = _make_vehicle_plan(client, admin, "ST-NONE2", "StatusNoneNotListedModel", current_km=4_500)
    try:
        db = SessionLocal()
        try:
            vh = db.query(models.Vehicle).filter_by(id=v["id"]).one()
            p = db.query(models.MaintenancePlan).filter_by(id=plan["id"]).one()
            results = services.compute_upcoming_maintenances(db, vehicles=[vh], plans=[p], include_open=True)
            assert all(x["vehicle"].id != v["id"] for x in results)
        finally:
            db.close()
    finally:
        _cleanup(client, admin, [v], [plan])


def test_status_yaklasiyor_between_window_and_5000km(client, admin_token):
    """Pencerenin (hedef-500) dışında ama 5.000 km içindeyse Yaklaşıyor."""
    admin = admin_token
    # target=10.000, kalan 3.000 km (current_km=7.000) -> pencere dışı (>500) ama <=5000.
    v, plan = _make_vehicle_plan(client, admin, "ST-YAK", "StatusYaklasiyorModel", current_km=7_000)
    try:
        db = SessionLocal()
        try:
            r = _status_for(db, v["id"], plan["id"])
            assert r["status"] == "yaklasiyor"
        finally:
            db.close()
    finally:
        _cleanup(client, admin, [v], [plan])


def test_status_planlanacak_within_window_not_started(client, admin_token):
    """Pencere içinde (hedef ± [500,1000]) ve henüz iş emri açılmamışsa Planlanacak."""
    admin = admin_token
    # target=10.000, current_km=9.800 -> kalan 200 km, pencere [9.500,11.000] içinde.
    v, plan = _make_vehicle_plan(client, admin, "ST-PLK", "StatusPlanlanacakModel", current_km=9_800)
    try:
        db = SessionLocal()
        try:
            r = _status_for(db, v["id"], plan["id"])
            assert r["status"] == "planlanacak"
            assert r["maintenance_id"] is None
        finally:
            db.close()
    finally:
        _cleanup(client, admin, [v], [plan])


def test_status_planlandi_within_window_started(client, admin_token):
    """Aynı pencere içinde ama bir iş emri gerçekten açılıp başlatılmışsa Planlandı."""
    admin = admin_token
    v, plan = _make_vehicle_plan(client, admin, "ST-PLD", "StatusPlanlandiModel", current_km=9_800)
    try:
        m = client.post(
            "/api/maintenances/manual", headers=auth(admin),
            json={"vehicle_id": v["id"], "plan_id": plan["id"], "target_km": 10_000},
        ).json()
        assert "id" in m, m
        db = SessionLocal()
        try:
            r = _status_for(db, v["id"], plan["id"])
            assert r["status"] == "planlandi"
            assert r["maintenance_id"] == m["id"]
        finally:
            db.close()
    finally:
        _cleanup(client, admin, [v], [plan])


def test_status_gecikti_past_window_regardless_of_started(client, admin_token):
    """Pencerenin ötesine (hedef+1000'i aşan) geçilmişse Bakım gecikti —
    iş emri hiç açılmamış olsa bile."""
    admin = admin_token
    # target=10.000, current_km=11.500 -> kalan -1.500, pencere üst sınırı
    # (11.000) aşılmış.
    v, plan = _make_vehicle_plan(client, admin, "ST-GEC", "StatusGeciktiModel", current_km=11_500)
    try:
        db = SessionLocal()
        try:
            r = _status_for(db, v["id"], plan["id"])
            assert r["status"] == "gecikti"
        finally:
            db.close()
    finally:
        _cleanup(client, admin, [v], [plan])


def test_status_gecikti_even_when_never_opened_far_overdue(client, admin_token):
    """Bir iş emri HİÇ açılmamış olsa bile, araç periyodu ciddi şekilde
    aşmışsa (ör. bir tam periyot içinde çok geride kalmış) yine Bakım
    gecikti gösterilmeli — önceki (dar) önizleme penceresi bu adayı
    tamamen listeden düşürüyordu, artık düşürmemeli."""
    admin = admin_token
    # interval=10.000, current_km=19.500 -> en yakın "floor" hedef=10.000,
    # kalan -9.500 (pencere üst sınırı 11.000'i çok aşmış). Hiç Maintenance
    # kaydı YOK (never opened).
    v, plan = _make_vehicle_plan(client, admin, "ST-FarGec", "StatusFarGeciktiModel", current_km=19_500)
    try:
        db = SessionLocal()
        try:
            r = _status_for(db, v["id"], plan["id"])
            assert r["status"] == "gecikti"
            assert r["maintenance_id"] is None
        finally:
            db.close()
    finally:
        _cleanup(client, admin, [v], [plan])


def test_status_time_based_all_four_states(client, admin_token):
    """Süre bazlı (aylık) planlarda aynı 4 durum gün cinsinden (bkz.
    CRITICAL_WITHIN_DAYS=7 pencere, URGENT_WITHIN_DAYS=14 Yaklaşıyor dış
    sınırı) doğru hesaplanmalı."""
    from datetime import date, timedelta

    admin = admin_token
    today = date.today()

    def _setup(code, model, months_ago_commissioned):
        v = client.post(
            "/api/vehicles", headers=auth(admin),
            json={
                "code": code, "name": "Süre Durum Test Aracı", "model": model,
                "commissioning_date": (today - timedelta(days=months_ago_commissioned)).isoformat(),
            },
        ).json()
        assert "id" in v, v
        plan = client.post(
            "/api/plans", headers=auth(admin),
            json={"plan_type": "time", "interval_months": 12, "vehicle_model": model},
        ).json()
        assert "id" in plan, plan
        return v, plan

    # 12 aylık periyot ~365 gün. Yaklaşık 355 gün önce devreye alınmış bir
    # araç, hedefe ~10 gün kala olur -> pencerenin (±7 gün) DIŞINDA ama
    # Yaklaşıyor'un (14 gün) İÇİNDE.
    v1, p1 = _setup("ST-T-YAK", "StatusTimeYaklasiyorModel", 355)
    # ~362 gün önce -> hedefe ~3 gün kala -> pencere (±7 gün) İÇİNDE, henüz
    # iş emri yok -> Planlanacak.
    v2, p2 = _setup("ST-T-PLK", "StatusTimePlanlanacakModel", 362)
    # ~380 gün önce -> hedef ~15 gün geçmiş -> pencerenin (7 gün) ötesinde -> Gecikti.
    v3, p3 = _setup("ST-T-GEC", "StatusTimeGeciktiModel", 380)

    def _time_status_for(db, vehicle_id, plan_id):
        v = db.query(models.Vehicle).filter_by(id=vehicle_id).one()
        p = db.query(models.MaintenancePlan).filter_by(id=plan_id).one()
        results = services.compute_upcoming_time_maintenances(db, vehicles=[v], plans=[p], include_open=True)
        return next(x for x in results if x["vehicle"].id == vehicle_id)

    vehicles, plans = [v1, v2, v3], [p1, p2, p3]
    try:
        db = SessionLocal()
        try:
            r1 = _time_status_for(db, v1["id"], p1["id"])
            assert r1["status"] == "yaklasiyor"

            r2 = _time_status_for(db, v2["id"], p2["id"])
            assert r2["status"] == "planlanacak"
            assert r2["maintenance_id"] is None

            r3 = _time_status_for(db, v3["id"], p3["id"])
            assert r3["status"] == "gecikti"
        finally:
            db.close()
    finally:
        _cleanup(client, admin, vehicles, plans)
