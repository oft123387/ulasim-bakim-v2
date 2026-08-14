"""POST /api/items/{id}/complete — "bu maddeyi kimler yaptı" (çoklu kişi).

Vardiyada aynı maddede birden fazla kişi çalışabildiği için tikleme sırasında
hepsi işaretlenebilir (bkz. models.maintenance_item_workers). completed_by_id
tek kişilik "tikleyen" alanı olarak KORUNUR — mevcut KPI/rapor hesapları onu
kullanıyor — ve çoklu seçimde listedeki ilk kişiye ayarlanır.
"""

from .conftest import auth


def _make_maintenance_with_leaf(client, admin, suffix: str):
    """Tek yaprak maddesi olan bir iş emri kurar ve (iş emri, yaprak) döner."""
    model = f"WorkersTest {suffix}"

    r = client.post(
        "/api/vehicles",
        headers=auth(admin),
        json={"code": f"97{suffix}", "name": "Worker Test Tramvay", "current_km": 30_000, "model": model},
    )
    assert r.status_code == 200, r.text
    vehicle = r.json()

    r = client.post(
        "/api/plans",
        headers=auth(admin),
        json={"name": f"{model} Planı", "plan_type": "km", "interval_km": 30_000, "vehicle_model": model},
    )
    assert r.status_code == 200, r.text
    plan = r.json()

    r = client.post("/api/tree", headers=auth(admin), json={"name": "ARAÇ ALTI", "plan_id": plan["id"]})
    assert r.status_code == 200, r.text
    root = r.json()
    r = client.post(
        "/api/tree", headers=auth(admin),
        json={"name": "Şanzıman yağ seviyesi", "parent_id": root["id"]},
    )
    assert r.status_code == 200, r.text

    r = client.post(
        "/api/maintenances/manual",
        headers=auth(admin),
        json={"vehicle_id": vehicle["id"], "plan_id": plan["id"], "target_km": 30_000},
    )
    assert r.status_code == 200, r.text
    maint = r.json()

    detail = client.get(f"/api/maintenances/{maint['id']}", headers=auth(admin)).json()
    leaf = detail["items"][0]["children"][0]
    return maint, leaf


def _create_user(client, admin, username: str, full_name: str) -> dict:
    r = client.post(
        "/api/users",
        headers=auth(admin),
        json={
            "username": username,
            "full_name": full_name,
            "password": "Test1234!",
            "role": "technician",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _leaf_after(client, admin, maintenance_id: int) -> dict:
    detail = client.get(f"/api/maintenances/{maintenance_id}", headers=auth(admin)).json()
    return detail["items"][0]["children"][0]


def test_complete_with_multiple_workers_records_all_of_them(client, admin_token):
    admin = admin_token
    maint, leaf = _make_maintenance_with_leaf(client, admin, "01")
    u1 = _create_user(client, admin, "worker_a", "Ali Usta")
    u2 = _create_user(client, admin, "worker_b", "Veli Usta")

    r = client.post(
        f"/api/items/{leaf['id']}/complete",
        headers=auth(admin),
        json={"worker_ids": [u1["id"], u2["id"]]},
    )
    assert r.status_code == 200, r.text

    done = _leaf_after(client, admin, maint["id"])
    assert done["is_completed"] is True
    names = sorted(w["full_name"] for w in done["workers"])
    assert names == ["Ali Usta", "Veli Usta"]
    # Listedeki ilk kişi "tikleyen" olarak da yazılır — completed_by_id
    # üzerinden çalışan mevcut raporlar boş kalmaz.
    assert done["completed_by"]["id"] == u1["id"]


def test_complete_without_workers_keeps_old_behaviour(client, admin_token):
    """worker_ids gönderilmezse eski davranış: oturum sahibine atfedilir ve
    workers listesi boş kalır (vardiyalı çalışma kapalı senaryosu)."""
    admin = admin_token
    maint, leaf = _make_maintenance_with_leaf(client, admin, "02")

    r = client.post(f"/api/items/{leaf['id']}/complete", headers=auth(admin))
    assert r.status_code == 200, r.text

    done = _leaf_after(client, admin, maint["id"])
    assert done["workers"] == []
    assert done["completed_by"]["username"] == "admin"


def test_duplicate_worker_ids_are_deduplicated(client, admin_token):
    admin = admin_token
    maint, leaf = _make_maintenance_with_leaf(client, admin, "03")
    u1 = _create_user(client, admin, "worker_c", "Ayşe Usta")

    r = client.post(
        f"/api/items/{leaf['id']}/complete",
        headers=auth(admin),
        json={"worker_ids": [u1["id"], u1["id"], u1["id"]]},
    )
    assert r.status_code == 200, r.text

    done = _leaf_after(client, admin, maint["id"])
    assert [w["id"] for w in done["workers"]] == [u1["id"]]


def test_unknown_worker_id_is_rejected(client, admin_token):
    admin = admin_token
    maint, leaf = _make_maintenance_with_leaf(client, admin, "04")

    r = client.post(
        f"/api/items/{leaf['id']}/complete",
        headers=auth(admin),
        json={"worker_ids": [999_999]},
    )
    assert r.status_code == 400, r.text

    # Reddedilen istek maddeyi tamamlamamış olmalı.
    still = _leaf_after(client, admin, maint["id"])
    assert still["is_completed"] is False


def test_unlocking_item_clears_workers(client, admin_token):
    """Kilit açılıp madde sıfırlanınca "kimler yaptı" kaydı da düşer;
    yeniden tiklendiğinde kişiler baştan seçilir."""
    admin = admin_token
    maint, leaf = _make_maintenance_with_leaf(client, admin, "05")
    u1 = _create_user(client, admin, "worker_d", "Mehmet Usta")

    r = client.post(
        f"/api/items/{leaf['id']}/complete",
        headers=auth(admin),
        json={"worker_ids": [u1["id"]]},
    )
    assert r.status_code == 200, r.text
    assert len(_leaf_after(client, admin, maint["id"])["workers"]) == 1

    r = client.post(f"/api/items/{leaf['id']}/unlock", headers=auth(admin))
    assert r.status_code == 200, r.text

    reset = _leaf_after(client, admin, maint["id"])
    assert reset["is_completed"] is False
    assert reset["workers"] == []
