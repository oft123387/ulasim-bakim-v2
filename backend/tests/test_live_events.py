"""Canlı Güncelleme (SSE) — bkz. app/events.py, main.py /api/events/stream.
TestClient gerçek bir soket açmadığı için sonsuz akışın tamamını okumuyoruz;
yalnızca (1) auth olmadan/](kötü token ile) reddedildiğini ve (2) bir
mutasyondan sonra events.broadcast'in gerçekten çağrıldığını (services.
log_action üzerinden) doğruluyoruz."""

from .conftest import auth
from app import events


def test_events_stream_rejects_invalid_token(client):
    r = client.get("/api/events/stream?token=gecersiz-bir-token")
    assert r.status_code == 401


def test_events_stream_rejects_missing_token(client):
    r = client.get("/api/events/stream")
    assert r.status_code == 422  # token zorunlu query parametresi


def test_log_action_broadcasts_event(client, admin_token):
    """services.log_action çağrıldığında events.broadcast tetiklenmeli —
    örn. bir parça oluşturmak 'part'/'create' olayını yayınlamalı."""
    sid = events.subscribe()
    try:
        events.poll(sid)  # önceki testlerden kalan olayları temizle
        r = client.post(
            "/api/parts", headers=auth(admin_token), json={"name": "SSE Test Parçası"},
        )
        assert r.status_code == 200, r.text
        items = events.poll(sid)
        assert any(i["entity_type"] == "part" and i["action"] == "create" for i in items)
    finally:
        events.unsubscribe(sid)
