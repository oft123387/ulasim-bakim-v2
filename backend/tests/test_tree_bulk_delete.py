"""Bakım Ağacı sayfasındaki toplu silme uçlarının testleri:

  - DELETE /api/tree/plan/{plan_id}: tek bir planın TÜM ağacını (kök
    düğümler + alt maddeler) siler; planın kendisi ve varsa geçmiş iş
    emirleri KORUNUR.
  - DELETE /api/tree: veritabanındaki HER plana ait TÜM ağaçları tek
    seferde siler; planların kendisi yine korunur.

NOT: "DELETE /api/tree" TASARIM GEREĞİ veritabanındaki TÜM ağaçları siler —
bu yüzden paylaşılan (session scope) test veritabanında (conftest.client)
çalıştırılırsa diğer testlerin dayandığı gerçek/seed bakım ağacını da (bkz.
conftest._seed_database) kalıcı olarak siler ve o testler bozulur. Bu, o
testlerdeki bir hata DEĞİL — bilerek yıkıcı olan bu uca özgü bir izolasyon
gereksinimi. Bu yüzden global "tüm ağaçları sil" testi kendi İZOLE, geçici
veritabanı/TestClient'ı üzerinde çalışır (bkz. _isolated_admin_client),
paylaşılan `client` fixture'ına hiç dokunmaz."""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import database, models
from app.auth import hash_password
from app.database import Base
from app.main import app

from .conftest import auth


@pytest.fixture()
def isolated_admin():
    """DELETE /api/tree gibi TÜM veritabanını etkileyen uçlar için: paylaşılan
    session-scope test veritabanından tamamen ayrı, geçici bir SQLite dosyası
    + kendi TestClient'ı. Yalnızca bu testin süresince `get_db` bağımlılığı
    bu izole veritabanına yönlendirilir, test bitince eski haline döner."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="ulasim_bakim_wipe_test_"))
    engine = create_engine(
        f"sqlite:///{tmp_dir / 'wipe_test.db'}", connect_args={"check_same_thread": False}
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    db = TestSessionLocal()
    db.add(models.User(
        username="wipe_admin", full_name="Wipe Test Admin", role="admin",
        password_hash=hash_password("wipe123"),
    ))
    db.commit()
    db.close()

    def _override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Not: startup event'leri (arka plan döngüleri vb.) kasıtlı olarak
    # tetiklenmez ("with" bloğu kullanılmaz) — bunlar modül seviyesindeki
    # database.SessionLocal'ı (paylaşılan gerçek veritabanı) kullanır, bu
    # izole testle hiç ilgileri yok, çalışmalarına gerek de yok.
    app.dependency_overrides[database.get_db] = _override_get_db
    isolated_client = TestClient(app)
    try:
        r = isolated_client.post(
            "/api/auth/login", data={"username": "wipe_admin", "password": "wipe123"}
        )
        assert r.status_code == 200, r.text
        token = r.json()["access_token"]
        yield isolated_client, token
    finally:
        app.dependency_overrides.pop(database.get_db, None)
        engine.dispose()

_BULK_HEADERS = ["ARAÇ", "BÖLGE", "SİSTEM", "EKİPMAN", "BAKIM", "BAKIM KALEMİ", "KART KODU"]


def _make_plan_with_tree(client, admin, model, km, root_name="Ana Bölüm"):
    r = client.post(
        "/api/plans",
        headers=auth(admin),
        json={"name": f"{model} {km} km", "plan_type": "km", "interval_km": km, "vehicle_model": model},
    )
    assert r.status_code == 200, r.text
    plan = r.json()
    r = client.post(
        "/api/tree",
        headers=auth(admin),
        json={"name": root_name, "plan_id": plan["id"]},
    )
    assert r.status_code == 200, r.text
    root = r.json()
    r = client.post(
        "/api/tree",
        headers=auth(admin),
        json={"name": "Alt Madde", "parent_id": root["id"]},
    )
    assert r.status_code == 200, r.text
    return plan


def test_delete_single_plan_tree_keeps_plan_and_others(client, admin_token):
    admin = admin_token
    model_a = "Tree Delete Test A"
    model_b = "Tree Delete Test B"

    plan_a = _make_plan_with_tree(client, admin, model_a, 111_000)
    plan_b = _make_plan_with_tree(client, admin, model_b, 222_000)

    tree_a = client.get(f"/api/tree?plan_id={plan_a['id']}", headers=auth(admin)).json()
    assert len(tree_a) == 1

    r = client.delete(f"/api/tree/plan/{plan_a['id']}", headers=auth(admin))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["deleted_roots"] == 1

    # A'nın ağacı boşaldı ama plan hâlâ var.
    tree_a_after = client.get(f"/api/tree?plan_id={plan_a['id']}", headers=auth(admin)).json()
    assert tree_a_after == []
    plans = client.get("/api/plans", headers=auth(admin)).json()
    assert any(p["id"] == plan_a["id"] for p in plans)

    # B'ye dokunulmadı.
    tree_b_after = client.get(f"/api/tree?plan_id={plan_b['id']}", headers=auth(admin)).json()
    assert len(tree_b_after) == 1
    assert any(p["id"] == plan_b["id"] for p in plans)

    # Var olmayan bir plan_id -> 404.
    r = client.delete("/api/tree/plan/999999", headers=auth(admin))
    assert r.status_code == 404


def test_delete_all_trees_wipes_every_plan_but_keeps_plans(isolated_admin):
    client, admin = isolated_admin
    model_c = "Tree Wipe Test C"
    model_d = "Tree Wipe Test D"

    plan_c = _make_plan_with_tree(client, admin, model_c, 333_000)
    plan_d = _make_plan_with_tree(client, admin, model_d, 444_000)

    r = client.delete("/api/tree", headers=auth(admin))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["deleted_roots"] >= 2

    tree_c = client.get(f"/api/tree?plan_id={plan_c['id']}", headers=auth(admin)).json()
    tree_d = client.get(f"/api/tree?plan_id={plan_d['id']}", headers=auth(admin)).json()
    assert tree_c == []
    assert tree_d == []

    # Planların kendisi hâlâ var (yalnızca ağaç boşaldı).
    plans = client.get("/api/plans", headers=auth(admin)).json()
    assert any(p["id"] == plan_c["id"] for p in plans)
    assert any(p["id"] == plan_d["id"] for p in plans)


def test_delete_tree_endpoints_require_admin(client, usta_token):
    r = client.delete("/api/tree", headers=auth(usta_token))
    assert r.status_code == 403
    r = client.delete("/api/tree/plan/1", headers=auth(usta_token))
    assert r.status_code == 403
