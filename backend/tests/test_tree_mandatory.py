"""Bakım ağacında madde zorunlu/opsiyonel seçilebilmesi (Faz 12).

Bir ağaç maddesi normalde "zorunlu"dur (TreeNode.is_mandatory=True) — bir
bakım açıldığında usta bunu elle tikler. Admin/mühendis/amir bir maddeyi
"zorunlu değil" yaparsa (genel varsayılan üzerinden, "hepsine uygula", ya da
belirli TEK bir araç için istisna olarak, "araca göre seç"), BUNDAN SONRA
açılan iş emirlerinde o madde otomatik tiklenmiş gelir — usta elle
dokunmasına gerek kalmaz. Zorunlu hâle geri döndürülürse (genel varsayılan
ya da araç istisnası kaldırılarak) yeni açılan iş emirlerinde tekrar normal
(tiksiz) davranır."""

import pytest

from .conftest import auth


@pytest.fixture(scope="module")
def engineer_token(client, admin_token):
    client.post(
        "/api/users", headers=auth(admin_token),
        json={"username": "tree_muh", "full_name": "Ağaç Test Mühendis", "role": "engineer", "password": "treemuhpass1"},
    )
    r = client.post("/api/auth/login", data={"username": "tree_muh", "password": "treemuhpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _setup(client, admin, model, code="TM2-A"):
    """Bir araç + bir km planı + iki yaprak maddeli ("A Maddesi", "B Maddesi")
    basit bir ağaç kurar; ikisi de başlangıçta (varsayılan) zorunludur."""
    v = client.post(
        "/api/vehicles", headers=auth(admin),
        json={"code": code, "name": "Zorunlu Test Aracı", "current_km": 5_000, "model": model},
    ).json()
    assert "id" in v, v
    plan = client.post(
        "/api/plans", headers=auth(admin),
        json={"name": f"{model} Planı", "plan_type": "km", "interval_km": 10_000, "vehicle_model": model},
    ).json()
    root = client.post(
        "/api/tree", headers=auth(admin), json={"name": "ARAÇ ÜSTÜ", "plan_id": plan["id"]},
    ).json()
    node_a = client.post(
        "/api/tree", headers=auth(admin),
        json={"name": "A Maddesi", "parent_id": root["id"]},
    ).json()
    node_b = client.post(
        "/api/tree", headers=auth(admin),
        json={"name": "B Maddesi", "parent_id": root["id"]},
    ).json()
    return v, plan, node_a, node_b


def _cleanup(client, admin, v, plan):
    client.delete(f"/api/vehicles/{v['id']}", headers=auth(admin))
    client.delete(f"/api/plans/{plan['id']}", headers=auth(admin))


def _open_maintenance(client, admin, vehicle_id, plan_id, target_km):
    r = client.post(
        "/api/maintenances/manual", headers=auth(admin),
        json={"vehicle_id": vehicle_id, "plan_id": plan_id, "target_km": target_km},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _items_by_name(client, admin, maintenance_id) -> dict:
    detail = client.get(f"/api/maintenances/{maintenance_id}", headers=auth(admin)).json()
    root_children = detail["items"][0]["children"]
    return {it["name"]: it for it in root_children}


def test_technician_cannot_toggle_mandatory(client, admin_token, usta_token):
    admin = admin_token
    v, plan, node_a, node_b = _setup(client, admin, "ZorunluModelTech", code="TM2-TECH")
    try:
        r = client.patch(
            f"/api/tree/{node_a['id']}/mandatory", headers=auth(usta_token),
            json={"is_mandatory": False},
        )
        assert r.status_code == 403
    finally:
        _cleanup(client, admin, v, plan)


def test_engineer_can_set_general_default_not_mandatory(client, admin_token, engineer_token):
    """Mühendis (admin olmadan) genel varsayılanı değiştirebilmeli — ve bu,
    BUNDAN SONRA açılan iş emirlerinde o maddeyi otomatik tikler; kardeş
    madde (hâlâ zorunlu) normal (tiksiz) kalır."""
    admin = admin_token
    v, plan, node_a, node_b = _setup(client, admin, "ZorunluModelGenel", code="TM2-GENEL")
    try:
        r = client.patch(
            f"/api/tree/{node_b['id']}/mandatory", headers=auth(engineer_token),
            json={"is_mandatory": False},
        )
        assert r.status_code == 200, r.text
        assert r.json()["is_mandatory"] is False

        maint = _open_maintenance(client, admin, v["id"], plan["id"], 10_000)
        items = _items_by_name(client, admin, maint["id"])
        assert items["A Maddesi"]["is_completed"] is False
        assert items["B Maddesi"]["is_completed"] is True
        assert items["B Maddesi"]["completed_by"] is None
    finally:
        _cleanup(client, admin, v, plan)


def test_vehicle_exception_only_affects_that_single_vehicle(client, admin_token):
    """Genel varsayılan zorunlu kalırken, TEK bir araç için istisna
    eklenirse yalnızca o araçta otomatik tiklenir; aynı modeldeki BAŞKA bir
    araçta madde normal (tiksiz, zorunlu) kalmaya devam eder."""
    admin = admin_token
    v1, plan, node_a, node_b = _setup(client, admin, "ZorunluModelIstisna", code="TM2-IST1")
    v2 = client.post(
        "/api/vehicles", headers=auth(admin),
        json={"code": "TM2-IST2", "name": "İkinci Araç", "current_km": 5_000, "model": "ZorunluModelIstisna"},
    ).json()
    assert "id" in v2, v2
    try:
        r = client.post(
            f"/api/tree/{node_a['id']}/vehicle-exceptions", headers=auth(admin),
            json={"vehicle_id": v1["id"], "is_mandatory": False},
        )
        assert r.status_code == 200, r.text
        exc = r.json()
        assert exc["is_mandatory"] is False
        assert exc["vehicle"]["id"] == v1["id"]

        listed = client.get(
            f"/api/tree/{node_a['id']}/vehicle-exceptions", headers=auth(admin)
        ).json()
        assert len(listed) == 1
        assert listed[0]["vehicle"]["code"] == v1["code"]

        maint_v1 = _open_maintenance(client, admin, v1["id"], plan["id"], 10_000)
        items_v1 = _items_by_name(client, admin, maint_v1["id"])
        assert items_v1["A Maddesi"]["is_completed"] is True  # istisna sayesinde

        maint_v2 = _open_maintenance(client, admin, v2["id"], plan["id"], 10_000)
        items_v2 = _items_by_name(client, admin, maint_v2["id"])
        assert items_v2["A Maddesi"]["is_completed"] is False  # genel varsayılan (zorunlu) geçerli

        # İstisnayı kaldır -> bundan sonra açılan iş emirlerinde v1 de normale döner.
        del_r = client.delete(f"/api/tree/vehicle-exceptions/{exc['id']}", headers=auth(admin))
        assert del_r.status_code == 200, del_r.text
        maint_v1_again = _open_maintenance(client, admin, v1["id"], plan["id"], 20_000)
        items_v1_again = _items_by_name(client, admin, maint_v1_again["id"])
        assert items_v1_again["A Maddesi"]["is_completed"] is False
    finally:
        client.delete(f"/api/vehicles/{v2['id']}", headers=auth(admin))
        _cleanup(client, admin, v1, plan)


def test_toggling_general_mandatory_retroactively_updates_open_maintenance(client, admin_token, usta_token):
    """Zaten AÇIK (tamamlanmamış) bir iş emrinde bir maddeyi sonradan
    zorunlu değil yaparsak, o açık iş emrindeki karşılık gelen madde de
    HEMEN otomatik tiklenmeli — yeni bir bakım açılmasını beklememeli.
    Tekrar zorunlu yapılınca (usta hiç dokunmadıysa) otomatik tik geri
    kalkmalı."""
    admin = admin_token
    v, plan, node_a, node_b = _setup(client, admin, "ZorunluModelRetro")
    try:
        maint = _open_maintenance(client, admin, v["id"], plan["id"], 10_000)
        items = _items_by_name(client, admin, maint["id"])
        assert items["B Maddesi"]["is_completed"] is False  # henüz zorunlu

        r = client.patch(
            f"/api/tree/{node_b['id']}/mandatory", headers=auth(admin),
            json={"is_mandatory": False},
        )
        assert r.status_code == 200, r.text

        items_after = _items_by_name(client, admin, maint["id"])
        assert items_after["B Maddesi"]["is_completed"] is True
        assert items_after["B Maddesi"]["completed_by"] is None
        assert items_after["A Maddesi"]["is_completed"] is False  # etkilenmedi

        r = client.patch(
            f"/api/tree/{node_b['id']}/mandatory", headers=auth(admin),
            json={"is_mandatory": True},
        )
        assert r.status_code == 200, r.text
        items_reverted = _items_by_name(client, admin, maint["id"])
        assert items_reverted["B Maddesi"]["is_completed"] is False
    finally:
        _cleanup(client, admin, v, plan)


def test_real_technician_completion_not_overridden_by_mandatory_toggle(client, admin_token, usta_token):
    """Bir maddeyi usta GERÇEKTEN tamamladıysa (completed_by dolu), bu
    maddenin zorunlu/zorunlu-değil ayarı sonradan değiştirilse bile o
    tamamlamaya ASLA dokunulmamalı."""
    admin = admin_token
    v, plan, node_a, node_b = _setup(client, admin, "ZorunluModelKoru")
    try:
        maint = _open_maintenance(client, admin, v["id"], plan["id"], 10_000)
        items = _items_by_name(client, admin, maint["id"])
        r = client.post(f"/api/items/{items['A Maddesi']['id']}/complete", headers=auth(usta_token))
        assert r.status_code == 200, r.text

        client.patch(f"/api/tree/{node_a['id']}/mandatory", headers=auth(admin), json={"is_mandatory": False})
        items_mid = _items_by_name(client, admin, maint["id"])
        assert items_mid["A Maddesi"]["is_completed"] is True
        assert items_mid["A Maddesi"]["completed_by"] is not None

        client.patch(f"/api/tree/{node_a['id']}/mandatory", headers=auth(admin), json={"is_mandatory": True})
        items_after = _items_by_name(client, admin, maint["id"])
        assert items_after["A Maddesi"]["is_completed"] is True
        assert items_after["A Maddesi"]["completed_by"] is not None
    finally:
        _cleanup(client, admin, v, plan)


def test_vehicle_exception_retroactively_updates_only_that_vehicles_open_maintenance(client, admin_token):
    admin = admin_token
    v1, plan, node_a, node_b = _setup(client, admin, "ZorunluModelRetroIst")
    v2 = client.post(
        "/api/vehicles", headers=auth(admin),
        json={"code": "TM2-RETRO2", "name": "İkinci Araç", "current_km": 5_000, "model": "ZorunluModelRetroIst"},
    ).json()
    try:
        maint1 = _open_maintenance(client, admin, v1["id"], plan["id"], 10_000)
        maint2 = _open_maintenance(client, admin, v2["id"], plan["id"], 10_000)

        r = client.post(
            f"/api/tree/{node_a['id']}/vehicle-exceptions", headers=auth(admin),
            json={"vehicle_id": v1["id"], "is_mandatory": False},
        )
        assert r.status_code == 200, r.text
        exc = r.json()

        items1 = _items_by_name(client, admin, maint1["id"])
        items2 = _items_by_name(client, admin, maint2["id"])
        assert items1["A Maddesi"]["is_completed"] is True   # v1: istisna sayesinde hemen tiklendi
        assert items2["A Maddesi"]["is_completed"] is False  # v2: etkilenmedi

        del_r = client.delete(f"/api/tree/vehicle-exceptions/{exc['id']}", headers=auth(admin))
        assert del_r.status_code == 200, del_r.text
        items1_after = _items_by_name(client, admin, maint1["id"])
        assert items1_after["A Maddesi"]["is_completed"] is False  # istisna kalkınca geri zorunlu
    finally:
        client.delete(f"/api/vehicles/{v2['id']}", headers=auth(admin))
        _cleanup(client, admin, v1, plan)
