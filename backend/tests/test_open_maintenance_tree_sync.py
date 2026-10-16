import io

from PIL import Image

from .conftest import auth


def _create_tree(client, admin, model, interval, suffix):
    plan = client.post(
        "/api/plans",
        headers=auth(admin),
        json={
            "name": f"{model} {interval} km",
            "plan_type": "km",
            "interval_km": interval,
            "vehicle_model": model,
        },
    ).json()
    root = client.post(
        "/api/tree",
        headers=auth(admin),
        json={"name": "Araç İçi", "plan_id": plan["id"]},
    ).json()
    component = client.post(
        "/api/tree",
        headers=auth(admin),
        json={"name": "Fren", "parent_id": root["id"]},
    ).json()
    leaf = client.post(
        "/api/tree",
        headers=auth(admin),
        json={"name": f"Kontrol {suffix}", "parent_id": component["id"]},
    ).json()
    return plan, root, component, leaf


def _open_maintenance(client, admin, model, plan, suffix):
    vehicle = client.post(
        "/api/vehicles",
        headers=auth(admin),
        json={
            "code": f"TREE-SYNC-{suffix}",
            "name": "Bakım Ağacı Eşitleme Aracı",
            "current_km": 10_000,
            "model": model,
        },
    ).json()
    maintenance = client.post(
        "/api/maintenances/manual",
        headers=auth(admin),
        json={"vehicle_id": vehicle["id"], "plan_id": plan["id"], "target_km": 20_000},
    )
    assert maintenance.status_code == 200, maintenance.text
    return vehicle, maintenance.json()


def _walk(items):
    for item in items:
        yield item
        yield from _walk(item["children"])


def _image_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (12, 12), color="red").save(buffer, format="PNG")
    return buffer.getvalue()


def test_open_maintenance_tracks_tree_edits_until_completion(client, admin_token):
    admin = admin_token
    model = "TreeSyncModel"
    plan, _, _, leaf = _create_tree(client, admin, model, 20_000, "original")
    vehicle, maintenance = _open_maintenance(client, admin, model, plan, "OPEN")
    try:
        detail = client.get(
            f"/api/maintenances/{maintenance['id']}", headers=auth(admin)
        ).json()
        original_item = next(item for item in _walk(detail["items"]) if item["name"] == leaf["name"])
        assert original_item["source_node_id"] == leaf["id"]

        created = client.post(
            "/api/tree",
            headers=auth(admin),
            json={"name": "Yeni kontrol", "parent_id": leaf["parent_id"]},
        )
        assert created.status_code == 200, created.text
        detail = client.get(
            f"/api/maintenances/{maintenance['id']}", headers=auth(admin)
        ).json()
        assert any(item["name"] == "Yeni kontrol" for item in _walk(detail["items"]))

        completed = client.post(
            f"/api/items/{original_item['id']}/complete", headers=auth(admin)
        )
        assert completed.status_code == 200, completed.text
        deleted = client.delete(f"/api/tree/{leaf['id']}", headers=auth(admin))
        assert deleted.status_code == 200, deleted.text

        detail = client.get(
            f"/api/maintenances/{maintenance['id']}", headers=auth(admin)
        ).json()
        remaining = list(_walk(detail["items"]))
        preserved = next(item for item in remaining if item["id"] == original_item["id"])
        assert preserved["is_completed"] is True
        assert preserved["source_node_id"] is None
        assert any(item["name"] == "Yeni kontrol" for item in remaining)
    finally:
        client.delete(f"/api/vehicles/{vehicle['id']}", headers=auth(admin))
        client.delete(f"/api/plans/{plan['id']}", headers=auth(admin))


def test_component_files_are_shared_across_periods_of_the_same_model(client, admin_token):
    admin = admin_token
    model = "TreeSyncSameModel"
    short_plan, _, short_component, _ = _create_tree(client, admin, model, 10_000, "short")
    long_plan, _, long_component, _ = _create_tree(client, admin, model, 20_000, "long")
    vehicle, maintenance = _open_maintenance(client, admin, model, long_plan, "SAME")

    other_model = "TreeSyncOtherModel"
    other_plan, _, other_component, _ = _create_tree(client, admin, other_model, 10_000, "other")
    try:
        note_update = client.patch(
            f"/api/tree/{long_component['id']}",
            headers=auth(admin),
            json={"note": "Bakım ağacındaki güncel açıklama"},
        )
        assert note_update.status_code == 200, note_update.text

        shared_file = client.post(
            f"/api/tree/{short_component['id']}/attachments",
            headers=auth(admin),
            files={"file": ("fren.png", _image_bytes(), "image/png")},
        )
        assert shared_file.status_code == 200, shared_file.text

        unrelated_file = client.post(
            f"/api/tree/{other_component['id']}/attachments",
            headers=auth(admin),
            files={"file": ("diger.png", _image_bytes(), "image/png")},
        )
        assert unrelated_file.status_code == 200, unrelated_file.text

        detail = client.get(
            f"/api/maintenances/{maintenance['id']}", headers=auth(admin)
        ).json()
        component = next(item for item in _walk(detail["items"]) if item["name"] == "Fren")
        assert component["note"] == "Bakım ağacındaki güncel açıklama"
        assert any(a["id"] == shared_file.json()["id"] for a in component["attachments"])
        assert all(a["id"] != unrelated_file.json()["id"] for a in component["attachments"])
    finally:
        client.delete(f"/api/vehicles/{vehicle['id']}", headers=auth(admin))
        for plan in (short_plan, long_plan, other_plan):
            client.delete(f"/api/plans/{plan['id']}", headers=auth(admin))
