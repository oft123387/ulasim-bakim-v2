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


def _setup_shift_scenario(client, admin, suffix: str):
    """Vardiyalı çalışma açık; iş emri A vardiyasına atanmış. A vardiyasında
    bir teknisyen, B vardiyasında başka bir teknisyen bulunur."""
    maint, leaf = _make_maintenance_with_leaf(client, admin, suffix)

    r = client.patch("/api/shift-mode", headers=auth(admin), json={"enabled": True})
    assert r.status_code == 200, r.text

    shifts = client.get("/api/shifts", headers=auth(admin)).json()
    assert len(shifts) >= 2, "varsayılan vardiyalar oluşmalıydı"
    shift_a, shift_b = shifts[0], shifts[1]

    r = client.patch(
        f"/api/maintenances/{maint['id']}/assign-shift",
        headers=auth(admin),
        json={"assigned_shift_id": shift_a["id"]},
    )
    assert r.status_code == 200, r.text

    inside = _create_user(client, admin, f"shift_in_{suffix}", "Vardiyadaki Usta")
    outside = _create_user(client, admin, f"shift_out_{suffix}", "Diğer Vardiya Ustası")
    for uid, sid in ((inside["id"], shift_a["id"]), (outside["id"], shift_b["id"])):
        r = client.patch(f"/api/users/{uid}", headers=auth(admin), json={"shift_id": sid})
        assert r.status_code == 200, r.text

    return maint, leaf, inside, outside


def test_worker_from_another_shift_is_rejected(client, admin_token):
    """İş emri A vardiyasına atanmışken B vardiyasındaki biri "yapan" olarak
    seçilemez — yanlış vardiyaya kayıt düşmesin."""
    admin = admin_token
    maint, leaf, _inside, outside = _setup_shift_scenario(client, admin, "06")

    r = client.post(
        f"/api/items/{leaf['id']}/complete",
        headers=auth(admin),
        json={"worker_ids": [outside["id"]]},
    )
    assert r.status_code == 400, r.text
    assert "vardiyasında" in r.json()["detail"]
    assert _leaf_after(client, admin, maint["id"])["is_completed"] is False


def test_worker_from_assigned_shift_is_accepted(client, admin_token):
    admin = admin_token
    maint, leaf, inside, _outside = _setup_shift_scenario(client, admin, "07")

    r = client.post(
        f"/api/items/{leaf['id']}/complete",
        headers=auth(admin),
        json={"worker_ids": [inside["id"]]},
    )
    assert r.status_code == 200, r.text
    done = _leaf_after(client, admin, maint["id"])
    assert [w["id"] for w in done["workers"]] == [inside["id"]]


def test_technician_from_another_shift_cannot_tick(client, admin_token):
    """Başka vardiyadaki teknisyen iş emrini açıp madde tikleyemez."""
    admin = admin_token
    maint, leaf, _inside, outside = _setup_shift_scenario(client, admin, "08")

    r = client.post(
        "/api/auth/login",
        data={"username": outside["username"], "password": "Test1234!"},
    )
    assert r.status_code == 200, r.text
    outsider_token = r.json()["access_token"]

    r = client.post(f"/api/items/{leaf['id']}/complete", headers=auth(outsider_token))
    assert r.status_code == 403, r.text
    assert _leaf_after(client, admin, maint["id"])["is_completed"] is False


def _login(client, username: str) -> str:
    r = client.post("/api/auth/login", data={"username": username, "password": "Test1234!"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_only_supervisor_can_tick_on_behalf_of_others(client, admin_token):
    """Vardiya sorumlusu başkaları adına işaretleyebilir; sıradan üye
    yalnızca kendi adına."""
    admin = admin_token
    maint, leaf, inside, _outside = _setup_shift_scenario(client, admin, "09")

    # Aynı vardiyaya ikinci bir üye ekle.
    mate = _create_user(client, admin, "shift_mate_09", "Vardiya Arkadaşı")
    shift_id = client.get(f"/api/maintenances/{maint['id']}", headers=auth(admin)).json()[
        "assigned_shift"
    ]["id"]
    r = client.patch(f"/api/users/{mate['id']}", headers=auth(admin), json={"shift_id": shift_id})
    assert r.status_code == 200, r.text

    # Sorumlu SEÇİLMEDEN: sıradan üye başkasını yazamaz.
    member_token = _login(client, mate["username"])
    r = client.post(
        f"/api/items/{leaf['id']}/complete",
        headers=auth(member_token),
        json={"worker_ids": [inside["id"], mate["id"]]},
    )
    assert r.status_code == 403, r.text
    assert "vardiya sorumlusu" in r.json()["detail"]

    # Kendi adına işaretleyebilir mi? (kilitlenmesin diye ayrı bir iş emrinde
    # denenir — yukarıdaki istek reddedildiği için madde hâlâ açık.)
    r = client.post(
        f"/api/items/{leaf['id']}/complete",
        headers=auth(member_token),
        json={"worker_ids": [mate["id"]]},
    )
    assert r.status_code == 200, r.text


def test_supervisor_can_tick_on_behalf_of_others(client, admin_token):
    admin = admin_token
    maint, leaf, inside, _outside = _setup_shift_scenario(client, admin, "10")
    mate = _create_user(client, admin, "shift_mate_10", "Vardiya Arkadaşı 2")
    shift_id = client.get(f"/api/maintenances/{maint['id']}", headers=auth(admin)).json()[
        "assigned_shift"
    ]["id"]
    r = client.patch(f"/api/users/{mate['id']}", headers=auth(admin), json={"shift_id": shift_id})
    assert r.status_code == 200, r.text

    # mate'i vardiya sorumlusu yap.
    r = client.patch(f"/api/shifts/{shift_id}", headers=auth(admin), json={"supervisor_id": mate["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["supervisor"]["id"] == mate["id"]

    supervisor_token = _login(client, mate["username"])
    r = client.post(
        f"/api/items/{leaf['id']}/complete",
        headers=auth(supervisor_token),
        json={"worker_ids": [inside["id"], mate["id"]]},
    )
    assert r.status_code == 200, r.text
    done = _leaf_after(client, admin, maint["id"])
    assert sorted(w["id"] for w in done["workers"]) == sorted([inside["id"], mate["id"]])


def test_supervisor_must_be_member_of_the_shift(client, admin_token):
    admin = admin_token
    _maint, _leaf, _inside, outside = _setup_shift_scenario(client, admin, "11")
    shifts = client.get("/api/shifts", headers=auth(admin)).json()
    shift_a = shifts[0]

    r = client.patch(
        f"/api/shifts/{shift_a['id']}", headers=auth(admin), json={"supervisor_id": outside["id"]}
    )
    assert r.status_code == 400, r.text
    assert "üyesi değil" in r.json()["detail"]


def test_leaving_shift_clears_supervisor_role(client, admin_token):
    """Sorumlu vardiyadan çıkarılınca sorumluluğu da düşer."""
    admin = admin_token
    _maint, _leaf, inside, _outside = _setup_shift_scenario(client, admin, "12")
    shift_id = inside["shift_id"] or client.get("/api/shifts", headers=auth(admin)).json()[0]["id"]

    r = client.patch(f"/api/shifts/{shift_id}", headers=auth(admin), json={"supervisor_id": inside["id"]})
    assert r.status_code == 200, r.text

    r = client.patch(f"/api/users/{inside['id']}", headers=auth(admin), json={"shift_id": None})
    assert r.status_code == 200, r.text

    shifts = {s["id"]: s for s in client.get("/api/shifts", headers=auth(admin)).json()}
    assert shifts[shift_id]["supervisor"] is None


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
