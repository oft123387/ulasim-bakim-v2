"""GET /api/system/status — admin için tanı/teşhis sistem özeti.

Amaç: "kodu düzenledim ama uygulama değişmiyor" / "yanlış veritabanına
bakıyormuşum" türü sorunları saniyeler içinde teşhis edebilmek (bkz. bu
projede gerçekte yaşanan Docker named-volume karışıklığı: backend/
klasöründeki ulasim_bakim.db dosyası ile konteynerin GERÇEKTEN kullandığı
/app/data/ulasim_bakim.db birbirinden habersizdi)."""

from .conftest import auth


def test_system_status_reports_real_db_location_and_counts(client, admin_token):
    admin = admin_token
    r = client.get("/api/system/status", headers=auth(admin))
    assert r.status_code == 200, r.text
    body = r.json()

    # Testler her zaman izole, geçici bir SQLite dosyası kullanır (bkz.
    # conftest.py) — bu uç nokta o GERÇEK (çözümlenmiş, mutlak) yolu
    # döndürmeli, "./ulasim_bakim.db" gibi göreli/belirsiz bir değer değil.
    assert body["db_backend"] == "sqlite"
    assert body["db_location"].endswith("test.db")
    assert body["db_location"].startswith("/")  # mutlak yol
    assert body["foreign_keys_enforced"] is True

    assert body["vehicle_count"] >= 0
    assert body["plan_count"] >= 1
    assert body["uptime_seconds"] >= 0
    assert "backup_dir" in body and "upload_dir" in body and "log_dir" in body


def test_system_status_requires_admin(client):
    # Girişsiz erişim engellenmeli
    r = client.get("/api/system/status")
    assert r.status_code == 401
