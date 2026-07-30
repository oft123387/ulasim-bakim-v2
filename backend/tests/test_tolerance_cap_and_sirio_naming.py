"""İki ilişkili düzeltme için testler:

1. Yüzdelik tolerans penceresi (plan_window / PREVIEW_LOWER_PCT-UPPER_PCT)
   artık büyük periyotlarda sınırsız büyümüyor — kullanıcının onayladığı
   km-tolerans tablosuna göre her iki yönde de en fazla
   services.MAX_TOLERANCE_KM (3.000 km) ile tavanlanıyor.
2. Sirio araç modelindeki km planlarının otomatik adı artık "X km Bakımı"
   yerine kısa periyot koduna (ör. "12Y", "3M") çevriliyor — yalnızca Sirio
   için, ve yalnızca periyot M(10.000)/Y(120.000) birimine tam bölünüyorsa."""

from app import services

from .conftest import auth


def test_plan_window_caps_percentage_tolerance_at_3000km():
    class FakePlan:
        threshold_type = "pct"
        lower_pct = 5.0
        upper_pct = 10.0

    # 1M (10.000 km): %5=500, %10=1000 — tavana değmiyor (kullanıcının
    # onayladığı tablo örneğiyle birebir aynı: alt=-500, üst=1000).
    p1 = FakePlan()
    p1.interval_km = 10_000
    lower, upper = services.plan_window(p1, 100_000)
    assert lower == 100_000 - 500
    assert upper == 100_000 + 1000

    # 6M (60.000 km): %5=3000 (tam tavanda), %10=6000 (tavanlanır -> 3000).
    p2 = FakePlan()
    p2.interval_km = 60_000
    lower2, upper2 = services.plan_window(p2, 600_000)
    assert lower2 == 600_000 - 3000
    assert upper2 == 600_000 + 3000

    # 12Y (1.440.000 km): %5 ve %10 ikisi de çok büyük -> ikisi de 3000'e taban.
    p3 = FakePlan()
    p3.interval_km = 1_440_000
    lower3, upper3 = services.plan_window(p3, 1_440_000)
    assert lower3 == 1_440_000 - 3000
    assert upper3 == 1_440_000 + 3000


def test_plan_window_km_threshold_type_unaffected_by_cap():
    """threshold_type='km' (sabit km toleransı) MAX_TOLERANCE_KM'den
    etkilenmemeli — admin bilinçli olarak 3000'den büyük bir sabit tolerans
    girdiyse bu olduğu gibi uygulanır (yalnızca yüzdelik tip tavanlanır)."""
    class FakePlan:
        threshold_type = "km"
        lower_km = 5000
        upper_km = 8000

    p = FakePlan()
    lower, upper = services.plan_window(p, 100_000)
    assert lower == 95_000
    assert upper == 108_000


def test_sirio_period_label_matches_table():
    assert services.sirio_period_label(10_000) == "1M"
    assert services.sirio_period_label(30_000) == "3M"
    assert services.sirio_period_label(60_000) == "6M"
    assert services.sirio_period_label(120_000) == "1Y"
    assert services.sirio_period_label(720_000) == "6Y"
    assert services.sirio_period_label(1_440_000) == "12Y"
    assert services.sirio_period_label(1_800_000) == "15Y"
    # Sirio tablosunda olmayan (M/Y birimine tam bölünmeyen) bir değer -> None.
    assert services.sirio_period_label(25_000) is None
    assert services.sirio_period_label(0) is None


def test_default_plan_name_uses_sirio_label_only_for_sirio_model():
    assert services.default_plan_name("km", 1_440_000, None, "Sirio") == "12Y"
    assert services.default_plan_name("km", 1_440_000, None, "SİRİO") == "12Y"
    assert services.default_plan_name("km", 1_440_000, None, "Sirio Tip-2") == "12Y"
    # Bozankaya (veya model belirtilmemiş) etkilenmez -> normal km biçimi.
    assert services.default_plan_name("km", 1_440_000, None, "Bozankaya Tip-1") == "1.440.000 km Bakımı"
    assert services.default_plan_name("km", 1_440_000, None, "") == "1.440.000 km Bakımı"
    # Sirio ama tabloya uymayan bir periyot (25.000) -> normal km biçimine düşer.
    assert services.default_plan_name("km", 25_000, None, "Sirio") == "25.000 km Bakımı"


def test_create_plan_endpoint_applies_sirio_naming(client, admin_token):
    admin = admin_token
    r = client.post(
        "/api/plans", headers=auth(admin),
        json={"interval_km": 1_440_000, "vehicle_model": "Sirio Naming Test"},
    )
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["name"] == "12Y"
    client.delete(f"/api/plans/{plan['id']}", headers=auth(admin))


def test_normalize_sirio_plan_names_endpoint_fixes_existing_plans(client, admin_token):
    admin = admin_token
    model = "Sirio Normalize Test"
    # Adı elle "eski" (km) biçiminde oluştur (default_plan_name devreye
    # girmesin diye açıkça bir ad veriyoruz) — gerçek hayatta bu, özellik
    # eklenmeden ÖNCE oluşturulmuş bir plana karşılık gelir.
    r = client.post(
        "/api/plans", headers=auth(admin),
        json={"name": "1.440.000 km Bakımı", "interval_km": 1_440_000, "vehicle_model": model},
    )
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["name"] == "1.440.000 km Bakımı"

    r = client.post("/api/plans/normalize-sirio-names", headers=auth(admin))
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["count"] >= 1
    assert any(x["plan_id"] == plan["id"] and x["new_name"] == "12Y" for x in result["renamed"])

    updated = client.get("/api/plans", headers=auth(admin)).json()
    mine = next(p for p in updated if p["id"] == plan["id"])
    assert mine["name"] == "12Y"

    # İkinci çağrı idempotent olmalı — artık bu plan için değişecek bir şey yok.
    r2 = client.post("/api/plans/normalize-sirio-names", headers=auth(admin))
    assert r2.status_code == 200, r2.text
    assert not any(x["plan_id"] == plan["id"] for x in r2.json()["renamed"])

    client.delete(f"/api/plans/{plan['id']}", headers=auth(admin))


def test_normalize_sirio_plan_names_requires_admin(client, usta_token):
    r = client.post("/api/plans/normalize-sirio-names", headers=auth(usta_token))
    assert r.status_code == 403
