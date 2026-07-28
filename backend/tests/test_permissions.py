"""Yetki Yönetimi: izin metadatası (kategori/açıklama), rol sıfırlama ve
yeni 'view_dashboard_analytics' görünürlük izni — bkz. services.py
PERMISSION_CATEGORIES/PERMISSION_DESCRIPTIONS, main.py list_permission_defs
/ reset_role_permissions."""

from .conftest import auth


def test_permission_defs_cover_all_keys_with_category(client, admin_token):
    admin = admin_token
    r = client.get("/api/permissions/defs", headers=auth(admin))
    assert r.status_code == 200, r.text
    defs = r.json()
    keys = {d["key"] for d in defs}
    # Bilinen tüm izin anahtarları (bkz. services.PERMISSION_KEYS) burada
    # olmalı, hiçbiri kategorisiz kalmamalı.
    expected = {
        "view_maintenance", "edit_maintenance", "delete_maintenance",
        "approve_maintenance", "start_maintenance", "create_maintenance",
        "delete_all_maintenance", "export_excel", "manage_users",
        "view_dashboard_analytics",
    }
    assert expected.issubset(keys)
    for d in defs:
        assert d["category"], f"{d['key']} kategorisiz kalmış"


def test_view_dashboard_analytics_default_true_for_all_roles(client, admin_token):
    admin = admin_token
    r = client.get("/api/permissions", headers=auth(admin))
    assert r.status_code == 200, r.text
    matrix = r.json()
    for role in ("engineer", "technician", "amir"):
        assert matrix[role]["view_dashboard_analytics"] is True, (
            f"{role} için varsayılan olarak açık olmalı (mevcut dashboard davranışını bozmamak için)"
        )


def test_permissions_reset_restores_defaults(client, admin_token):
    admin = admin_token

    # Usta'nın export_excel iznini kapat.
    r = client.patch(
        "/api/permissions", headers=auth(admin),
        json={"role": "technician", "permission": "export_excel", "allowed": False},
    )
    assert r.status_code == 200
    assert r.json()["technician"]["export_excel"] is False

    # Sıfırla -> fabrika varsayılanına (export_excel=True) dönmeli.
    r = client.post("/api/permissions/reset", headers=auth(admin), json={"role": "technician"})
    assert r.status_code == 200, r.text
    matrix = r.json()
    assert matrix["technician"]["export_excel"] is True
    assert matrix["technician"]["manage_users"] is False  # diğer varsayılanlar da doğru olmalı

    # Diğer rollere dokunulmamalı.
    r2 = client.get("/api/permissions", headers=auth(admin))
    assert r2.json()["engineer"]["export_excel"] is True


def test_permissions_reset_rejects_invalid_role(client, admin_token):
    r = client.post("/api/permissions/reset", headers=auth(admin_token), json={"role": "admin"})
    assert r.status_code == 422  # pydantic pattern reddeder


def test_permission_defs_and_reset_require_admin(client, usta_token):
    r = client.get("/api/permissions/defs", headers=auth(usta_token))
    assert r.status_code == 403
    r2 = client.post("/api/permissions/reset", headers=auth(usta_token), json={"role": "technician"})
    assert r2.status_code == 403
