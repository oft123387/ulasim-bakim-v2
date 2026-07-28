"""Bakım Ağacı sayfasındaki toplu silme uçlarının testleri:

  - DELETE /api/tree/plan/{plan_id}: tek bir planın TÜM ağacını (kök
    düğümler + alt maddeler) siler; planın kendisi ve varsa geçmiş iş
    emirleri KORUNUR.
  - DELETE /api/tree: veritabanındaki HER plana ait TÜM ağaçları tek
    seferde siler; planların kendisi yine korunur.
"""

from .conftest import auth

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


def test_delete_all_trees_wipes_every_plan_but_keeps_plans(client, admin_token):
    admin = admin_token
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
