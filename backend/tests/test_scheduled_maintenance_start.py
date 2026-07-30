"""'Başlatılmayı Bekleyenler' listesinde ileri tarihli otomatik başlatma
(scheduled_start_at) için testler.

Kullanıcı isteği: "Başlat" derken bir tarih seçilebilsin (ör. 2-3 gün sonra
veya ertesi gün), ne zaman seçilirse o tarihte iş emri otomatik başlasın.
POST /api/maintenances/{id}/start artık isteğe bağlı bir scheduled_start_at
alır: boşsa ya da bugüne eşit/öncesiyse eski davranış (ANINDA başlatma)
uygulanır; bugünden ileriyse iş emri hemen başlamaz, yalnızca tarihi
kaydedilir ve arka plan zamanlayıcısı (services.run_scheduled_maintenance_
starts) o tarih geldiğinde otomatik başlatır. Zamanlamayı iptal etme
(cancel-schedule) ve elle tetikleme (run-scheduled-starts, esas olarak test
amaçlı) uç noktaları da burada test edilir."""

from datetime import date, timedelta

from app import models
from app.database import SessionLocal

from .conftest import auth


def _make_pending_candidate(client, admin, model):
    """interval_km=10_000, lower_pct/upper_pct varsayılan (%5/%10, en fazla
    3.000 km'de tavanlanır) -> tolerans penceresi [9.500, 11.000]. Aracı bu
    pencerenin içinde ama hedefe henüz ulaşmamış (9.800 km) oluşturmak,
    araç oluşturmadaki skip_passed_targets=True kuralına takılmadan otomatik
    bir aday (is_activated=False) açılmasını sağlar."""
    plan = client.post(
        "/api/plans", headers=auth(admin),
        json={"plan_type": "km", "interval_km": 10_000, "vehicle_model": model},
    ).json()
    v = client.post(
        "/api/vehicles", headers=auth(admin),
        json={"code": f"SCH-{model}", "name": "Zamanlama Test Aracı", "current_km": 9_800, "model": model},
    ).json()
    pend = client.get("/api/maintenances/pending-start", headers=auth(admin)).json()
    cand = next(p for p in pend if p["vehicle"]["id"] == v["id"])
    return v, plan, cand


def _cleanup(client, admin, v, plan):
    client.delete(f"/api/vehicles/{v['id']}", headers=auth(admin))
    client.delete(f"/api/plans/{plan['id']}", headers=auth(admin))


def test_start_with_future_date_only_schedules_does_not_activate(client, admin_token):
    admin = admin_token
    v, plan, cand = _make_pending_candidate(client, admin, "SchedModelA")
    try:
        future = (date.today() + timedelta(days=3)).isoformat()
        r = client.post(
            f"/api/maintenances/{cand['id']}/start", headers=auth(admin),
            json={"scheduled_start_at": future},
        )
        assert r.status_code == 200, r.text
        m = r.json()
        assert m["is_activated"] is False
        assert m["scheduled_start_at"] == future

        # Hâlâ pending-start listesinde olmalı (henüz gerçekten başlamadı).
        pend = client.get("/api/maintenances/pending-start", headers=auth(admin)).json()
        assert any(p["id"] == cand["id"] for p in pend)
    finally:
        _cleanup(client, admin, v, plan)


def test_start_without_date_activates_immediately_as_before(client, admin_token):
    """Geriye dönük uyumluluk: gövde göndermeden çağıran eski istemciler
    (ör. test_workflow.py) hâlâ anında başlatma davranışını almalı."""
    admin = admin_token
    v, plan, cand = _make_pending_candidate(client, admin, "SchedModelB")
    try:
        r = client.post(f"/api/maintenances/{cand['id']}/start", headers=auth(admin))
        assert r.status_code == 200, r.text
        m = r.json()
        assert m["is_activated"] is True
        assert m["scheduled_start_at"] is None
    finally:
        _cleanup(client, admin, v, plan)


def test_start_with_past_or_today_date_activates_immediately(client, admin_token):
    admin = admin_token
    v, plan, cand = _make_pending_candidate(client, admin, "SchedModelC")
    try:
        today = date.today().isoformat()
        r = client.post(
            f"/api/maintenances/{cand['id']}/start", headers=auth(admin),
            json={"scheduled_start_at": today},
        )
        assert r.status_code == 200, r.text
        assert r.json()["is_activated"] is True
    finally:
        _cleanup(client, admin, v, plan)


def test_cancel_schedule_clears_scheduled_start_at(client, admin_token):
    admin = admin_token
    v, plan, cand = _make_pending_candidate(client, admin, "SchedModelD")
    try:
        future = (date.today() + timedelta(days=2)).isoformat()
        client.post(
            f"/api/maintenances/{cand['id']}/start", headers=auth(admin),
            json={"scheduled_start_at": future},
        )
        r = client.post(f"/api/maintenances/{cand['id']}/cancel-schedule", headers=auth(admin))
        assert r.status_code == 200, r.text
        m = r.json()
        assert m["scheduled_start_at"] is None
        assert m["is_activated"] is False
    finally:
        _cleanup(client, admin, v, plan)


def test_cancel_schedule_requires_existing_schedule(client, admin_token):
    admin = admin_token
    v, plan, cand = _make_pending_candidate(client, admin, "SchedModelE")
    try:
        r = client.post(f"/api/maintenances/{cand['id']}/cancel-schedule", headers=auth(admin))
        assert r.status_code == 400
    finally:
        _cleanup(client, admin, v, plan)


def test_run_scheduled_starts_activates_due_maintenances(client, admin_token):
    admin = admin_token
    v, plan, cand = _make_pending_candidate(client, admin, "SchedModelF")
    try:
        # Uç nokta bugünden ileri olmayan tarihleri hemen aktive ettiği için,
        # "geçmişte kalmış ama hâlâ zamanlanmış" durumunu (ör. sunucu kapalıyken
        # tarihi geçmiş) simüle etmek amacıyla veritabanına doğrudan yazıyoruz.
        db = SessionLocal()
        try:
            m = db.query(models.Maintenance).filter_by(id=cand["id"]).one()
            m.scheduled_start_at = date.today() - timedelta(days=1)
            db.commit()
        finally:
            db.close()

        r = client.post("/api/maintenances/run-scheduled-starts", headers=auth(admin))
        assert r.status_code == 200, r.text
        assert r.json()["started"] >= 1

        detail = client.get(f"/api/maintenances/{cand['id']}", headers=auth(admin)).json()
        assert detail["is_activated"] is True
        assert detail["scheduled_start_at"] is None
    finally:
        _cleanup(client, admin, v, plan)


def test_run_scheduled_starts_requires_admin(client, usta_token):
    r = client.post("/api/maintenances/run-scheduled-starts", headers=auth(usta_token))
    assert r.status_code == 403


def test_cancel_schedule_requires_permission(client, usta_token):
    r = client.post("/api/maintenances/1/cancel-schedule", headers=auth(usta_token))
    assert r.status_code == 403
