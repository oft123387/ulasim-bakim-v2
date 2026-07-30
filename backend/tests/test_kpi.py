"""Faz 18 — KPI çekme özelliği testleri.

Kullanıcı isteği: hem uygulama içi bir KPI sayfası hem de Excel dışa
aktarma; dört metrik kategorisi (tamamlanma oranı/süresi, geciken/kritik
bakım sayıları, araç/model bazlı performans, personel/vardiya performansı);
esnek tarih aralığı seçimi. Bu dosya GET /api/kpi ve GET /api/kpi/export
uç noktalarını (services.compute_kpi_report üzerinden) test eder.

Maddeleri güvenilir şekilde elde etmek için (otomatik-açma + genel yedek
ağaç yerine) test_item_exec_attachments.py'deki desenle aynı şekilde:
kendi ağacımızı (POST /api/tree) ve manuel bakım oluşturmayı
(POST /api/maintenances/manual, doğrudan aktif/is_activated=True açar)
kullanıyoruz — böylece madde sayısı/kimliği tam olarak kontrol altında."""

import io
from datetime import date, timedelta

import openpyxl

from .conftest import auth


def _make_maintenance_with_item(client, admin, model, current_km, target_km, interval_km=10_000):
    v = client.post(
        "/api/vehicles", headers=auth(admin),
        json={"code": f"KPI-{model}", "name": "KPI Test Aracı", "current_km": current_km, "model": model},
    ).json()
    plan = client.post(
        "/api/plans", headers=auth(admin),
        json={"plan_type": "km", "interval_km": interval_km, "vehicle_model": model},
    ).json()
    root = client.post(
        "/api/tree", headers=auth(admin), json={"name": "ARAÇ İÇİ", "plan_id": plan["id"]},
    ).json()
    client.post(
        "/api/tree", headers=auth(admin),
        json={"name": "Test Maddesi", "parent_id": root["id"], "card_code": "KPI-01"},
    )
    m = client.post(
        "/api/maintenances/manual", headers=auth(admin),
        json={"vehicle_id": v["id"], "plan_id": plan["id"], "target_km": target_km},
    ).json()
    detail = client.get(f"/api/maintenances/{m['id']}", headers=auth(admin)).json()
    item = detail["items"][0]["children"][0]
    return v, plan, m, item


def _cleanup(client, admin, v, plan):
    client.delete(f"/api/vehicles/{v['id']}", headers=auth(admin))
    client.delete(f"/api/plans/{plan['id']}", headers=auth(admin))


def _get_usta_id(client, admin) -> int:
    users = client.get("/api/users", headers=auth(admin)).json()
    return next(u["id"] for u in users if u["username"] == "usta")


def test_kpi_completion_rate_and_vehicle_model_breakdown(client, admin_token, usta_token):
    admin = admin_token
    model = "KPIModelA"
    v, plan, m, item = _make_maintenance_with_item(client, admin, model, current_km=9_800, target_km=10_000)
    try:
        usta_id = _get_usta_id(client, admin)
        client.patch(
            f"/api/maintenances/{m['id']}/assign", headers=auth(admin),
            json={"assigned_to_id": usta_id},
        )
        r = client.post(f"/api/items/{item['id']}/complete", headers=auth(usta_token))
        assert r.status_code == 200, r.text
        assert r.json()["maintenance_status"] == "completed"

        today = date.today()
        wide_from = (today - timedelta(days=1)).isoformat()
        wide_to = (today + timedelta(days=1)).isoformat()
        kpi = client.get(
            "/api/kpi", headers=auth(admin),
            params={"date_from": wide_from, "date_to": wide_to},
        ).json()

        assert kpi["completion"]["opened_in_range"] >= 1
        assert kpi["completion"]["completed_of_opened"] >= 1
        assert kpi["completion"]["completed_in_range"] >= 1
        assert kpi["completion"]["avg_completion_days"] is not None
        assert kpi["completion"]["avg_completion_days"] >= 0

        model_row = next(
            (row for row in kpi["by_vehicle_model"] if row["vehicle_model"] == model), None
        )
        assert model_row is not None
        assert model_row["completed"] >= 1

        person_row = next(
            (row for row in kpi["by_person"] if row["user"]["username"] == "usta"), None
        )
        assert person_row is not None
        assert person_row["items_completed_in_range"] >= 1
        assert person_row["maintenances_completed_in_range"] >= 1

        # usta'nın (varsayılan) vardiyası yok -> "Vardiyasız" kırılımında görünmeli.
        vardiyasiz = next(
            (row for row in kpi["by_shift"] if row["shift_name"] == "Vardiyasız"), None
        )
        assert vardiyasiz is not None
        assert vardiyasiz["items_completed_in_range"] >= 1
    finally:
        _cleanup(client, admin, v, plan)


def test_kpi_date_range_excludes_out_of_range_maintenances(client, admin_token):
    admin = admin_token
    model = "KPIModelB"
    v, plan, m, item = _make_maintenance_with_item(client, admin, model, current_km=9_800, target_km=10_000)
    try:
        far_past_from = (date.today() - timedelta(days=400)).isoformat()
        far_past_to = (date.today() - timedelta(days=390)).isoformat()
        kpi = client.get(
            "/api/kpi", headers=auth(admin),
            params={"date_from": far_past_from, "date_to": far_past_to},
        ).json()
        assert not any(
            row["vehicle_model"] == model and row["opened"] for row in kpi["by_vehicle_model"]
        )
    finally:
        _cleanup(client, admin, v, plan)


def test_kpi_critical_and_overdue_counts_reflect_current_state(client, admin_token):
    admin = admin_token
    model = "KPIModelC"
    # Hedeften (10.000) uzak başlat (kalan 5.000 km, kritik eşiği olan
    # %15/%5'in (1.500/500 km) çok üzerinde) — "before" ölçümünde bu
    # bakımın kendisi henüz kritik/gecikmiş sayılmamalı.
    v, plan, m, item = _make_maintenance_with_item(
        client, admin, model, current_km=5_000, target_km=10_000, interval_km=10_000,
    )
    try:
        before = client.get("/api/kpi", headers=auth(admin)).json()["critical"]

        # Aracı hedefin (10.000) ötesine taşı -> remaining_km negatif ->
        # hem "gecikmiş" hem (interval'in %5'i = 500 km eşiğinin altında
        # olduğu için) "çok kritik" sayılır.
        r = client.put(
            f"/api/vehicles/{v['id']}/km", headers=auth(admin), json={"current_km": 10_500}
        )
        assert r.status_code == 200, r.text

        after = client.get("/api/kpi", headers=auth(admin)).json()["critical"]
        assert after["overdue_count"] >= before["overdue_count"] + 1
        assert after["very_critical_count"] >= before["very_critical_count"] + 1
    finally:
        _cleanup(client, admin, v, plan)


def test_kpi_requires_view_dashboard_analytics_permission(client, admin_token, usta_token):
    admin = admin_token
    r = client.patch(
        "/api/permissions", headers=auth(admin),
        json={"role": "technician", "permission": "view_dashboard_analytics", "allowed": False},
    )
    assert r.status_code == 200, r.text
    try:
        r = client.get("/api/kpi", headers=auth(usta_token))
        assert r.status_code == 403
    finally:
        client.patch(
            "/api/permissions", headers=auth(admin),
            json={"role": "technician", "permission": "view_dashboard_analytics", "allowed": True},
        )


def test_kpi_export_requires_export_excel_permission(client, admin_token, usta_token):
    admin = admin_token
    r = client.patch(
        "/api/permissions", headers=auth(admin),
        json={"role": "technician", "permission": "export_excel", "allowed": False},
    )
    assert r.status_code == 200, r.text
    try:
        r = client.get("/api/kpi/export", headers=auth(usta_token))
        assert r.status_code == 403
    finally:
        client.patch(
            "/api/permissions", headers=auth(admin),
            json={"role": "technician", "permission": "export_excel", "allowed": True},
        )


def test_kpi_export_returns_valid_multisheet_xlsx(client, admin_token):
    admin = admin_token
    r = client.get("/api/kpi/export", headers=auth(admin))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Özet", "Araç Modeli", "Araçlar", "Personel", "Vardiya"]
