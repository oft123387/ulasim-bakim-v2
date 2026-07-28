"""POST /api/vehicle-models/merge — yanlış yazılmış/eski bir araç modelini
gerçek filo modeliyle birleştirme (ör. Excel'den "BOZANKAYA" gelmiş, filodaki
gerçek model "Bozankaya Tip-1" gibi durumlar için)."""

from .conftest import auth


def test_merge_moves_vehicles_and_renames_nonconflicting_plan(client, admin_token):
    admin = admin_token

    r = client.post(
        "/api/vehicles",
        headers=auth(admin),
        json={"code": "9101", "name": "Test Tramvay", "current_km": 0, "model": "STRAY MODEL"},
    )
    assert r.status_code == 200, r.text
    vehicle = r.json()

    r = client.post(
        "/api/plans",
        headers=auth(admin),
        json={"name": "77.000 km Bakımı", "interval_km": 77_000, "vehicle_model": "STRAY MODEL"},
    )
    assert r.status_code == 200, r.text
    plan = r.json()

    r = client.post(
        "/api/vehicle-models/merge",
        headers=auth(admin),
        json={"from_model": "STRAY MODEL", "to_model": "Bozankaya Tip-1"},
    )
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["vehicles_moved"] == 1
    assert len(result["plans_renamed"]) == 1
    assert len(result["plans_merged"]) == 0

    vehicles = client.get("/api/vehicles", headers=auth(admin)).json()
    v = next(x for x in vehicles if x["id"] == vehicle["id"])
    assert v["model"] == "Bozankaya Tip-1"

    p = client.get("/api/plans", headers=auth(admin)).json()
    moved_plan = next(x for x in p if x["id"] == plan["id"])
    assert moved_plan["vehicle_model"] == "Bozankaya Tip-1"


def test_merge_rejects_same_model(client, admin_token):
    admin = admin_token
    r = client.post(
        "/api/vehicle-models/merge",
        headers=auth(admin),
        json={"from_model": "Sirio", "to_model": "Sirio"},
    )
    assert r.status_code == 422
