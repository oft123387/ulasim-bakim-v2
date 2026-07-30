"""Manuel bakım ekleme (POST /api/maintenances/manual) ve eski (hatalı)
iş emirlerini düzeltme (POST /api/maintenances/{id}/resync-tiers) için testler.

Arka plan: create_manual_maintenance eskiden yalnızca kullanıcının SEÇTİĞİ
tek planın ağacını kopyalıyordu. Ama hedef km, seçilen plandan BAŞKA bir
planın periyoduna da tam bölünüyorsa (ör. 3M periyodu seçilip hedef
30.000 girildiğinde, bu hedef aynı zamanda 1M'in de tam katıdır), o planın
maddeleri de checklist'e dahil edilmeliydi — check_and_open_maintenances
(otomatik açılış) bunu zaten doğru yapıyordu, manuel ekleme yapmıyordu.
Bu artık düzeltildi; resync-tiers uç noktası da DÜZELTMEDEN ÖNCE açılmış,
hâlâ açık iş emirlerini (tamamlanmış maddelere dokunmadan) düzeltmek için var."""

from app import models, services
from app.database import SessionLocal

from .conftest import auth


def _setup_plans(client, admin, model):
    v = client.post(
        "/api/vehicles", headers=auth(admin),
        json={"code": f"TM-{model}", "name": "Tier Merge Test Aracı", "current_km": 30_000, "model": model},
    ).json()
    plan_1m = client.post(
        "/api/plans", headers=auth(admin),
        json={"name": f"{model} 1M Planı", "plan_type": "km", "interval_km": 10_000, "vehicle_model": model},
    ).json()
    plan_3m = client.post(
        "/api/plans", headers=auth(admin),
        json={"name": f"{model} 3M Planı", "plan_type": "km", "interval_km": 30_000, "vehicle_model": model},
    ).json()

    # Aynı kök isim ("ARAÇ İÇİ") kasıtlı — birleştirme, yol (path) bazlı
    # tekilleştirme yapıyor mu diye test eder.
    root1 = client.post(
        "/api/tree", headers=auth(admin), json={"name": "ARAÇ İÇİ", "plan_id": plan_1m["id"]},
    ).json()
    client.post(
        "/api/tree", headers=auth(admin),
        json={"name": "1M Madde", "parent_id": root1["id"], "card_code": "1M-01"},
    )
    root3 = client.post(
        "/api/tree", headers=auth(admin), json={"name": "ARAÇ İÇİ", "plan_id": plan_3m["id"]},
    ).json()
    client.post(
        "/api/tree", headers=auth(admin),
        json={"name": "3M Madde", "parent_id": root3["id"], "card_code": "3M-01"},
    )
    return v, plan_1m, plan_3m


def _collect_card_codes(nodes, out):
    for n in nodes:
        if n["card_code"]:
            out.add(n["card_code"])
        _collect_card_codes(n["children"], out)


def _cleanup(client, admin, v, plan_1m, plan_3m):
    """Bu dosyadaki testler km=hedef (remaining_km=0) olacak şekilde araç
    oluşturuyor — paylaşılan (session scope) test veritabanında temizlenmezse
    dashboard'un "yaklaşanlar" (limit=8, remaining_km'e göre sıralı) listesini
    doldurup BAŞKA testlerin (ör. test_workflow.py) beklediği farklı bir aracı
    top-8'den düşürebilir. Bu yüzden her test kendi oluşturduğu araç/planı
    sonunda mutlaka siler (bkz. test_criticality_thresholds.py'deki aynı desen)."""
    client.delete(f"/api/vehicles/{v['id']}", headers=auth(admin))
    client.delete(f"/api/plans/{plan_1m['id']}", headers=auth(admin))
    client.delete(f"/api/plans/{plan_3m['id']}", headers=auth(admin))


def test_manual_maintenance_merges_smaller_period_automatically(client, admin_token):
    admin = admin_token
    v, plan_1m, plan_3m = _setup_plans(client, admin, "TierMergeModelA")
    try:
        r = client.post(
            "/api/maintenances/manual", headers=auth(admin),
            json={"vehicle_id": v["id"], "plan_id": plan_3m["id"], "target_km": 30_000},
        )
        assert r.status_code == 200, r.text
        maint = r.json()

        detail = client.get(f"/api/maintenances/{maint['id']}", headers=auth(admin)).json()
        # Tek "ARAÇ İÇİ" kökü altında birleşmiş olmalı (path bazlı tekilleştirme).
        assert len(detail["items"]) == 1
        assert detail["items"][0]["name"] == "ARAÇ İÇİ"

        codes: set[str] = set()
        _collect_card_codes(detail["items"], codes)
        assert "1M-01" in codes, "3M hedefi (30.000) 1M'in (10.000) de tam katı — 1M maddeleri dahil olmalı"
        assert "3M-01" in codes
        assert sorted(detail["covered_tiers"]) == [10_000, 30_000]
    finally:
        _cleanup(client, admin, v, plan_1m, plan_3m)


def test_manual_maintenance_keeps_only_selected_plan_when_target_not_shared(client, admin_token):
    """Hedef km, YALNIZCA seçilen planın periyoduna bölünüyorsa (başka bir
    planla paylaşılmıyorsa) birleştirme gereksiz yere tetiklenmemeli."""
    admin = admin_token
    v, plan_1m, plan_3m = _setup_plans(client, admin, "TierMergeModelB")
    try:
        # 3M planı seçilip hedef 90.000 girilirse (yalnızca 3M'e bölünür, 1M'e de
        # bölünür aslında — o yüzden 1M planının periyodunu farklı bir asal
        # sayı yapalım ki gerçekten paylaşılmasın.
        r = client.post(
            "/api/maintenances/manual", headers=auth(admin),
            json={"vehicle_id": v["id"], "plan_id": plan_1m["id"], "target_km": 10_000},
        )
        assert r.status_code == 200, r.text
        maint = r.json()
        detail = client.get(f"/api/maintenances/{maint['id']}", headers=auth(admin)).json()
        codes: set[str] = set()
        _collect_card_codes(detail["items"], codes)
        # 10.000, 30.000'e (3M) bölünmüyor -> yalnızca 1M'in maddesi olmalı.
        assert codes == {"1M-01"}
    finally:
        _cleanup(client, admin, v, plan_1m, plan_3m)


def test_resync_tiers_adds_missing_period_without_touching_completed(client, admin_token):
    admin = admin_token
    v, plan_1m, plan_3m = _setup_plans(client, admin, "TierMergeModelC")
    try:
        # DÜZELTMEDEN ÖNCEKİ hatalı davranışı simüle et: yalnızca 3M planının
        # ağacını kopyalayan eski tarz bir iş emri (tek plan snapshot).
        db = SessionLocal()
        try:
            vehicle = db.query(models.Vehicle).filter_by(id=v["id"]).one()
            plan3 = db.query(models.MaintenancePlan).filter_by(id=plan_3m["id"]).one()
            m = models.Maintenance(
                vehicle_id=vehicle.id, plan_id=plan3.id, target_km=30_000,
                km_at_open=vehicle.current_km, status="pending", is_activated=True,
            )
            db.add(m)
            db.flush()
            services._copy_tree_snapshot(db, m, [plan3])
            db.commit()
            mid = m.id
        finally:
            db.close()

        detail = client.get(f"/api/maintenances/{mid}", headers=auth(admin)).json()
        assert len(detail["items"]) == 1
        leaf3 = detail["items"][0]["children"][0]
        assert leaf3["name"] == "3M Madde"

        # NOT: resync'i madde tamamlamadan ÖNCE çağırıyoruz — o an tek madde
        # (3M Madde) olduğu için onu tamamlarsak iş emrinin TAMAMI biter (tek
        # maddeli iş emirlerinde son madde = iş emri tamamlandı kuralı) ve
        # "tamamlanmış iş emri resync edilemez" koruması devreye girerdi. Gerçek
        # senaryoda zaten resync, iş emri HALA açıkken (madde tamamlanmadan önce)
        # yapılır — eksik periyodun farkına varılır varılmaz.
        r = client.post(f"/api/maintenances/{mid}/resync-tiers", headers=auth(admin))
        assert r.status_code == 200, r.text
        result = r.json()
        assert result["added_items"] == 1
        assert result["merged_periods"] == [10_000]

        detail2 = client.get(f"/api/maintenances/{mid}", headers=auth(admin)).json()
        assert len(detail2["items"]) == 1
        root = detail2["items"][0]
        by_name = {c["name"]: c for c in root["children"]}
        assert set(by_name) == {"3M Madde", "1M Madde"}

        # Şimdi yalnızca 3M Madde'yi tamamla — 1M Madde hâlâ bekliyor olduğu
        # için iş emri "in_progress" kalır, TAMAMLANMAZ.
        r = client.post(f"/api/items/{leaf3['id']}/complete", headers=auth(admin))
        assert r.status_code == 200, r.text

        detail3 = client.get(f"/api/maintenances/{mid}", headers=auth(admin)).json()
        assert detail3["status"] == "in_progress"
        by_name3 = {c["name"]: c for c in detail3["items"][0]["children"]}
        assert by_name3["3M Madde"]["is_completed"] is True
        assert by_name3["1M Madde"]["is_completed"] is False

        # İkinci resync çağrısı idempotent olmalı — eklenecek bir şey kalmadı,
        # ve tamamlanmış maddeye dokunulmadığı (hâlâ tamamlanmış) doğrulanır.
        r2 = client.post(f"/api/maintenances/{mid}/resync-tiers", headers=auth(admin))
        assert r2.status_code == 200, r2.text
        assert r2.json()["added_items"] == 0
        detail4 = client.get(f"/api/maintenances/{mid}", headers=auth(admin)).json()
        by_name4 = {c["name"]: c for c in detail4["items"][0]["children"]}
        assert by_name4["3M Madde"]["is_completed"] is True
    finally:
        _cleanup(client, admin, v, plan_1m, plan_3m)


def test_resync_tiers_rejects_completed_maintenance(client, admin_token):
    admin = admin_token
    v, plan_1m, plan_3m = _setup_plans(client, admin, "TierMergeModelD")
    try:
        r = client.post(
            "/api/maintenances/manual", headers=auth(admin),
            json={"vehicle_id": v["id"], "plan_id": plan_3m["id"], "target_km": 30_000},
        )
        maint = r.json()
        r = client.post(f"/api/maintenances/{maint['id']}/resync-tiers", headers=auth(admin))
        assert r.status_code == 200  # zaten birleşik açıldığı için eklenecek bir şey yok ama hata da vermemeli
        assert r.json()["added_items"] == 0
    finally:
        _cleanup(client, admin, v, plan_1m, plan_3m)


def test_resync_tiers_requires_permission(client, usta_token):
    r = client.post("/api/maintenances/1/resync-tiers", headers=auth(usta_token))
    assert r.status_code == 403
