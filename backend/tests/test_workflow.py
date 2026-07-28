"""Uçtan uca iş akışı testi (eski backend/test_e2e.py'nin pytest'e taşınmış
hâli). Artık çalışan bir sunucuya elle bağlanmıyor — FastAPI'nin kendi
TestClient'ı ile uygulamanın içinde, izole bir geçici veritabanına karşı
çalışır. Çalıştırmak için: `pytest` (backend/ dizininden) veya CI'da
otomatik.

Bilerek TEK büyük, sıralı bir senaryo olarak yazıldı (orijinal script de
öyleydi) — birçok adım bir öncekinin bıraktığı durumu (oluşturulan araç,
tamamlanmış bakım, plan vb.) kullanıyor. Bunu bağımsız/izole testlere
bölmek daha "doğru" olurdu ama iş mantığının (otomatik bakım açma,
kademeli bakım birleştirme, kilit/kilit açma, plan/araç yumuşak-silme)
gerçek akıştaki gibi uçtan uca doğrulanması değerini kaybederdi."""

from .conftest import auth, leaves, make_xlsx


def test_full_backend_workflow(client, admin_token, usta_token):
    admin = admin_token
    usta = usta_token

    def start_all_pending():
        """Faz 26 — otomatik açılan iş emirleri admin/mühendis 'Başlat'
        demeden görünmez; testin geri kalanının çalışabilmesi için her
        araç oluşturma/km güncellemesinden sonra bekleyen tüm adaylar
        başlatılır."""
        r = client.get("/api/maintenances/pending-start", headers=auth(admin))
        for p in r.json():
            client.post(f"/api/maintenances/{p['id']}/start", headers=auth(admin))

    # ============================================================ Girişler
    r = client.get("/api/users", headers=auth(usta))
    assert r.status_code == 403, "Usta admin ekranına erişemez"

    # ============================================================ Kullanıcı kaydı
    r = client.post(
        "/api/auth/register",
        json={
            "username": "test.kayit", "full_name": "Test Kayıt",
            "password": "sifre123", "email": "test.kayit@example.com",
        },
    )
    assert r.status_code == 200, f"Kayıt başarısız: {r.text}"

    r = client.post("/api/auth/login", data={"username": "test.kayit", "password": "sifre123"})
    assert r.status_code == 403, "Onaysız kullanıcı giriş yapamamalı"

    users = client.get("/api/users", headers=auth(admin)).json()
    newu = next(u for u in users if u["username"] == "test.kayit")
    assert newu["is_active"] is False, "Yeni kullanıcı pasif (onay bekliyor) oluşmalı"
    r = client.patch(f"/api/users/{newu['id']}", headers=auth(admin), json={"is_active": True})
    assert r.status_code == 200 and r.json()["is_active"] is True

    r = client.post("/api/auth/login", data={"username": "test.kayit", "password": "sifre123"})
    assert r.status_code == 200 and "access_token" in r.json()

    # ============================================================ Otomatik bakım açma
    # 3801 -> 1.024.000 km: 1.025.000 bakımı aralığa girer (1.012.500 - 1.027.500)
    vehicles = client.get("/api/vehicles", headers=auth(admin)).json()
    v = next(x for x in vehicles if x["code"] == "3801")
    client.put(f"/api/vehicles/{v['id']}/km", headers=auth(admin), json={"current_km": 1_024_000})
    start_all_pending()
    ms = client.get("/api/maintenances", headers=auth(admin)).json()
    m = [x for x in ms if x["vehicle"]["id"] == v["id"] and x["target_km"] == 1_025_000]
    assert len(m) == 1 and m[0]["status"] == "pending", "1.025.000 km bakımı otomatik açılmalı"

    client.put(f"/api/vehicles/{v['id']}/km", headers=auth(admin), json={"current_km": 1_025_500})
    start_all_pending()
    ms = client.get("/api/maintenances", headers=auth(admin)).json()
    m2 = [x for x in ms if x["vehicle"]["id"] == v["id"] and x["target_km"] == 1_025_000]
    assert len(m2) == 1, "Aynı bakım ikinci kez açılmamalı"

    mid = m[0]["id"]
    detail = client.get(f"/api/maintenances/{mid}", headers=auth(usta)).json()
    lv = leaves(detail["items"])
    assert len(lv) > 20, f"Gerçek bakım ağacı snapshot kopyalanmalı ({len(lv)} madde)"

    parent = detail["items"][0]
    r = client.post(f"/api/items/{parent['id']}/complete", headers=auth(usta))
    assert r.status_code == 400, "Üst düğüm doğrudan işaretlenememeli"

    for leaf in lv:
        client.post(f"/api/items/{leaf['id']}/complete", headers=auth(usta))
    detail = client.get(f"/api/maintenances/{mid}", headers=auth(admin)).json()
    assert all(i["is_completed"] and i["is_locked"] for i in detail["items"]), \
        "Tüm üst düğümler otomatik tamamlanmalı"
    assert detail["status"] == "completed"
    lv2 = leaves(detail["items"])
    assert all(
        x["completed_by"] and x["completed_by"]["username"] == "usta" and x["completed_at"]
        for x in lv2
    ), "Kim yaptı kaydı tutulmalı"

    r = client.post(f"/api/items/{lv2[0]['id']}/complete", headers=auth(usta))
    assert r.status_code == 400, "Kilitli madde değiştirilememeli"
    r = client.post(f"/api/items/{lv2[0]['id']}/unlock", headers=auth(usta))
    assert r.status_code == 403, "Usta kilit açamamalı"

    r = client.post(f"/api/items/{lv2[0]['id']}/unlock", headers=auth(admin))
    detail = client.get(f"/api/maintenances/{mid}", headers=auth(admin)).json()
    lv3 = leaves(detail["items"])
    target = next(x for x in lv3 if x["id"] == lv2[0]["id"])
    assert r.status_code == 200 and not target["is_completed"], "Admin kilidi kaldırmalı, madde sıfırlanmalı"
    assert detail["status"] == "in_progress", "Bakım tekrar 'Devam Ediyor' olmalı"

    client.post(f"/api/items/{target['id']}/complete", headers=auth(usta))
    detail = client.get(f"/api/maintenances/{mid}", headers=auth(admin)).json()
    assert detail["status"] == "completed", "Yeniden tamamlanınca bakım tekrar bitmeli"

    # ============================================================ Plana özel bakım ağacı editörü
    plans = client.get("/api/plans", headers=auth(admin)).json()
    plan25 = next(p for p in plans if p["interval_km"] == 25000)
    plan50 = next(p for p in plans if p["interval_km"] == 50000)

    r = client.get(f"/api/tree?plan_id={plan25['id']}", headers=auth(admin))
    assert r.status_code == 200 and {n["name"] for n in r.json()} == {"ARAÇ ÜSTÜ", "ARAÇ İÇİ", "ARAÇ ALTI"}
    tree25 = r.json()

    # ============================================================ Kademeli (kümülatif) bakım
    # 150.000 km hem 25.000 hem 50.000'e tam bölünür -> tek iş emri.
    # Not: araç 0 km'de oluşturulup km PUT ile yükseltiliyor — create_vehicle
    # endpoint'i skip_passed_targets=True kullanır (aracın GİRİLDİĞİ km'nin
    # zaten geçmiş olduğu hedefler için iş emri açmaz, ör. sisteme sonradan
    # 250.000 km ile kaydedilen bir araç için 250.000 bakımı kendiliğinden
    # açılmamalı); burada test edilen asıl özellik o değil, kademeli
    # birleştirme, bu yüzden gerçekçi kullanım gibi km'yi PUT ile artırıyoruz.
    kum_v = client.post(
        "/api/vehicles", headers=auth(admin),
        json={"code": "KUM1", "name": "Kümülatif Test", "current_km": 0},
    ).json()
    client.put(f"/api/vehicles/{kum_v['id']}/km", headers=auth(admin), json={"current_km": 150000})
    start_all_pending()
    kum_ms = client.get("/api/maintenances", headers=auth(admin)).json()
    kum_ms = [x for x in kum_ms if x["vehicle"]["id"] == kum_v["id"]]
    kum_150 = next((x for x in kum_ms if x["target_km"] == 150000), None)
    assert kum_150 is not None and kum_150["plan"]["interval_km"] == 50000, \
        "150.000 km'de tek iş emri açılmalı (25k+50k birleşik)"

    kum_detail = client.get(f"/api/maintenances/{kum_150['id']}", headers=auth(admin)).json()
    # Birleşik ağaç, plan başına ayrı bir sarmalayıcı grup İLE DEĞİL, aynı
    # yoldaki (ör. ARAÇ ÜSTÜ > ...) düğümleri TEKİLLEŞTİREREK oluşturulur —
    # bkz. services._copy_tree_snapshot. Ortak düğümler her iki periyodu da
    # "tiers" alanında taşır (ör. "25000,50000").
    root_names = {n["name"] for n in kum_detail["items"]}
    assert root_names == {"ARAÇ ÜSTÜ", "ARAÇ İÇİ", "ARAÇ ALTI"}, \
        "Birleşik ağaçta da kök düğümler ARAÇ ÜSTÜ/İÇİ/ALTI olmalı (path bazlı tekilleştirme)"
    kum_leaves = leaves(kum_detail["items"])
    assert len(kum_leaves) > len(lv), "Birleşik iş emrinin madde sayısı tek plandan fazla olmalı"
    tiers_seen = {t for leaf in kum_leaves for t in leaf.get("tier_kms", [])}
    assert {25000, 50000} <= tiers_seen, "Maddeler hangi periyoda ait olduğunu 'tier_kms' alanında taşımalı"

    for leaf in kum_leaves:
        client.post(f"/api/items/{leaf['id']}/complete", headers=auth(usta))
    kum_detail2 = client.get(f"/api/maintenances/{kum_150['id']}", headers=auth(admin)).json()
    assert kum_detail2["status"] == "completed", "Tüm gruplar bitince birleşik iş emri de tamamlanmalı"

    # 75.000 km sadece 25.000'e bölünür -> tek plan, sarmalayıcı grup YOK.
    kum_v2 = client.post(
        "/api/vehicles", headers=auth(admin),
        json={"code": "KUM2", "name": "Kümülatif Test 2", "current_km": 0},
    ).json()
    client.put(f"/api/vehicles/{kum_v2['id']}/km", headers=auth(admin), json={"current_km": 75000})
    start_all_pending()
    kum_ms2 = client.get("/api/maintenances", headers=auth(admin)).json()
    kum_75 = next((x for x in kum_ms2 if x["vehicle"]["id"] == kum_v2["id"] and x["target_km"] == 75000), None)
    assert kum_75 is not None and kum_75["plan"]["interval_km"] == 25000
    kum_detail3 = client.get(f"/api/maintenances/{kum_75['id']}", headers=auth(admin)).json()
    root_names3 = {n["name"] for n in kum_detail3["items"]}
    assert root_names3 == {"ARAÇ ÜSTÜ", "ARAÇ İÇİ", "ARAÇ ALTI"}, \
        "Tek plan durumunda sarmalayıcı grup olmamalı"

    r2 = client.get(f"/api/tree?plan_id={plan50['id']}", headers=auth(admin))
    assert r2.status_code == 200 and r2.json() != tree25, "50.000 km ağacı 25.000'den bağımsız olmalı"

    # Araç İçi -> alt bölüm ekleme/çıkarma (sınırsız seviye)
    arac_ici = next(n for n in tree25 if n["name"] == "ARAÇ İÇİ")
    r = client.post(
        "/api/tree", headers=auth(admin),
        json={"name": "Yolcu Kabini", "parent_id": arac_ici["id"], "plan_id": plan25["id"]},
    )
    assert r.status_code == 200 and r.json()["name"] == "Yolcu Kabini"
    yeni_bolum = r.json()

    r = client.post(
        "/api/tree", headers=auth(admin),
        json={"name": "Koltuk Kontrolü", "parent_id": yeni_bolum["id"]},
    )
    assert r.status_code == 200
    alt_madde = r.json()

    assert client.delete(f"/api/tree/{alt_madde['id']}", headers=auth(admin)).status_code == 200
    assert client.delete(f"/api/tree/{yeni_bolum['id']}", headers=auth(admin)).status_code == 200

    n1 = client.post("/api/tree", headers=auth(admin), json={"name": "Test Bölümü", "plan_id": plan25["id"]}).json()
    n2 = client.post("/api/tree", headers=auth(admin), json={"name": "Alt Madde", "parent_id": n1["id"]}).json()
    r = client.patch(f"/api/tree/{n1['id']}", headers=auth(admin), json={"parent_id": n2["id"]})
    assert r.status_code == 400, "Döngü koruması çalışmalı"
    assert client.delete(f"/api/tree/{n1['id']}", headers=auth(admin)).status_code == 200

    # ============================================================ Dashboard
    r = client.get("/api/dashboard", headers=auth(admin))
    assert r.status_code == 200
    dash = r.json()
    assert dash["vehicle_count"] >= 2
    assert len(dash["upcoming"]) > 0
    assert any(x["id"] == mid for x in dash["recent_completed"])

    # ============================================================ Araç Excel içe/dışa aktarma
    xlsx_bytes = make_xlsx(
        [
            ["Araç Kodu", "Araç Adı", "KM", "Üretici", "Model", "Şasi No", "Not"],
            ["3801", "Tramvay 3801", 1_030_000, "Hyundai Rotem", "Sirio", "SIR-3801", "İçe aktarma testi"],
            ["3899", "Test Aracı", 5000, "Hyundai Rotem", "Sirio", "SIR-3899", ""],
        ]
    )
    r = client.post(
        "/api/vehicles/import", headers=auth(admin),
        files={"file": ("araclar.xlsx", xlsx_bytes,
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    start_all_pending()
    assert r.status_code == 200 and r.json()["created"] == 1 and r.json()["updated"] == 1

    vehicles = client.get("/api/vehicles", headers=auth(admin)).json()
    v01 = next(x for x in vehicles if x["code"] == "3801")
    v99 = next((x for x in vehicles if x["code"] == "3899"), None)
    assert v01["current_km"] == 1_030_000
    assert v01["chassis_no"] == "SIR-3801"
    assert v99 is not None and v99["current_km"] == 5000
    assert v01["last_km_update_at"] is not None

    r = client.get("/api/vehicles/export", headers=auth(admin))
    assert r.status_code == 200 and len(r.content) > 1000

    # ============================================================ 3D Model + Parça Eşleşmesi
    r = client.get(f"/api/tree/flat?plan_id={plan25['id']}", headers=auth(admin))
    assert r.status_code == 200 and len(r.json()) > 10
    flat_node_id = r.json()[0]["id"]

    r = client.get("/api/models3d", headers=auth(admin))
    assert r.status_code == 200, "3D model listesi çalışmalı"

    glb_bytes = b"glTF" + (b"\x00" * 16)  # gerçek olmayan minik ikili içerik
    r = client.post(
        "/api/models3d", headers=auth(admin),
        data={"label": "Test Modeli"},
        files={"file": ("test.glb", glb_bytes, "model/gltf-binary")},
    )
    assert r.status_code == 200 and r.json().get("filename")
    model3d = r.json()
    model3d_id = model3d["id"]

    r = client.get(f"/uploads/models/{model3d['filename']}")
    assert r.status_code == 200, "Yüklenen 3D model dosyası erişilebilir olmalı"

    r = client.post(
        f"/api/models3d/{model3d_id}/hotspots", headers=auth(admin),
        json={"plan_id": plan25["id"], "tree_node_id": flat_node_id,
              "mesh_name": "Coupler Scharfenberg", "label": "Kuplör"},
    )
    assert r.status_code == 200
    part_hotspot_id = r.json()["id"]

    r = client.patch(f"/api/model-hotspots/{part_hotspot_id}", headers=auth(admin), json={"label": "Kuplör (güncel)"})
    assert r.status_code == 200 and r.json()["label"] == "Kuplör (güncel)"

    r = client.post(
        f"/api/models3d/{model3d_id}/hotspots", headers=auth(usta),
        json={"plan_id": plan25["id"], "tree_node_id": flat_node_id, "mesh_name": "X"},
    )
    assert r.status_code == 403, "Usta parça eşleşmesi oluşturamamalı"

    r = client.get(f"/api/models3d/{model3d_id}/hotspots?plan_id={plan25['id']}", headers=auth(admin))
    assert r.status_code == 200 and any(h["id"] == part_hotspot_id for h in r.json())

    r = client.get(f"/api/maintenances/{mid}/model3d", headers=auth(admin))
    assert r.status_code == 200
    match = next((d for d in r.json() if d["id"] == model3d_id), None)
    assert match is not None, "3D model bakım görünümünde eşleşmeli"
    hs = next((x for x in match["hotspots"] if x["hotspot"]["id"] == part_hotspot_id), None)
    assert hs is not None and hs["is_completed"] is True, "Parça durumu (tamamlandı) doğru dönmeli"

    assert client.delete(f"/api/model-hotspots/{part_hotspot_id}", headers=auth(admin)).status_code == 200
    assert client.delete(f"/api/models3d/{model3d_id}", headers=auth(admin)).status_code == 200
    r = client.get(f"/uploads/models/{model3d['filename']}")
    assert r.status_code == 404, "3D model silinince dosya da kaldırılmalı"

    # ============================================================ 400.000 km planı hiç yok
    all_plans = client.get("/api/plans", headers=auth(admin)).json()
    assert not any(p["interval_km"] == 400000 for p in all_plans)

    # ============================================================ Plan silme
    r = client.post(
        "/api/plans", headers=auth(admin),
        json={"name": "Silme Testi", "interval_km": 660000, "lower_pct": 10, "upper_pct": 10},
    )
    assert r.status_code == 200
    del_test_plan = r.json()

    r = client.delete(f"/api/plans/{del_test_plan['id']}", headers=auth(usta))
    assert r.status_code == 403, "Usta plan silemez"

    r = client.delete(f"/api/plans/{del_test_plan['id']}", headers=auth(admin))
    assert r.status_code == 200 and r.json()["deactivated"] is False, \
        "Geçmişi olmayan plan tamamen silinmeli"
    plans_after_del = client.get("/api/plans", headers=auth(admin)).json()
    assert not any(p["id"] == del_test_plan["id"] for p in plans_after_del)

    assert client.delete("/api/plans/999999", headers=auth(admin)).status_code == 404

    # plan25'in zaten açılmış bakımları var -> silinmemeli, pasife alınmalı
    r = client.delete(f"/api/plans/{plan25['id']}", headers=auth(admin))
    assert r.status_code == 200 and r.json()["deactivated"] is True, \
        "Geçmişi olan plan silinmemeli, pasife alınmalı"
    plans_after_del2 = client.get("/api/plans", headers=auth(admin)).json()
    p25_after = next(p for p in plans_after_del2 if p["id"] == plan25["id"])
    assert p25_after["is_active"] is False
    client.patch(f"/api/plans/{plan25['id']}", headers=auth(admin), json={"is_active": True})

    # ============================================================ Bakım açma taban-birim düzeltmesi
    base_v = client.post(
        "/api/vehicles", headers=auth(admin),
        json={"code": "BASE500K", "name": "Taban Birim Testi", "current_km": 0},
    ).json()
    client.put(f"/api/vehicles/{base_v['id']}/km", headers=auth(admin), json={"current_km": 500_100})
    start_all_pending()
    base_ms = client.get("/api/maintenances", headers=auth(admin)).json()
    base_ms = [x for x in base_ms if x["vehicle"]["id"] == base_v["id"]]
    assert any(x["target_km"] == 500000 and x["plan"]["interval_km"] == 500000 for x in base_ms)
    assert not any(x["plan"]["interval_km"] == 300000 for x in base_ms)
    assert not any(x["target_km"] == 600000 for x in base_ms)

    # ============================================================ Excel ile self-servis yeni bakım tablosu
    tree_xlsx_bytes = make_xlsx(
        [
            [777000],
            ["TEST KATEGORİ"],
            ["TEST ALT KATEGORİ"],
            ["TEST BİLEŞEN"],
            ["Test kontrol maddesi 1"],
            ["Test kontrol maddesi 2"],
        ]
    )
    r = client.post(
        "/api/tree/import-excel", headers=auth(usta),
        files={"file": ("yeni_bakim.xlsx", tree_xlsx_bytes,
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 403, "Usta Excel ile yeni tablo oluşturamaz"

    r = client.post(
        "/api/tree/import-excel", headers=auth(admin),
        files={"file": ("yeni_bakim.xlsx", tree_xlsx_bytes,
                         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 200 and r.json()["nodes_created"] > 0
    new_plan_id = next((p["id"] for p in r.json()["plans"] if p["interval_km"] == 777000), None)
    assert new_plan_id is not None

    new_tree = client.get(f"/api/tree?plan_id={new_plan_id}", headers=auth(admin)).json()
    assert any(n["name"] == "TEST KATEGORİ" for n in new_tree)
    client.delete(f"/api/plans/{new_plan_id}", headers=auth(admin))

    # ============================================================ Dashboard yaklaşan bakımlar penceresi
    plans_now = client.get("/api/plans", headers=auth(admin)).json()
    plan500 = next(p for p in plans_now if p["interval_km"] == 500000)
    client.patch(f"/api/plans/{plan500['id']}", headers=auth(admin), json={"lower_pct": 0, "upper_pct": 0})

    dash_near_v = client.post(
        "/api/vehicles", headers=auth(admin),
        json={"code": "DASHNEAR", "name": "Dashboard Yakın", "current_km": 480_000},
    ).json()
    dash_far_v = client.post(
        "/api/vehicles", headers=auth(admin),
        json={"code": "DASHFAR", "name": "Dashboard Uzak", "current_km": 260_000},
    ).json()

    dash2 = client.get("/api/dashboard", headers=auth(admin)).json()
    near_shown = any(
        u["vehicle"]["id"] == dash_near_v["id"] and u["target_km"] == 500000 for u in dash2["upcoming"]
    )
    far_shown = any(u["vehicle"]["id"] == dash_far_v["id"] for u in dash2["upcoming"])
    assert near_shown, "Hedefe yakın araç yaklaşanlar listesinde olmalı"
    assert not far_shown, "Hedefe uzak araç yaklaşanlar listesinde OLMAMALI"

    alerts = client.get("/api/vehicles/alerts", headers=auth(admin))
    assert alerts.status_code == 200
    alert_near_shown = any(
        u["vehicle"]["id"] == dash_near_v["id"] and u["target_km"] == 500000 for u in alerts.json()
    )
    assert alert_near_shown, "Yakın araç /vehicles/alerts listesinde de olmalı"

    client.patch(f"/api/plans/{plan500['id']}", headers=auth(admin), json={"lower_pct": 50, "upper_pct": 10})

    # ============================================================ Araç silme
    r = client.post(
        "/api/vehicles", headers=auth(admin),
        json={"code": "DELTEST", "name": "Silme Testi Aracı", "current_km": 1000},
    )
    assert r.status_code == 200
    del_v = r.json()

    r = client.delete(f"/api/vehicles/{del_v['id']}", headers=auth(usta))
    assert r.status_code == 403, "Usta araç silemez"

    r = client.delete(f"/api/vehicles/{del_v['id']}", headers=auth(admin))
    assert r.status_code == 200, "Geçmişi olmayan araç tamamen silinmeli"
    vehicles_after_del = client.get("/api/vehicles", headers=auth(admin)).json()
    assert not any(x["id"] == del_v["id"] for x in vehicles_after_del)

    # kum_v'nin (yukarıda oluşturulan KUM1) zaten açılmış/tamamlanmış bir bakımı
    # var. Araç silme (plan/kullanıcı silmenin aksine) yumuşak/pasife-alma dalı
    # İÇERMİYOR — admin bir aracı sildiğinde geçmişiyle (iş emirleri + alt
    # maddeleri) birlikte KALICI olarak siliniyor (bkz. main.py delete_vehicle).
    r = client.delete(f"/api/vehicles/{kum_v['id']}", headers=auth(admin))
    assert r.status_code == 200 and r.json()["deleted_maintenances"] >= 1
    vehicles_after_del2 = client.get("/api/vehicles", headers=auth(admin)).json()
    assert not any(x["id"] == kum_v["id"] for x in vehicles_after_del2), \
        "Geçmişi olan araç da dahil, silme her zaman kalıcıdır"

    # ============================================================ Araç geçmişi görüntüleme
    v_history = client.get(f"/api/maintenances?vehicle_id={v['id']}", headers=auth(admin)).json()
    assert len(v_history) > 0
    assert all(x["vehicle"]["id"] == v["id"] for x in v_history)
