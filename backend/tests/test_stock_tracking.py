"""Parça stok takibi (Faz 27): madde tamamlanınca eşleştirilmiş parçaların
otomatik düşmesi/geri yüklenmesi, manuel stok hareketi ve düşük stok
bildirimi — bkz. services._adjust_stock_for_node, main.py
add_node_part_usage / adjust_part_stock / notifications()."""

from .conftest import auth, leaves


def test_stock_auto_deduct_and_restore_on_complete_unlock(client, admin_token):
    admin = admin_token

    # ---- Kurulum: yeni araç + plan + tek yapraklı ağaç + parça ----
    # DİKKAT: vehicle_model=="" olan bir plan TÜM araçlara (diğer testlerin
    # kullandığı 3801/3802 dahil) uygulanır ve paylaşılan test veritabanında
    # otomatik bakım açma mantığını etkileyerek diğer testleri bozar — bu
    # yüzden burada özel, benzersiz bir model kullanılıyor (yalnızca bu
    # testteki araca uygulanır).
    v = client.post(
        "/api/vehicles", headers=auth(admin),
        json={"code": "STOK-TEST-1", "name": "Stok Test Aracı", "current_km": 0, "model": "StokTestModel"},
    ).json()
    plan = client.post(
        "/api/plans", headers=auth(admin),
        json={"name": "Stok Test Planı", "interval_km": 10000, "vehicle_model": "StokTestModel"},
    ).json()
    root = client.post(
        "/api/tree", headers=auth(admin),
        json={"name": "Stok Test Bölümü", "plan_id": plan["id"]},
    ).json()
    leaf = client.post(
        "/api/tree", headers=auth(admin),
        json={"name": "Balata Değişimi", "parent_id": root["id"]},
    ).json()
    part = client.post(
        "/api/parts", headers=auth(admin),
        json={"name": "Fren Balatası (Test)", "stock_qty": 20, "min_stock": 5, "unit": "adet"},
    ).json()
    assert part["stock_qty"] == 20 and part["min_stock"] == 5

    # Maddeye "tamamlanınca 3 adet balata düşer" eşleşmesi ekle.
    r = client.post(
        f"/api/tree/{leaf['id']}/parts", headers=auth(admin),
        json={"part_id": part["id"], "qty": 3},
    )
    assert r.status_code == 200, r.text
    usage = r.json()
    assert usage["qty"] == 3 and usage["part_name"] == "Fren Balatası (Test)"

    usages = client.get(f"/api/tree/{leaf['id']}/parts", headers=auth(admin)).json()
    assert len(usages) == 1 and usages[0]["part_id"] == part["id"]

    # ---- Manuel bakım aç, maddeyi tamamla ----
    m = client.post(
        "/api/maintenances/manual", headers=auth(admin),
        json={"vehicle_id": v["id"], "plan_id": plan["id"], "target_km": 10000},
    ).json()
    detail = client.get(f"/api/maintenances/{m['id']}", headers=auth(admin)).json()
    item = next(i for i in leaves(detail["items"]) if i["name"] == "Balata Değişimi")
    assert not item["children"], "Yaprak madde olmalı"

    r = client.post(f"/api/items/{item['id']}/complete", headers=auth(admin))
    assert r.status_code == 200, r.text

    part_after = client.get(f"/api/parts/{part['id']}", headers=auth(admin)).json()
    assert part_after["stock_qty"] == 17, "3 adet otomatik düşmeli (20 -> 17)"

    # Düşük stok eşiği (5) henüz geçilmedi — bildirimde görünmemeli.
    notifs = client.get("/api/notifications", headers=auth(admin)).json()
    assert not any(n["category"] == "low_stock" and "Fren Balatası (Test)" in n["title"] for n in notifs)

    # ---- Kilidi aç: stok geri yüklenmeli ----
    r = client.post(f"/api/items/{item['id']}/unlock", headers=auth(admin))
    assert r.status_code == 200, r.text
    part_restored = client.get(f"/api/parts/{part['id']}", headers=auth(admin)).json()
    assert part_restored["stock_qty"] == 20, "Kilit açılınca stok geri eklenmeli (17 -> 20)"

    # ---- Tekrar tamamla, birkaç kez daha eşiğin altına düşür ----
    client.post(f"/api/items/{item['id']}/complete", headers=auth(admin))  # 20 -> 17
    client.post(f"/api/items/{item['id']}/unlock", headers=auth(admin))
    client.post(f"/api/items/{item['id']}/complete", headers=auth(admin))  # 20 -> 17 (tekrar)

    # Manuel stok düşümüyle eşiğin (5) altına indir.
    r = client.post(
        f"/api/parts/{part['id']}/stock-adjust", headers=auth(admin),
        json={"delta": -13, "note": "Sayım düzeltmesi"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["stock_qty"] == 4

    notifs2 = client.get("/api/notifications", headers=auth(admin)).json()
    low = [n for n in notifs2 if n["category"] == "low_stock" and "Fren Balatası (Test)" in n["title"]]
    assert len(low) == 1, "Stok eşiğin altına inince düşük stok bildirimi çıkmalı"
    assert "4" in low[0]["detail"] and "5" in low[0]["detail"]

    # ---- Eşleşmeyi kaldır ----
    r = client.delete(f"/api/tree/part-usages/{usage['id']}", headers=auth(admin))
    assert r.status_code == 200
    assert client.get(f"/api/tree/{leaf['id']}/parts", headers=auth(admin)).json() == []


def test_repair_materials_deduct_catalog_but_not_free_text(client, admin_token):
    admin = admin_token

    korfuk = client.post(
        "/api/parts", headers=auth(admin), json={"name": "Körük (Test)"},
    ).json()
    vida = client.post(
        "/api/parts", headers=auth(admin),
        json={"name": "Vida M6 (Test)", "stock_qty": 50, "min_stock": 10, "unit": "adet"},
    ).json()

    # Katalogdan malzeme + serbest metin malzeme ekle.
    r = client.post(
        f"/api/parts/{korfuk['id']}/repair-materials", headers=auth(admin),
        json={"material_part_id": vida["id"], "qty": 6},
    )
    assert r.status_code == 200, r.text
    row1 = r.json()
    assert row1["material_name"] == "Vida M6 (Test)" and row1["material_stock_qty"] == 50

    r2 = client.post(
        f"/api/parts/{korfuk['id']}/repair-materials", headers=auth(admin),
        json={"free_text": "Silikon conta macunu", "qty": 1},
    )
    assert r2.status_code == 200, r2.text
    row2 = r2.json()
    assert row2["material_part_id"] is None and row2["free_text"] == "Silikon conta macunu"

    # Kendi kendine malzeme olamaz.
    r3 = client.post(
        f"/api/parts/{korfuk['id']}/repair-materials", headers=auth(admin),
        json={"material_part_id": korfuk["id"], "qty": 1},
    )
    assert r3.status_code == 400

    # Ne malzeme ne metin -> 400.
    r4 = client.post(
        f"/api/parts/{korfuk['id']}/repair-materials", headers=auth(admin), json={"qty": 1},
    )
    assert r4.status_code == 400

    materials = client.get(f"/api/parts/{korfuk['id']}/repair-materials", headers=auth(admin)).json()
    assert len(materials) == 2

    # ---- Tamir Edildi ----
    r = client.post(
        f"/api/parts/{korfuk['id']}/repair", headers=auth(admin), json={"note": "Periyodik tamir"},
    )
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["materials_deducted"] == ["Vida M6 (Test) x6"]
    assert result["materials_skipped"] == ["Silikon conta macunu x1"]

    vida_after = client.get(f"/api/parts/{vida['id']}", headers=auth(admin)).json()
    assert vida_after["stock_qty"] == 44, "Katalogdan seçilen malzemenin stoğu düşmeli (50 -> 44)"

    # ---- Satırı sil ----
    r = client.delete(f"/api/parts/repair-materials/{row2['id']}", headers=auth(admin))
    assert r.status_code == 200
    materials2 = client.get(f"/api/parts/{korfuk['id']}/repair-materials", headers=auth(admin)).json()
    assert len(materials2) == 1 and materials2[0]["id"] == row1["id"]


def test_fractional_quantities_supported(client, admin_token):
    """'1 kg'lık üründen bir bakımda 0.2 kg kullanılıyor' senaryosu — stok,
    madde-parça eşleşmesi ve tamir malzemesi miktarları kesirli olabilmeli."""
    admin = admin_token

    yapistirici = client.post(
        "/api/parts", headers=auth(admin),
        json={"name": "Yapıştırıcı (Test)", "stock_qty": 1, "min_stock": 0.9, "unit": "kg"},
    ).json()
    assert yapistirici["stock_qty"] == 1 and yapistirici["min_stock"] == 0.9

    # ---- Bakım ağacı maddesinde kesirli kullanım ----
    v = client.post(
        "/api/vehicles", headers=auth(admin),
        json={"code": "STOK-TEST-2", "name": "Kesirli Test Aracı", "current_km": 0, "model": "KesirliTestModel"},
    ).json()
    plan = client.post(
        "/api/plans", headers=auth(admin),
        json={"name": "Kesirli Test Planı", "interval_km": 5000, "vehicle_model": "KesirliTestModel"},
    ).json()
    root = client.post(
        "/api/tree", headers=auth(admin), json={"name": "Kesirli Test Bölümü", "plan_id": plan["id"]},
    ).json()
    leaf = client.post(
        "/api/tree", headers=auth(admin), json={"name": "Körük Yapıştırma", "parent_id": root["id"]},
    ).json()
    r = client.post(
        f"/api/tree/{leaf['id']}/parts", headers=auth(admin),
        json={"part_id": yapistirici["id"], "qty": 0.2},
    )
    assert r.status_code == 200, r.text
    assert r.json()["qty"] == 0.2

    m = client.post(
        "/api/maintenances/manual", headers=auth(admin),
        json={"vehicle_id": v["id"], "plan_id": plan["id"], "target_km": 5000},
    ).json()
    detail = client.get(f"/api/maintenances/{m['id']}", headers=auth(admin)).json()
    item = next(i for i in leaves(detail["items"]) if i["name"] == "Körük Yapıştırma")

    r = client.post(f"/api/items/{item['id']}/complete", headers=auth(admin))
    assert r.status_code == 200, r.text
    after = client.get(f"/api/parts/{yapistirici['id']}", headers=auth(admin)).json()
    assert abs(after["stock_qty"] - 0.8) < 1e-9, f"1 kg - 0.2 kg = 0.8 kg olmalı, geldi: {after['stock_qty']}"

    # Eşiğin (0.9) altına indi -> düşük stok bildirimi çıkmalı.
    notifs = client.get("/api/notifications", headers=auth(admin)).json()
    low = [n for n in notifs if n["category"] == "low_stock" and "Yapıştırıcı (Test)" in n["title"]]
    assert len(low) == 1
    assert "0.8" in low[0]["detail"] and "kg" in low[0]["detail"]

    # Kilidi aç -> 0.2 kg geri eklenmeli (0.8 -> 1.0).
    r = client.post(f"/api/items/{item['id']}/unlock", headers=auth(admin))
    assert r.status_code == 200, r.text
    restored = client.get(f"/api/parts/{yapistirici['id']}", headers=auth(admin)).json()
    assert abs(restored["stock_qty"] - 1.0) < 1e-9

    # ---- Manuel stok hareketinde kesirli delta ----
    r = client.post(
        f"/api/parts/{yapistirici['id']}/stock-adjust", headers=auth(admin),
        json={"delta": -0.25, "note": "Sayım"},
    )
    assert r.status_code == 200, r.text
    assert abs(r.json()["stock_qty"] - 0.75) < 1e-9

    # ---- Tamir malzemesinde kesirli miktar ----
    korfuk2 = client.post("/api/parts", headers=auth(admin), json={"name": "Körük 2 (Test)"}).json()
    r = client.post(
        f"/api/parts/{korfuk2['id']}/repair-materials", headers=auth(admin),
        json={"material_part_id": yapistirici["id"], "qty": 0.1},
    )
    assert r.status_code == 200 and r.json()["qty"] == 0.1
    r = client.post(f"/api/parts/{korfuk2['id']}/repair", headers=auth(admin), json={})
    assert r.status_code == 200, r.text
    assert "Yapıştırıcı (Test) x0.1" in r.json()["materials_deducted"]
    final = client.get(f"/api/parts/{yapistirici['id']}", headers=auth(admin)).json()
    assert abs(final["stock_qty"] - 0.65) < 1e-9


def test_stock_adjust_requires_nonzero_delta(client, admin_token):
    admin = admin_token
    part = client.post(
        "/api/parts", headers=auth(admin), json={"name": "Sıfır Delta Test Parçası"},
    ).json()
    assert part["stock_qty"] == 0 and part["min_stock"] == 0 and part["unit"] == "adet"
    r = client.post(
        f"/api/parts/{part['id']}/stock-adjust", headers=auth(admin), json={"delta": 0},
    )
    assert r.status_code == 400
