"""is_catalog_only=True işaretli bir plan (kendi başına bakım TETİKLEMEZ ve
kademeli/birleşik bakım hesabına DAHİL EDİLMEZ) davranışının hâlâ doğru
çalıştığını doğrular — bu davranışın kendisi kasıtlıdır, değiştirilmedi."""

from app import models, services
from app.database import SessionLocal

from .conftest import auth


def _setup(client, admin, model, code):
    v = client.post(
        "/api/vehicles", headers=auth(admin),
        json={"code": code, "name": "Katalog-Only Test Aracı", "current_km": 60_000, "model": model},
    ).json()
    assert "id" in v, v

    def _plan(name, interval_km, vehicle_model=None):
        p = client.post(
            "/api/plans", headers=auth(admin),
            json={"name": name, "plan_type": "km", "interval_km": interval_km,
                  "vehicle_model": model if vehicle_model is None else vehicle_model},
        ).json()
        assert "id" in p, p
        return p

    plan_1m = _plan(f"{model} 1M Planı", 10_000)
    plan_3m = _plan(f"{model} 3M Planı", 30_000)
    plan_6m = _plan(f"{model} 6M Planı", 60_000)

    for pid, label in [(plan_1m["id"], "1M Madde"), (plan_3m["id"], "3M Madde"), (plan_6m["id"], "6M Madde")]:
        root = client.post(
            "/api/tree", headers=auth(admin), json={"name": "ARAÇ İÇİ", "plan_id": pid},
        ).json()
        client.post(
            "/api/tree", headers=auth(admin),
            json={"name": label, "parent_id": root["id"], "card_code": f"{label}-01"},
        )
    return v, plan_1m, plan_3m, plan_6m


def _cleanup(client, admin, v, plans):
    client.delete(f"/api/vehicles/{v['id']}", headers=auth(admin))
    for p in plans:
        client.delete(f"/api/plans/{p['id']}", headers=auth(admin))


def test_catalog_only_plan_excluded_from_merge(client, admin_token):
    admin = admin_token
    model = "CatalogOnlyBugModel"
    v, plan_1m, plan_3m, plan_6m = _setup(client, admin, model, code="CO-BUG")
    try:
        db = SessionLocal()
        try:
            p3 = db.query(models.MaintenancePlan).filter_by(id=plan_3m["id"]).one()
            p3.is_catalog_only = True
            db.commit()
        finally:
            db.close()

        visible = client.get("/api/plans", headers=auth(admin)).json()
        assert all(p["id"] != plan_3m["id"] for p in visible)

        m = client.post(
            "/api/maintenances/manual", headers=auth(admin),
            json={"vehicle_id": v["id"], "plan_id": plan_6m["id"], "target_km": 60_000},
        ).json()
        assert "id" in m, m
        assert sorted(m["covered_tiers"]) == [10_000, 60_000]  # 30.000 (3M) katalog-only, dahil değil

        r = client.post(f"/api/maintenances/{m['id']}/resync-tiers", headers=auth(admin))
        assert r.status_code == 200, r.text
        result = r.json()
        assert result["added_items"] == 0
        # Teşhis: 3M planı listede görünmeli ve DIŞLANMA nedeni "katalog-
        # amaçlı" olarak açıkça belirtilmeli — "3M gelmiyor ama neden
        # belli değil" durumunu çözen özellik.
        diag_3m = next(d for d in result["diagnostics"] if d["plan_id"] == plan_3m["id"])
        assert diag_3m["is_catalog_only"] is True
        assert diag_3m["divides_target"] is True
        assert "katalog" in diag_3m["excluded_reason"]
        diag_1m = next(d for d in result["diagnostics"] if d["plan_id"] == plan_1m["id"])
        assert diag_1m["excluded_reason"] is None
    finally:
        _cleanup(client, admin, v, [plan_1m, plan_3m, plan_6m])
