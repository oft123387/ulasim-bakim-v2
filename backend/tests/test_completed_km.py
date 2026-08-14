"""Bakım tamamlanan km + sapma yüzdesi (Faz — "bakım yapılırken bakım
yapan usta veya kişi tarafından o anki güncel km girme olsun").

Kullanıcı isteği (birebir): "bizim bakımlarımız -%5 ve +%10 aralığı ya
diyelim ki 200 bin bakımı yaklaşan araç için 199.188 de bakım yapılırsa
o yapılan aralık atıyorum -%0.5 o hesaplansın ve excel indirildiğinde
gözüksün bakım geçmişi sayfasında da o yüzde yine gözüksün."

PATCH /api/maintenances/{id}/completed-km uç noktasını uçtan uca (yalnızca
services.compute_km_deviation_pct birim testini değil) doğrular: kayıt,
sapma hesabı, aracın km'sinin ileri taşınması, geriye alma engeli, süre
bazlı planlarda reddedilmesi ve teknisyen yetki kısıtı."""

from datetime import date, datetime, timedelta

import pytest

from app import models, services
from app.database import SessionLocal

from .conftest import auth


def _make_activated_km_candidate(client, admin, model, interval_km=200_000, start_km=199_000, code=None):
    """lower_pct/upper_pct varsayılan (%5/%10, en fazla 3.000 km'de
    tavanlanır) -> tolerans penceresi [interval_km-3000, interval_km+3000]
    (200.000 km planında). Aracı bu pencerenin içinde oluşturup adayı
    /start ile aktive eder (gerçek kullanım akışı: önce onay, sonra
    tamamlanan km girişi)."""
    plan = client.post(
        "/api/plans", headers=auth(admin),
        json={"plan_type": "km", "interval_km": interval_km, "vehicle_model": model},
    ).json()
    v = client.post(
        "/api/vehicles", headers=auth(admin),
        json={"code": code or f"CKM-{model}"[:20], "name": "Tamamlanan KM Test Aracı", "current_km": start_km, "model": model},
    ).json()
    assert "id" in v, v
    pend = client.get("/api/maintenances/pending-start", headers=auth(admin)).json()
    cand = next(p for p in pend if p["vehicle"]["id"] == v["id"])
    r = client.post(f"/api/maintenances/{cand['id']}/start", headers=auth(admin))
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["is_activated"] is True
    return v, plan, m


def _cleanup(client, admin, v, plan):
    client.delete(f"/api/vehicles/{v['id']}", headers=auth(admin))
    client.delete(f"/api/plans/{plan['id']}", headers=auth(admin))


def test_completed_km_computes_deviation_percentage(client, admin_token):
    """Kullanıcının verdiği örnek: hedef 200.000 km, gerçekte 199.188 km'de
    yapılmış -> sapma yaklaşık -%0.41 (negatif = erken/hedeften önce)."""
    admin = admin_token
    v, plan, m = _make_activated_km_candidate(client, admin, "CkmModelA", start_km=199_000)
    try:
        r = client.patch(
            f"/api/maintenances/{m['id']}/completed-km", headers=auth(admin),
            json={"completed_km": 199_188},
        )
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["completed_km"] == 199_188
        assert out["km_deviation_pct"] == pytest.approx(-0.41, abs=0.01)

        # Bakım Geçmişi / detay sayfasında da kalıcı olmalı.
        detail = client.get(f"/api/maintenances/{m['id']}", headers=auth(admin)).json()
        assert detail["completed_km"] == 199_188
        assert detail["km_deviation_pct"] == pytest.approx(-0.41, abs=0.01)
    finally:
        _cleanup(client, admin, v, plan)


def test_completed_km_advances_vehicle_current_km(client, admin_token):
    admin = admin_token
    v, plan, m = _make_activated_km_candidate(client, admin, "CkmModelB", start_km=199_000)
    try:
        r = client.patch(
            f"/api/maintenances/{m['id']}/completed-km", headers=auth(admin),
            json={"completed_km": 199_300},
        )
        assert r.status_code == 200, r.text
        all_vehicles = client.get("/api/vehicles", headers=auth(admin)).json()
        veh = next(x for x in all_vehicles if x["id"] == v["id"])
        assert veh["current_km"] == 199_300
    finally:
        _cleanup(client, admin, v, plan)


def test_completed_km_cannot_go_backwards(client, admin_token):
    admin = admin_token
    v, plan, m = _make_activated_km_candidate(client, admin, "CkmModelC", start_km=199_000)
    try:
        client.patch(
            f"/api/maintenances/{m['id']}/completed-km", headers=auth(admin),
            json={"completed_km": 199_300},
        )
        r = client.patch(
            f"/api/maintenances/{m['id']}/completed-km", headers=auth(admin),
            json={"completed_km": 199_100},
        )
        assert r.status_code == 400
    finally:
        _cleanup(client, admin, v, plan)


def test_completed_km_rejected_for_time_based_plan(client, admin_token):
    """Süre bazlı planların tolerans penceresi ay biriminde ve
    elapsed_months tam sayı döndürdüğü için (varsayılan %5/%10 toleransla)
    araç oluşturma anında otomatik aday açılması pratikte garanti değil —
    bu yüzden burada test_run_scheduled_starts_activates_due_maintenances'
    daki gibi doğrudan veritabanına bir süre bazlı iş emri (candidate)
    yazılır, sonra /start ile aktive edilir; asıl doğrulanan şey PATCH
    completed-km'in plan_type != "km" olduğunda 400 döndürmesi."""
    admin = admin_token
    plan = client.post(
        "/api/plans", headers=auth(admin),
        json={"plan_type": "time", "interval_months": 12, "vehicle_model": "CkmModelD"},
    ).json()
    v = client.post(
        "/api/vehicles", headers=auth(admin),
        json={
            "code": "CKM-D", "name": "Süre Bazlı Test Aracı", "model": "CkmModelD",
            "current_km": 1_000, "commissioning_date": (date.today() - timedelta(days=362)).isoformat(),
        },
    ).json()
    try:
        db = SessionLocal()
        try:
            m = models.Maintenance(
                vehicle_id=v["id"], plan_id=plan["id"],
                target_km=-12, target_date=date.today() + timedelta(days=3),
                km_at_open=v["current_km"], status="pending", is_activated=False,
            )
            db.add(m)
            db.commit()
            db.refresh(m)
            mid = m.id
        finally:
            db.close()

        started = client.post(f"/api/maintenances/{mid}/start", headers=auth(admin)).json()
        assert started["is_activated"] is True, started

        r = client.patch(
            f"/api/maintenances/{mid}/completed-km", headers=auth(admin),
            json={"completed_km": 199_188},
        )
        assert r.status_code == 400
    finally:
        _cleanup(client, admin, v, plan)


def test_technician_cannot_set_completed_km_for_others_assignment(client, admin_token, usta_token):
    admin = admin_token
    v, plan, m = _make_activated_km_candidate(client, admin, "CkmModelE", start_km=199_000)
    try:
        other = client.post(
            "/api/users", headers=auth(admin),
            json={"username": "ckm_other_usta", "full_name": "Başka Usta", "role": "technician", "password": "ckmotherpass1"},
        ).json()
        client.patch(f"/api/maintenances/{m['id']}/assign", headers=auth(admin), json={"assigned_to_id": other["id"]})

        r = client.patch(
            f"/api/maintenances/{m['id']}/completed-km", headers=auth(usta_token),
            json={"completed_km": 199_188},
        )
        assert r.status_code == 403
    finally:
        _cleanup(client, admin, v, plan)


def test_technician_can_set_completed_km_when_assigned_to_self_or_unassigned(client, admin_token, usta_token):
    admin = admin_token
    v, plan, m = _make_activated_km_candidate(client, admin, "CkmModelF", start_km=199_000)
    try:
        # Henüz kimseye atanmamış -> herhangi bir teknisyen girebilir.
        r = client.patch(
            f"/api/maintenances/{m['id']}/completed-km", headers=auth(usta_token),
            json={"completed_km": 199_188},
        )
        assert r.status_code == 200, r.text
        assert r.json()["completed_km"] == 199_188
    finally:
        _cleanup(client, admin, v, plan)


def test_force_complete_auto_captures_completed_km_when_not_entered_manually(client, admin_token):
    """Kullanıcı isteği: "şuan tamamlananlar için de tamamlandığı km
    üzerinden yine hesapla ve yaz" — usta bakım sırasında PATCH
    completed-km ile elle bir değer GİRMEDEN iş emri tamamlanırsa (burada
    admin'in "Doğrudan Tamamlandı Yap" kısayoluyla), completed_km otomatik
    olarak aracın o anki takip edilen km'sinden yazılmalı; Sapma % asla boş
    kalmamalı."""
    admin = admin_token
    v, plan, m = _make_activated_km_candidate(client, admin, "CkmModelG", start_km=199_000)
    try:
        r = client.post(f"/api/maintenances/{m['id']}/force-complete", headers=auth(admin))
        assert r.status_code == 200, r.text

        detail = client.get(f"/api/maintenances/{m['id']}", headers=auth(admin)).json()
        assert detail["status"] == "completed"
        assert detail["completed_km"] == 199_000  # araç km'si hiç değişmemişti
        assert detail["km_deviation_pct"] is not None
    finally:
        _cleanup(client, admin, v, plan)


def test_force_complete_does_not_overwrite_manually_entered_completed_km(client, admin_token):
    """Usta bakım sırasında gerçek km'yi elle girmişse (ör. 199.188), daha
    sonra force-complete ile tamamlansa bile bu değer asla otomatik aracın
    takip edilen km'siyle EZİLMEMELİ — tıpkı completed_by_id gibi "gerçek
    bir teknisyen girişi" hep önceliklidir."""
    admin = admin_token
    v, plan, m = _make_activated_km_candidate(client, admin, "CkmModelH", start_km=199_000)
    try:
        client.patch(
            f"/api/maintenances/{m['id']}/completed-km", headers=auth(admin),
            json={"completed_km": 199_188},
        )
        r = client.post(f"/api/maintenances/{m['id']}/force-complete", headers=auth(admin))
        assert r.status_code == 200, r.text

        detail = client.get(f"/api/maintenances/{m['id']}", headers=auth(admin)).json()
        assert detail["completed_km"] == 199_188
    finally:
        _cleanup(client, admin, v, plan)


def test_backfill_fills_completed_km_from_km_at_open_when_no_audit_trail(client, admin_token):
    """Bu özellik eklenmeden ÖNCE tamamlanmış (completed_km=None) bir km
    bazlı iş emrini simüle eder — audit_logs'ta o araca ait bir "KM
    güncellendi" izi yoksa (bu senaryoda yok), backfill km_at_open'a
    düşmeli."""
    admin = admin_token
    v, plan, m = _make_activated_km_candidate(client, admin, "CkmModelI", start_km=199_000)
    try:
        db = SessionLocal()
        try:
            # SQLite, silinen araçların id'sini yeniden kullanabildiği için
            # (bu test dosyasındaki önceki testler kendi araçlarını
            # oluşturup siliyor), bu id için önceki bir teste ait "sahte"
            # audit izi kalmış olabilir — "hiç audit izi yok" senaryosunu
            # gerçekten test edebilmek için bu aracın audit geçmişini
            # temizleriz (backfill fonksiyonunun kendisi DEĞİL, yalnızca bu
            # testin ön koşulu).
            db.query(models.AuditLog).filter_by(entity_type="vehicle", entity_id=v["id"]).delete()
            row = db.query(models.Maintenance).filter_by(id=m["id"]).one()
            row.status = "completed"
            row.completed_at = datetime.now()
            row.completed_km = None
            db.commit()
        finally:
            db.close()

        db = SessionLocal()
        try:
            n = services.backfill_completed_km_for_finished_maintenances(db)
            assert n >= 1
            row = db.query(models.Maintenance).filter_by(id=m["id"]).one()
            assert row.completed_km == row.km_at_open == 199_000
        finally:
            db.close()

        # İdempotent: ikinci çalıştırma bu satırı tekrar işlemez.
        db = SessionLocal()
        try:
            n2 = services.backfill_completed_km_for_finished_maintenances(db)
            row = db.query(models.Maintenance).filter_by(id=m["id"]).one()
            assert row.completed_km == 199_000
        finally:
            db.close()
    finally:
        _cleanup(client, admin, v, plan)


def test_backfill_prefers_audit_log_km_over_km_at_open(client, admin_token):
    """Aracın tamamlanma anından ÖNCE gerçek bir "KM güncellendi" audit
    kaydı varsa (ör. manuel PUT /vehicles/{id}/km), backfill bunu
    km_at_open yerine tercih etmeli — daha güvenilir/gerçek bir iz."""
    admin = admin_token
    v, plan, m = _make_activated_km_candidate(client, admin, "CkmModelJ", start_km=199_000)
    try:
        client.put(f"/api/vehicles/{v['id']}/km", headers=auth(admin), json={"current_km": 199_450})

        db = SessionLocal()
        try:
            row = db.query(models.Maintenance).filter_by(id=m["id"]).one()
            row.status = "completed"
            row.completed_at = datetime.now()
            row.completed_km = None
            db.commit()
        finally:
            db.close()

        db = SessionLocal()
        try:
            services.backfill_completed_km_for_finished_maintenances(db)
            row = db.query(models.Maintenance).filter_by(id=m["id"]).one()
            assert row.completed_km == 199_450
        finally:
            db.close()
    finally:
        _cleanup(client, admin, v, plan)
