"""Kritik alarm e-posta bildirimi — bkz. main.py _send_critical_alarm_emails,
POST /api/alarms/send-critical-emails-now. SMTP gerçek bir sunucu
gerektirmediği için email_utils.is_configured/send_email monkeypatch'lenir."""

from app import email_utils

from .conftest import auth


def test_critical_alarm_email_sent_once_and_deduped(client, admin_token, monkeypatch):
    admin = admin_token
    sent_log: list[tuple[str, str]] = []

    monkeypatch.setattr(email_utils, "is_configured", lambda: True)
    monkeypatch.setattr(
        email_utils, "send_email",
        lambda to_addr, subject, body: sent_log.append((to_addr, subject)) or True,
    )

    # Admin'in e-postası doğrulanmamış olabilir (seed verisinde) — testte
    # bilerek doğrulanmış/aktif hale getiriyoruz ki alıcı listesine girsin.
    admin_user = client.get("/api/users", headers=auth(admin)).json()
    me = next(u for u in admin_user if u["username"] == "admin")
    client.patch(
        f"/api/users/{me['id']}", headers=auth(admin),
        json={"email": "admin@example.com"},
    )
    # email_verified alanı UserUpdate'te yoksa doğrudan DB üzerinden ayarla.
    from app.database import SessionLocal
    from app import models
    db = SessionLocal()
    try:
        u = db.get(models.User, me["id"])
        u.email = "admin@example.com"
        u.email_verified = True
        db.commit()
    finally:
        db.close()

    v = client.post(
        "/api/vehicles", headers=auth(admin),
        json={"code": "ALARM-EPOSTA-1", "name": "Alarm E-posta Test Aracı", "current_km": 0, "model": "AlarmEmailTestModel"},
    ).json()
    plan = client.post(
        "/api/plans", headers=auth(admin),
        json={"name": "Alarm E-posta Test Planı", "interval_km": 1000, "vehicle_model": "AlarmEmailTestModel"},
    ).json()
    # Hedefi (1000 km) aşacak şekilde km güncelle -> remaining_km <= 0 -> kritik.
    client.put(f"/api/vehicles/{v['id']}/km", headers=auth(admin), json={"current_km": 1050})

    r = client.post("/api/alarms/send-critical-emails-now", headers=auth(admin))
    assert r.status_code == 200, r.text
    assert r.json()["sent"] >= 1
    assert any(to == "admin@example.com" for to, _ in sent_log)

    # İkinci çağrıda AYNI alarm için tekrar e-posta gitmemeli (dedup).
    sent_log.clear()
    r2 = client.post("/api/alarms/send-critical-emails-now", headers=auth(admin))
    assert r2.status_code == 200
    assert r2.json()["sent"] == 0
    assert sent_log == []

    # Temizlik: plan sil (diğer testleri etkilememesi için gerekmez ama
    # araç modeli benzersiz olduğu için zaten izole).
    _ = plan


def test_critical_alarm_email_requires_smtp_configured(client, admin_token, monkeypatch):
    monkeypatch.setattr(email_utils, "is_configured", lambda: False)
    r = client.post("/api/alarms/send-critical-emails-now", headers=auth(admin_token))
    assert r.status_code == 400


def test_critical_alarm_email_requires_admin(client, usta_token):
    r = client.post("/api/alarms/send-critical-emails-now", headers=auth(usta_token))
    assert r.status_code == 403
