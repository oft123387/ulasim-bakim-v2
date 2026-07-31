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
import pytest

from .conftest import auth


@pytest.fixture(scope="module")
def engineer_token(client, admin_token):
    client.post(
        "/api/users", headers=auth(admin_token),
        json={"username": "kpi_muh", "full_name": "KPI Test Mühendis", "role": "engineer", "password": "kpimuhpass1"},
    )
    r = client.post("/api/auth/login", data={"username": "kpi_muh", "password": "kpimuhpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


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


def test_kpi_blocked_for_technician_even_with_permission_on(client, admin_token, usta_token):
    """Usta rolü, view_dashboard_analytics izni AÇIK olsa bile (bu izin
    varsayılan olarak açıktır çünkü aynı zamanda Dashboard'daki analiz
    grafiğini de kontrol eder) KPI raporunu göremez/dışa aktaramaz — bu,
    yönetici rollerine (admin/mühendis/amir) özel, sabit bir kısıtlamadır."""
    admin = admin_token
    perms = client.get("/api/permissions", headers=auth(admin)).json()
    assert perms["technician"]["view_dashboard_analytics"] is True
    assert perms["technician"]["export_excel"] is True

    r = client.get("/api/kpi", headers=auth(usta_token))
    assert r.status_code == 403
    r = client.get("/api/kpi/export", headers=auth(usta_token))
    assert r.status_code == 403


def test_kpi_requires_view_dashboard_analytics_permission_for_non_technician(client, admin_token, engineer_token):
    admin = admin_token
    r = client.patch(
        "/api/permissions", headers=auth(admin),
        json={"role": "engineer", "permission": "view_dashboard_analytics", "allowed": False},
    )
    assert r.status_code == 200, r.text
    try:
        r = client.get("/api/kpi", headers=auth(engineer_token))
        assert r.status_code == 403
    finally:
        client.patch(
            "/api/permissions", headers=auth(admin),
            json={"role": "engineer", "permission": "view_dashboard_analytics", "allowed": True},
        )


def test_kpi_export_requires_export_excel_permission_for_non_technician(client, admin_token, engineer_token):
    admin = admin_token
    r = client.patch(
        "/api/permissions", headers=auth(admin),
        json={"role": "engineer", "permission": "export_excel", "allowed": False},
    )
    assert r.status_code == 200, r.text
    try:
        r = client.get("/api/kpi/export", headers=auth(engineer_token))
        assert r.status_code == 403
    finally:
        client.patch(
            "/api/permissions", headers=auth(admin),
            json={"role": "engineer", "permission": "export_excel", "allowed": True},
        )


def test_kpi_on_time_rate_km_based(client, admin_token, usta_token):
    """Zamanında bakım yapılan araç oranı KPI'si: km bazlı bir bakım,
    km_at_open planın KENDİ tolerans penceresi (varsayılan -%5/+%10,
    interval_km=10.000 için hedef ± [500, 1000]) İÇİNDEYSE "zamanında"
    sayılır; pencerenin DIŞINDA (ör. çok geç, hedef+1000'i aşmış) açılmışsa
    sayılmaz — ama her iki durumda da "değerlendirilen" (payda) sayısına
    dahildir."""
    admin = admin_token
    usta_id = _get_usta_id(client, admin)
    today = date.today()
    wide_from = (today - timedelta(days=1)).isoformat()
    wide_to = (today + timedelta(days=1)).isoformat()

    def _complete(item_id):
        r = client.post(f"/api/items/{item_id}/complete", headers=auth(usta_token))
        assert r.status_code == 200, r.text

    before = client.get(
        "/api/kpi", headers=auth(admin), params={"date_from": wide_from, "date_to": wide_to},
    ).json()["completion"]

    # Zamanında: km_at_open (9.800) pencere [9.500, 11.000] içinde.
    v1, plan1, m1, item1 = _make_maintenance_with_item(
        client, admin, "KPIOnTimeKm", current_km=9_800, target_km=10_000,
    )
    try:
        client.patch(f"/api/maintenances/{m1['id']}/assign", headers=auth(admin), json={"assigned_to_id": usta_id})
        _complete(item1["id"])

        mid = client.get(
            "/api/kpi", headers=auth(admin), params={"date_from": wide_from, "date_to": wide_to},
        ).json()["completion"]
        assert mid["on_time_count"] == before["on_time_count"] + 1
        assert mid["on_time_evaluated"] == before["on_time_evaluated"] + 1

        # Geç: km_at_open (11.500) pencerenin ÜST sınırını (11.000) aşmış —
        # bakım daha açıldığı anda tolerans penceresinin dışındaydı,
        # "zamanında" sayılmaz.
        v2, plan2, m2, item2 = _make_maintenance_with_item(
            client, admin, "KPILateKm", current_km=11_500, target_km=10_000,
        )
        try:
            client.patch(f"/api/maintenances/{m2['id']}/assign", headers=auth(admin), json={"assigned_to_id": usta_id})
            _complete(item2["id"])

            after = client.get(
                "/api/kpi", headers=auth(admin), params={"date_from": wide_from, "date_to": wide_to},
            ).json()["completion"]
            assert after["on_time_count"] == mid["on_time_count"]  # artmadı
            assert after["on_time_evaluated"] == mid["on_time_evaluated"] + 1
            assert after["on_time_rate_pct"] is not None
            assert 0 <= after["on_time_rate_pct"] <= 100
        finally:
            _cleanup(client, admin, v2, plan2)
    finally:
        _cleanup(client, admin, v1, plan1)


def test_kpi_on_time_rate_time_based(client, admin_token, usta_token):
    """Süre bazlı bakımlarda "zamanında" ölçütü completed_at<=target_date'dir."""
    admin = admin_token
    usta_id = _get_usta_id(client, admin)
    today = date.today()
    wide_from = (today - timedelta(days=1)).isoformat()
    wide_to = (today + timedelta(days=1)).isoformat()

    def _setup_time_plan(model):
        v = client.post(
            "/api/vehicles", headers=auth(admin),
            json={
                "code": f"KPIT-{model}", "name": "KPI Süre Test Aracı", "model": model,
                "commissioning_date": (today - timedelta(days=400)).isoformat(),
            },
        ).json()
        plan = client.post(
            "/api/plans", headers=auth(admin),
            json={"plan_type": "time", "interval_months": 12, "vehicle_model": model},
        ).json()
        root = client.post("/api/tree", headers=auth(admin), json={"name": "ARAÇ İÇİ", "plan_id": plan["id"]}).json()
        client.post("/api/tree", headers=auth(admin), json={"name": "Test Maddesi", "parent_id": root["id"]})
        return v, plan

    before = client.get(
        "/api/kpi", headers=auth(admin), params={"date_from": wide_from, "date_to": wide_to},
    ).json()["completion"]

    # Zamanında: hedef tarih bugün ya da ileri bir tarih.
    v1, plan1 = _setup_time_plan("KPIOnTimeSure")
    try:
        m1 = client.post(
            "/api/maintenances/manual", headers=auth(admin),
            json={"vehicle_id": v1["id"], "plan_id": plan1["id"], "target_date": (today + timedelta(days=5)).isoformat()},
        ).json()
        item1 = client.get(f"/api/maintenances/{m1['id']}", headers=auth(admin)).json()["items"][0]["children"][0]
        client.patch(f"/api/maintenances/{m1['id']}/assign", headers=auth(admin), json={"assigned_to_id": usta_id})
        r = client.post(f"/api/items/{item1['id']}/complete", headers=auth(usta_token))
        assert r.status_code == 200, r.text

        mid = client.get(
            "/api/kpi", headers=auth(admin), params={"date_from": wide_from, "date_to": wide_to},
        ).json()["completion"]
        assert mid["on_time_count"] == before["on_time_count"] + 1
        assert mid["on_time_evaluated"] == before["on_time_evaluated"] + 1

        # Geç: hedef tarih geçmişte kalmış (bugünden önce) — tamamlanma
        # bugün olduğu için completed_at.date() > target_date.
        v2, plan2 = _setup_time_plan("KPILateSure")
        try:
            m2 = client.post(
                "/api/maintenances/manual", headers=auth(admin),
                json={"vehicle_id": v2["id"], "plan_id": plan2["id"], "target_date": (today - timedelta(days=5)).isoformat()},
            ).json()
            item2 = client.get(f"/api/maintenances/{m2['id']}", headers=auth(admin)).json()["items"][0]["children"][0]
            client.patch(f"/api/maintenances/{m2['id']}/assign", headers=auth(admin), json={"assigned_to_id": usta_id})
            r = client.post(f"/api/items/{item2['id']}/complete", headers=auth(usta_token))
            assert r.status_code == 200, r.text

            after = client.get(
                "/api/kpi", headers=auth(admin), params={"date_from": wide_from, "date_to": wide_to},
            ).json()["completion"]
            assert after["on_time_count"] == mid["on_time_count"]  # artmadı
            assert after["on_time_evaluated"] == mid["on_time_evaluated"] + 1
        finally:
            _cleanup(client, admin, v2, plan2)
    finally:
        _cleanup(client, admin, v1, plan1)


def test_kpi_export_returns_valid_multisheet_xlsx(client, admin_token):
    admin = admin_token
    r = client.get("/api/kpi/export", headers=auth(admin))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    wb = openpyxl.load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Özet", "Araç Modeli", "Araçlar", "Personel", "Vardiya"]
