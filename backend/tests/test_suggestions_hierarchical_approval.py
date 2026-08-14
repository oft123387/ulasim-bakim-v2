"""Öneriler sayfası: araç modeli (Bozankaya/Sirio) seçimi ve TEK AŞAMALI onay.

Faz 31'de Faz 30'un iki aşamalı (amir -> mühendis) hiyerarşik zinciri
kaldırıldı. Akış artık düz: herhangi bir rol öneri oluşturur; mühendis,
amir ya da admin öneriyi doğrudan onaylar veya reddeder ve bu karar
NİHAİDİR — ikinci bir aşama, "Ağaca Eklendi" kararı ve rol sırası yoktur.
Herkesin TÜM önerileri görebildiği de burada doğrulanır.

Dosya adı, geçmiş kayıtların (ve migration testinin) izlenebilirliği için
korunmuştur."""

import pytest

from .conftest import auth


@pytest.fixture(scope="module")
def engineer_token(client, admin_token):
    client.post(
        "/api/users", headers=auth(admin_token),
        json={"username": "sug_muh", "full_name": "Test Mühendis", "role": "engineer", "password": "sugmuhpass1"},
    )
    r = client.post("/api/auth/login", data={"username": "sug_muh", "password": "sugmuhpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def amir_token(client, admin_token):
    client.post(
        "/api/users", headers=auth(admin_token),
        json={"username": "sug_amir", "full_name": "Test Amir", "role": "amir", "password": "sugamirpass1"},
    )
    r = client.post("/api/auth/login", data={"username": "sug_amir", "password": "sugamirpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _create(client, token, model="Sirio", text="Test önerisi — fren balatası kontrolü eklensin"):
    r = client.post(
        "/api/suggestions", headers=auth(token),
        json={"vehicle_model": model, "text": text},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_create_requires_vehicle_model(client, usta_token):
    # Pydantic (min_length=1) boş string'i şema doğrulamasında (422) reddeder.
    r = client.post(
        "/api/suggestions", headers=auth(usta_token),
        json={"vehicle_model": "", "text": "boş model"},
    )
    assert r.status_code == 422

    # Yalnızca boşluk karakteri şemadan geçer ama endpoint kendi kontrolüyle (400) reddeder.
    r2 = client.post(
        "/api/suggestions", headers=auth(usta_token),
        json={"vehicle_model": "   ", "text": "boşluklu model"},
    )
    assert r2.status_code == 400


def test_create_sets_vehicle_model_and_no_specific_vehicle(client, usta_token):
    sug = _create(client, usta_token, model="Bozankaya Tip-1")
    assert sug["status"] == "pending"
    assert sug["vehicle_model"] == "Bozankaya Tip-1"
    assert sug["vehicle"] is None
    assert sug["amir_status"] == "pending"
    assert sug["engineer_status"] == "pending"


def test_everyone_sees_all_suggestions(client, usta_token, amir_token, engineer_token):
    sug = _create(client, usta_token)
    for tok in (usta_token, amir_token, engineer_token):
        r = client.get("/api/suggestions", headers=auth(tok))
        assert r.status_code == 200, r.text
        assert any(s["id"] == sug["id"] for s in r.json())


def test_engineer_can_decide_directly_without_amir(client, usta_token, engineer_token):
    """Zincir kalktı: mühendis, amir kararını beklemeden doğrudan karar verir."""
    sug = _create(client, usta_token)
    r = client.patch(
        f"/api/suggestions/{sug['id']}", headers=auth(engineer_token),
        json={"status": "approved", "admin_note": "uygun"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "approved"
    assert body["reviewed_by"]["full_name"] == "Test Mühendis"
    assert body["admin_note"] == "uygun"


def test_technician_cannot_review(client, usta_token):
    sug = _create(client, usta_token)
    r = client.patch(
        f"/api/suggestions/{sug['id']}", headers=auth(usta_token),
        json={"status": "approved"},
    )
    assert r.status_code == 403


def test_amir_decision_is_final_no_second_stage(client, usta_token, amir_token, engineer_token):
    """Amirin kararı da nihaidir — öneri artık mühendise DÜŞMEZ."""
    sug = _create(client, usta_token)
    r = client.patch(
        f"/api/suggestions/{sug['id']}", headers=auth(amir_token),
        json={"status": "rejected", "admin_note": "gerekli değil"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "rejected"
    assert body["admin_note"] == "gerekli değil"

    # Sonuçlanmış öneri yeniden değerlendirilemez (kim olursa olsun).
    r2 = client.patch(
        f"/api/suggestions/{sug['id']}", headers=auth(engineer_token),
        json={"status": "approved"},
    )
    assert r2.status_code == 400


def test_amir_approval_does_not_create_pending_engineer(client, usta_token, amir_token):
    """Amir onayı doğrudan 'approved' olur — eski 'pending_engineer' ara
    durumu artık hiç üretilmez."""
    sug = _create(client, usta_token)
    r = client.patch(
        f"/api/suggestions/{sug['id']}", headers=auth(amir_token),
        json={"status": "approved"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"


def test_added_status_is_no_longer_accepted(client, usta_token, engineer_token):
    """'Bakım ağacına ekle' kaldırıldı: 'added' kararı artık şemadan geçmez.
    Bir öneriyi onaylamak, ağacın kendiliğinden değişmesi anlamına gelmez."""
    sug = _create(client, usta_token)
    r = client.patch(
        f"/api/suggestions/{sug['id']}", headers=auth(engineer_token),
        json={"status": "added"},
    )
    assert r.status_code == 422


def test_admin_can_review_too(client, usta_token, admin_token):
    sug = _create(client, usta_token)
    r = client.patch(
        f"/api/suggestions/{sug['id']}", headers=auth(admin_token),
        json={"status": "approved"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"

    # Tek karar nihai — admin bile ikinci kez değerlendiremez.
    r2 = client.patch(
        f"/api/suggestions/{sug['id']}", headers=auth(admin_token),
        json={"status": "rejected"},
    )
    assert r2.status_code == 400


def test_legacy_pending_engineer_row_is_flattened_to_pending(tmp_path):
    """Zincirin ORTASINDA kalmış eski kayıtlar ('pending_engineer' — amir
    onaylamış, mühendis bekleniyor) migration ile normal 'pending'e döner ki
    yetkili biri tek kararla sonuçlandırabilsin. Sonuçlanmış kayıtlara
    dokunulmaz."""
    import sqlalchemy as sa

    from app import migrate
    from app.database import Base

    db_path = tmp_path / "midchain.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        for sid, status in ((1, "pending_engineer"), (2, "pending"), (3, "approved"), (4, "rejected")):
            conn.execute(sa.text(
                "INSERT INTO suggestions (id, text, status, vehicle_model, admin_note, "
                "amir_status, amir_note, engineer_status, engineer_note, created_at) "
                "VALUES (:i, 'x', :s, 'Sirio', '', 'pending', '', 'pending', '', '2026-01-01 09:00:00')"
            ), {"i": sid, "s": status})

    migrate.run_light_migrations(engine)

    with engine.begin() as conn:
        rows = dict(conn.execute(sa.text("SELECT id, status FROM suggestions")).all())
    assert rows == {1: "pending", 2: "pending", 3: "approved", 4: "rejected"}
    engine.dispose()


def test_finalized_suggestion_cannot_be_reviewed_again(client, usta_token, amir_token):
    sug = _create(client, usta_token)
    client.patch(f"/api/suggestions/{sug['id']}", headers=auth(amir_token), json={"status": "rejected"})
    r = client.patch(f"/api/suggestions/{sug['id']}", headers=auth(amir_token), json={"status": "approved"})
    assert r.status_code == 400


def test_delete_denied_for_unrelated_role_allowed_for_creator(client, admin_token, usta_token):
    sug = _create(client, usta_token)
    other = client.post(
        "/api/users", headers=auth(admin_token),
        json={"username": "sug_usta2", "full_name": "Başka Usta", "role": "technician", "password": "sugusta2pass"},
    ).json()
    login = client.post("/api/auth/login", data={"username": "sug_usta2", "password": "sugusta2pass"})
    other_token = login.json()["access_token"]

    r = client.delete(f"/api/suggestions/{sug['id']}", headers=auth(other_token))
    assert r.status_code == 403

    r2 = client.delete(f"/api/suggestions/{sug['id']}", headers=auth(usta_token))
    assert r2.status_code == 200, r2.text


def test_legacy_single_stage_migration_maps_correctly(tmp_path):
    """suggestions tablosu bu özellikten ÖNCEKİ (tek aşamalı, vehicle_id
    NOT NULL) şemayla var olan bir veritabanında, migrate.run_light_
    migrations çalıştığında mevcut kayıtların kaybolmadan yeni iki aşamalı
    alanlara doğru eşlendiğini doğrular."""
    import sqlalchemy as sa

    from app import migrate

    db_path = tmp_path / "legacy_suggestions.db"
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE vehicles (id INTEGER PRIMARY KEY, code TEXT)"))
        conn.execute(sa.text("CREATE TABLE users (id INTEGER PRIMARY KEY, full_name TEXT)"))
        conn.execute(sa.text("CREATE TABLE maintenance_plans (id INTEGER PRIMARY KEY, name TEXT)"))
        conn.execute(sa.text(
            """
            CREATE TABLE suggestions (
                id INTEGER PRIMARY KEY,
                vehicle_id INTEGER NOT NULL REFERENCES vehicles(id) ON DELETE CASCADE,
                plan_id INTEGER REFERENCES maintenance_plans(id) ON DELETE SET NULL,
                text TEXT NOT NULL,
                status VARCHAR(20) DEFAULT 'pending',
                admin_note TEXT DEFAULT '',
                created_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at DATETIME,
                reviewed_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                reviewed_at DATETIME
            )
            """
        ))
        conn.execute(sa.text("INSERT INTO vehicles (id, code) VALUES (1, 'V1')"))
        conn.execute(sa.text("INSERT INTO users (id, full_name) VALUES (1, 'Eski Reviewer')"))
        conn.execute(sa.text(
            "INSERT INTO suggestions (id, vehicle_id, text, status, reviewed_by_id, reviewed_at) "
            "VALUES (1, 1, 'eski onaylanmis', 'approved', 1, '2025-01-01 10:00:00')"
        ))
        conn.execute(sa.text(
            "INSERT INTO suggestions (id, vehicle_id, text, status, reviewed_by_id, reviewed_at) "
            "VALUES (2, 1, 'eski reddedilmis', 'rejected', 1, '2025-01-02 10:00:00')"
        ))
        conn.execute(sa.text(
            "INSERT INTO suggestions (id, vehicle_id, text, status) VALUES (3, 1, 'hala bekleyen', 'pending')"
        ))
        conn.execute(sa.text(
            "INSERT INTO suggestions (id, vehicle_id, text, status, reviewed_by_id, reviewed_at) "
            "VALUES (4, 1, 'agaca eklenmis', 'added', 1, '2025-01-03 10:00:00')"
        ))

    migrate.run_light_migrations(engine)

    with engine.begin() as conn:
        rows = {
            row[0]: row
            for row in conn.execute(sa.text(
                "SELECT id, status, amir_status, engineer_status, vehicle_model FROM suggestions ORDER BY id"
            ))
        }

    assert rows[1][1] == "approved" and rows[1][2] == "approved" and rows[1][3] == "approved"
    assert rows[2][1] == "rejected" and rows[2][2] == "rejected" and rows[2][3] == "pending"
    assert rows[3][1] == "pending" and rows[3][2] == "pending" and rows[3][3] == "pending"
    assert rows[4][1] == "added" and rows[4][2] == "approved" and rows[4][3] == "added"
    assert all(r[4] == "" for r in rows.values())

    # vehicle_id artık NULL kabul etmeli (model bazlı yeni bir öneriyi simüle eder).
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO suggestions (id, vehicle_id, vehicle_model, text, status) "
            "VALUES (5, NULL, 'Sirio', 'model bazli yeni', 'pending')"
        ))
    engine.dispose()

    # İkinci kez çalıştırıldığında idempotent olmalı (tekrar rebuild etmemeli).
    engine2 = sa.create_engine(f"sqlite:///{db_path}")
    migrate.run_light_migrations(engine2)
    with engine2.begin() as conn:
        count = conn.execute(sa.text("SELECT COUNT(*) FROM suggestions")).scalar()
    assert count == 5
    engine2.dispose()
