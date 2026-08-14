"""KB TOPLU LİSTE formatındaki toplu bakım kataloğu içe aktarma için uçtan
uca test.

Gerçek yüklenen "KB TOPLU LİSTE.xlsx" dosyasıyla manuel olarak doğrulandı.
Bu test o akışın küçük, kontrollü bir örneğini otomatik test paketine ekler:
  - Düz sayı VE kısaltmalı km kodları ("1M"/"3M"/"6Y" — SÜRE değil, km
    katıdır: M=10.000 km, Y=120.000 km birimi — kullanıcı beyanı: 1Y=120.000,
    2Y=240.000 vb., çünkü 1 yıl=12 ay ve 1 ay=10.000 km) doğru km değerine
    çevrilir.
  - Kaynak dosyada görünen HER periyot (ör. 720.000 km gibi başka bir
    periyodun tam katı olsa bile) kendi başına GERÇEK, Bakım Planları
    sayfasında da (varsayılan GET /api/plans) görünen bir plan olarak açılır
    — hiçbiri "is_catalog_only" olarak gizlenmez.
  - BAKIM hücresi yorumlanamayan ("?") bir satır ATLANIR — hiçbir zaman
    "?" periyotlu bir plan/madde ağaca eklenmez; yalnızca önizleme/rapor
    (`unresolved_period`) listesinde görünür.
  - Ağaç içe aktarıldığı anda, o modeldeki araçlar için otomatik bakım
    adayları HEMEN yeniden değerlendirilir.
  - Excel'deki ARAÇ değeri (ör. "SİRİO") filodaki gerçek Vehicle.model
    değeriyle (ör. "Sirio") birebir aynı yazılmasa da (büyük/küçük harf,
    Türkçe karakter) doğru araca eşleşir.
  - "Tümü" birleşik görünüm (GET /api/tree/merged) tüm periyotların
    maddelerini, her birinin yanında periyot etiketiyle birlikte döner.
"""

from .conftest import auth, make_xlsx

_BULK_HEADERS = ["ARAÇ", "BÖLGE", "SİSTEM", "EKİPMAN", "BAKIM", "BAKIM KALEMİ", "KART KODU"]


def test_bulk_catalog_import_km_codes_and_model_matching(client, admin_token):
    admin = admin_token

    # Excel'deki ARAÇ değeri filodaki gerçek modelle (Türkçe karakter/büyük-
    # küçük harf farkıyla) BİREBİR aynı değil — "SİRİO" (Excel) vs "Sirio"
    # (gerçek araç modeli). Eşleştirmenin normalize edilerek (case/Türkçe
    # karakter duyarsız, alt dize) çalıştığı doğrulanır.
    # Not: km 200.000'e ÇOK YAKIN (199.500) ama BİREBİR AYNI değil seçildi.
    # Gerçek/seed veritabanındaki "Genel" (vehicle_model="") km planları da
    # bu araca uygulandığından (25.000/50.000/... gibi daha ince taneli
    # periyotlar), aday hedef hesaplaması TÜM uygulanabilir planların ortak
    # bölenini kullanır; 200.000'e bu kadar yakın olması, hangi plan
    # penceresi kullanılırsa kullanılsın güvenle içine düşmesini sağlar.
    # BİREBİR 200.000 KULLANILMAZ: araç oluşturulunca current_km, o aracın
    # "baseline_km"sine (sisteme giriş kilometresi) eşitlenir ve
    # check_and_open_maintenances/check_and_open_time_maintenances,
    # target <= baseline_km olan hedefleri kasıtlı olarak ASLA açmaz (bkz.
    # services.py — "aracın sisteme girmeden önce zaten yapılmış sayılan
    # bakımlar" kuralı). km TAM 200.000 olsaydı, target_km=200.000 ==
    # baseline_km=200.000 olur ve bu test hiçbir zaman bir aday görmezdi.
    model_real = "Sirio Test"
    r = client.post(
        "/api/vehicles",
        headers=auth(admin),
        json={"code": "9003", "name": "Test Tramvay 3", "current_km": 199_500, "model": model_real},
    )
    assert r.status_code == 200, r.text
    vehicle = r.json()

    rows = [
        _BULK_HEADERS,
        [model_real.upper(), "ARAÇ İÇİ", "AYDINLATMA SİSTEMİ", "Dış Aydınlatma Sistemi", 200000,
         "Dış farların gerekirse ayarlanması ve temizliği", "SKA-TST-01"],
        # kısaltmalı km kodu: 1M -> 10.000 km
        [model_real.upper(), "ARAÇ ALTI", "FREN SİSTEMİ", "Fren Balatası", "1M",
         "Fren balata kalınlığının kontrolü", "SKA-TST-02"],
        # kısaltmalı km kodu: 6Y -> 720.000 km (6 × 120.000) — 200.000'in tam
        # katı olsa da ARTIK ayrı, gerçek/görünür kendi planını alır.
        [model_real.upper(), "ARAÇ ÜSTÜ", "PANTOGRAF", "Karbon Şerit", "6Y",
         "Karbon şeridin aşınma kontrolü", "SKA-TST-04"],
        # BAKIM hücresi yorumlanamıyor ("?") — hiçbir zaman ağaca/plana
        # eklenmez, yalnızca unresolved_period raporunda görünür.
        [model_real.upper(), "ARAÇ İÇİ", "TCMS", "Manuel Kontroler", "?",
         "Mikrosviçin kontrolü", "SKA-TST-05"],
    ]
    xlsx = make_xlsx(rows)

    r = client.post(
        "/api/tree/import-bulk-catalog",
        headers=auth(admin),
        files={"file": ("kb_toplu_liste_test.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 200, r.text
    result = r.json()

    # ARAÇ hücresi "SİRİO TEST" (büyük harf) yazılsa da gerçek model adı
    # ("Sirio Test") ile eşleşip onunla kaydedilmeli.
    assert result["vehicle_models"] == [model_real]
    assert len(result["skipped"]) == 0
    assert len(result["unresolved_period"]) == 1
    assert "?" in result["unresolved_period"][0]["reason"]

    # Artık hiçbir periyot is_catalog_only olarak gizlenmiyor.
    assert result["catalog_only_periods"] == []

    # Bakım Planları sayfası (varsayılan GET /api/plans) yalnızca
    # YORUMLANABİLEN periyotları gösterir: 200.000, 10.000 (1M), 720.000
    # (6Y). "?" satırı için hiçbir plan oluşturulmaz.
    plans = client.get("/api/plans", headers=auth(admin)).json()
    by_model = [p for p in plans if p.get("vehicle_model") == model_real]
    assert len(by_model) == 3
    assert not any(p["plan_type"] == "unknown" for p in by_model)
    plan_200k = next(p for p in by_model if p["interval_km"] == 200_000)
    plan_10k = next(p for p in by_model if p["interval_km"] == 10_000)
    plan_720k = next(p for p in by_model if p["interval_km"] == 720_000)
    # model_real ("Sirio Test") "sirio" içerdiği için otomatik plan adları
    # artık kısa M/Y koduyla üretilir (bkz. services.default_plan_name /
    # sirio_period_label) — "X km Bakımı" yerine.
    assert plan_200k["name"] == "20M"
    assert plan_200k["is_catalog_only"] is False
    assert plan_10k["name"] == "1M"
    assert plan_720k["name"] == "6Y"
    assert plan_720k["is_catalog_only"] is False

    # Her periyodun kendi ağacında yalnızca kendi maddesi olmalı — hiçbiri
    # başka bir periyoda katılmıyor.
    def collect(nodes, out):
        for n in nodes:
            if n["card_code"]:
                out.add(n["card_code"])
            collect(n["children"], out)

    tree_200k = client.get(f"/api/tree?plan_id={plan_200k['id']}", headers=auth(admin)).json()
    codes_200k = set()
    collect(tree_200k, codes_200k)
    assert "SKA-TST-01" in codes_200k
    assert "SKA-TST-04" not in codes_200k, "6Y'nin maddesi 200.000'in ağacına katılmamalı"

    tree_720k = client.get(f"/api/tree?plan_id={plan_720k['id']}", headers=auth(admin)).json()
    codes_720k = set()
    collect(tree_720k, codes_720k)
    assert "SKA-TST-04" in codes_720k, "6Y'nin maddesi kendi (720.000) planının ağacında olmalı"

    # "Tümü" birleşik görünüm: her periyodun maddeleri, periyot etiketiyle
    # birlikte tek ağaçta görünmeli.
    merged = client.get(f"/api/tree/merged?vehicle_model={model_real}", headers=auth(admin)).json()
    merged_leaves: dict[str, str] = {}

    def collect_merged(nodes):
        for n in nodes:
            if n["card_code"]:
                merged_leaves[n["card_code"]] = n["period_label"]
            collect_merged(n["children"])

    collect_merged(merged)
    assert merged_leaves.get("SKA-TST-01") == "200.000 km"
    assert merged_leaves.get("SKA-TST-02") == "10.000 km"
    assert merged_leaves.get("SKA-TST-04") == "720.000 km"
    # "?" satırı hiç ağaca eklenmediği için "Tümü" görünümünde de yok.
    assert "SKA-TST-05" not in merged_leaves

    # ================================================================ Anında yeniden değerlendirme
    # Araç zaten 200.000 km'nin hemen altında (199.500) duruyordu — ne km
    # güncellemesi ne başka bir tetikleyici çağrıldı, sadece ağaç içe
    # aktarıldı. Bakım HEMEN aday (is_activated=False, "Başlatılmayı
    # Bekleyenler"de) olarak açılmış olmalı.
    # Not: seed veritabanındaki gerçek "Genel" (vehicle_model="") bakım
    # ağacında da tesadüfen 200.000 km'lik bir plan var; hedef km'de birden
    # fazla plan (Genel + bu modele özel) çakışınca kademeli birleştirme
    # mantığı TEK iş emri açar ve "yöneten" (governing) planı ikisinden
    # BİRİNİ seçer (bu motorun BEKLENEN davranışıdır) — bu yüzden burada
    # hedef km'ye (200.000) göre kontrol edilir, belirli bir plan id'sine göre değil.
    pending = client.get("/api/maintenances/pending-start", headers=auth(admin)).json()
    mine = [p for p in pending if p["vehicle"]["id"] == vehicle["id"]]
    target_200k_candidates = [p for p in mine if p["target_km"] == 200_000]
    assert len(target_200k_candidates) == 1, (
        f"200.000 km bakımı ağaç yüklenince hemen aday olarak açılmalıydı: {mine}"
    )


def test_bulk_catalog_import_dry_run_reports_but_does_not_persist(client, admin_token):
    """dry_run=true: gerçek yükleme ile BİREBİR AYNI sayaçlar/rapor üretilir
    ama veritabanına hiçbir şey yazılmaz — admin önce güvenle önizleyebilir."""
    admin = admin_token
    model_real = "Dry Run Test Model"

    rows = [
        _BULK_HEADERS,
        [model_real, "ARAÇ İÇİ", "TCMS", "Ekran", 90000, "Ekran testi", "DRY-01"],
        [model_real, "ARAÇ ALTI", "FREN", "Balata", "?", "Belirsiz madde", "DRY-02"],
    ]
    xlsx = make_xlsx(rows)

    r = client.post(
        "/api/tree/import-bulk-catalog",
        headers=auth(admin),
        files={"file": ("dry.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"dry_run": "true"},
    )
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["dry_run"] is True
    assert result["vehicle_models"] == [model_real]
    assert result["plans_created"] == 1  # yalnızca 90.000 km — "?" satırı için plan oluşturulmaz
    assert len(result["unresolved_period"]) == 1

    # Hiçbir şey kaydedilmemeli.
    plans = client.get("/api/plans", headers=auth(admin)).json()
    assert not any(p.get("vehicle_model") == model_real for p in plans)

    # Aynı dosyayı dry_run olmadan gönderince GERÇEKTEN kaydedilmeli.
    xlsx2 = make_xlsx(rows)
    r2 = client.post(
        "/api/tree/import-bulk-catalog",
        headers=auth(admin),
        files={"file": ("real.xlsx", xlsx2, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r2.status_code == 200, r2.text
    result2 = r2.json()
    assert result2["dry_run"] is False
    assert result2["plans_created"] == 1

    plans2 = client.get("/api/plans", headers=auth(admin)).json()
    assert any(p.get("vehicle_model") == model_real for p in plans2)


def test_bulk_catalog_import_vehicle_model_override_without_arac_column(client, admin_token):
    """Bakım Ağacı sayfasındaki yükleme formunda araç modeli seçilirse:
    dosyada ARAÇ sütunu HİÇ olmasa bile (yalnızca BÖLGE/SİSTEM/EKİPMAN/
    BAKIM/BAKIM KALEMİ/KART KODU) dosya işlenebilmeli, TÜM satırlar
    seçilen modele atanmalı."""
    admin = admin_token
    model_real = "Override Test Model"

    headers_no_arac = ["BÖLGE", "SİSTEM", "EKİPMAN", "BAKIM", "BAKIM KALEMİ", "KART KODU"]
    rows = [
        headers_no_arac,
        ["ARAÇ İÇİ", "TCMS", "Ekran Kartı", 50000, "Ekran kartı bağlantı kontrolü", "OVR-01"],
        ["ARAÇ ALTI", "FREN", "Kompresör", 100000, "Kompresör yağ seviyesi kontrolü", "OVR-02"],
    ]
    xlsx = make_xlsx(rows)

    r = client.post(
        "/api/tree/import-bulk-catalog",
        headers=auth(admin),
        files={"file": ("no_arac.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"vehicle_model": model_real},
    )
    assert r.status_code == 200, r.text
    result = r.json()

    assert result["vehicle_models"] == [model_real]
    assert len(result["skipped"]) == 0

    plans = client.get("/api/plans", headers=auth(admin)).json()
    by_model = sorted(p["interval_km"] for p in plans if p.get("vehicle_model") == model_real)
    assert by_model == [50000, 100000]
