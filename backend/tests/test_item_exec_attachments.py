"""Faz 21 — Bakım sırasında (checklist maddesini tikleyip incelerken)
usta/mühendis/amirin tablet kamerasıyla veya galeriden fotoğraf eklemesi
için testler: POST /api/items/{id}/exec-attachments ve
DELETE /api/item-exec-attachments/{id}.

Bu, admin'in bakım ağacı ŞABLONUNA eklediği referans görselinden
(NodeAttachment, bkz. test_tree_attachments benzeri testler) tamamen
ayrıdır — burada test edilen, doğrudan bir iş emrinin (Maintenance) somut
bir checklist maddesine (MaintenanceItem) bağlı, sahada o an çekilen
kanıt/durum fotoğrafıdır."""

import io

from PIL import Image

from .conftest import auth


def _fake_image_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (12, 12), color="red").save(buf, format="PNG")
    return buf.getvalue()


def _make_maintenance_with_item(client, admin, model):
    """Manuel bakım oluşturma akışı üzerinden gerçek bir MaintenanceItem
    (checklist maddesi) id'si elde etmek için: araç + plan + tek yapraklı
    bir ağaç + o plan/hedefe göre manuel bakım açılışı."""
    v = client.post(
        "/api/vehicles", headers=auth(admin),
        json={"code": f"EXA-{model}", "name": "Görsel Ek Test Aracı", "current_km": 10_000, "model": model},
    ).json()
    plan = client.post(
        "/api/plans", headers=auth(admin),
        json={"plan_type": "km", "interval_km": 10_000, "vehicle_model": model},
    ).json()
    root = client.post(
        "/api/tree", headers=auth(admin), json={"name": "ARAÇ İÇİ", "plan_id": plan["id"]},
    ).json()
    client.post(
        "/api/tree", headers=auth(admin),
        json={"name": "Test Maddesi", "parent_id": root["id"], "card_code": "EXA-01"},
    )
    m = client.post(
        "/api/maintenances/manual", headers=auth(admin),
        json={"vehicle_id": v["id"], "plan_id": plan["id"], "target_km": 10_000},
    ).json()
    detail = client.get(f"/api/maintenances/{m['id']}", headers=auth(admin)).json()
    item = detail["items"][0]["children"][0]
    return v, plan, m, item


def _cleanup(client, admin, v, plan):
    client.delete(f"/api/vehicles/{v['id']}", headers=auth(admin))
    client.delete(f"/api/plans/{plan['id']}", headers=auth(admin))


def test_upload_exec_attachment_shows_in_maintenance_detail(client, admin_token):
    admin = admin_token
    v, plan, m, item = _make_maintenance_with_item(client, admin, "ExaModelA")
    try:
        r = client.post(
            f"/api/items/{item['id']}/exec-attachments", headers=auth(admin),
            files={"file": ("foto.png", _fake_image_bytes(), "image/png")},
        )
        assert r.status_code == 200, r.text
        att = r.json()
        assert att["original_name"] == "foto.png"
        assert att["filename"]
        assert att["uploaded_by"]["username"] == "admin"

        detail = client.get(f"/api/maintenances/{m['id']}", headers=auth(admin)).json()
        leaf = detail["items"][0]["children"][0]
        assert len(leaf["exec_attachments"]) == 1
        assert leaf["exec_attachments"][0]["id"] == att["id"]

        # Yüklenen dosya statik olarak erişilebilir olmalı.
        r2 = client.get(f"/uploads/attachments/{att['filename']}")
        assert r2.status_code == 200
    finally:
        _cleanup(client, admin, v, plan)


def test_upload_usta_can_add_photo_when_unassigned(client, admin_token, usta_token):
    """Bakım kimseye atanmamışsa (assigned_to_id=None) herhangi bir usta
    fotoğraf ekleyebilir — complete_item/set_item_note ile aynı kural."""
    admin = admin_token
    v, plan, m, item = _make_maintenance_with_item(client, admin, "ExaModelB")
    try:
        r = client.post(
            f"/api/items/{item['id']}/exec-attachments", headers=auth(usta_token),
            files={"file": ("foto.png", _fake_image_bytes(), "image/png")},
        )
        assert r.status_code == 200, r.text
    finally:
        _cleanup(client, admin, v, plan)


def test_upload_rejects_when_assigned_to_someone_else(client, admin_token, usta_token):
    admin = admin_token
    v, plan, m, item = _make_maintenance_with_item(client, admin, "ExaModelC")
    try:
        other = client.post(
            "/api/users", headers=auth(admin),
            json={"username": "usta2exa", "full_name": "Diğer Usta", "role": "technician", "password": "usta2pass123"},
        ).json()
        client.patch(
            f"/api/maintenances/{m['id']}/assign", headers=auth(admin),
            json={"assigned_to_id": other["id"]},
        )
        r = client.post(
            f"/api/items/{item['id']}/exec-attachments", headers=auth(usta_token),
            files={"file": ("foto.png", _fake_image_bytes(), "image/png")},
        )
        assert r.status_code == 403
    finally:
        _cleanup(client, admin, v, plan)


def test_upload_rejects_non_image(client, admin_token):
    admin = admin_token
    v, plan, m, item = _make_maintenance_with_item(client, admin, "ExaModelD")
    try:
        r = client.post(
            f"/api/items/{item['id']}/exec-attachments", headers=auth(admin),
            files={"file": ("belge.txt", b"merhaba", "text/plain")},
        )
        assert r.status_code == 400
    finally:
        _cleanup(client, admin, v, plan)


def test_upload_requires_item_to_exist(client, admin_token):
    r = client.post(
        "/api/items/999999999/exec-attachments", headers=auth(admin_token),
        files={"file": ("foto.png", _fake_image_bytes(), "image/png")},
    )
    assert r.status_code == 404


def test_delete_exec_attachment_removes_it(client, admin_token):
    admin = admin_token
    v, plan, m, item = _make_maintenance_with_item(client, admin, "ExaModelE")
    try:
        r = client.post(
            f"/api/items/{item['id']}/exec-attachments", headers=auth(admin),
            files={"file": ("foto.png", _fake_image_bytes(), "image/png")},
        )
        att = r.json()

        r2 = client.delete(f"/api/item-exec-attachments/{att['id']}", headers=auth(admin))
        assert r2.status_code == 200, r2.text

        detail = client.get(f"/api/maintenances/{m['id']}", headers=auth(admin)).json()
        leaf = detail["items"][0]["children"][0]
        assert leaf["exec_attachments"] == []

        # Dosya da diskten silinmiş olmalı (statik sunumdan artık 404 döner).
        r3 = client.get(f"/uploads/attachments/{att['filename']}")
        assert r3.status_code == 404
    finally:
        _cleanup(client, admin, v, plan)


def test_delete_requires_permission(client, admin_token, usta_token):
    admin = admin_token
    v, plan, m, item = _make_maintenance_with_item(client, admin, "ExaModelF")
    try:
        r = client.post(
            f"/api/items/{item['id']}/exec-attachments", headers=auth(admin),
            files={"file": ("foto.png", _fake_image_bytes(), "image/png")},
        )
        att = r.json()
        other = client.post(
            "/api/users", headers=auth(admin),
            json={"username": "usta3exa", "full_name": "Üçüncü Usta", "role": "technician", "password": "usta3pass123"},
        ).json()
        client.patch(
            f"/api/maintenances/{m['id']}/assign", headers=auth(admin),
            json={"assigned_to_id": other["id"]},
        )
        r2 = client.delete(f"/api/item-exec-attachments/{att['id']}", headers=auth(usta_token))
        assert r2.status_code == 403
    finally:
        _cleanup(client, admin, v, plan)
