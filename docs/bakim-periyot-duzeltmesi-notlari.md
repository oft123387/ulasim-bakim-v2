# Bakım Periyodu Düzeltmesi — Temel Periyot / Kat Periyot Notları

> Bu dosya, "1.200.000 km Bakımı gibi olmayan bakımların ayrı plan olarak
> açılması" sorununun düzeltmesini ve **ileride hatırlanması / değiştirilmesi
> gereken** noktaları özetler. Bir sonraki geliştirmede önce bunu oku.

## Sorun

"Bakım Planları" sayfasında **1.200.000 km Bakımı** gibi planlar görünüyordu.
Böyle bir bakım aslında yoktur; bu, **200.000 km bakımının katıdır**
(1.200.000 = 6 × 200.000). Araçlar (özellikle SİRİO'lar) ~1,5 milyon km'de
olduğu için, toplu katalog (KB TOPLU LİSTE) her farklı periyot değeri için
ayrı bir `MaintenancePlan` oluşturuyor, kümülatif km değerleri de sahte plan
hâline geliyordu.

## Temel Periyotlar (kullanıcı beyanı — KB)

Bu kümede **olmayan** her km periyodu, bir temel periyodun katı sayılır ve
ayrı plan olarak açılmaz; onu tam bölen **en büyük** temel periyoda katlanır.

| Model | Temel periyotlar (bin km) |
|-------|---------------------------|
| **SİRİO** | 10 / 30 / 60 / 100 / 200 / 500 |
| **Diğer** (Bozankaya vb.) | 25 / 50 / 100 / 200 / 300 / 500 |

Örnekler: SİRİO 1.200.000 → 200.000 · SİRİO 400.000 → 200.000 ·
Diğer 1.200.000 → 300.000 · Diğer 400.000 → 200.000.

> ⚠️ Bu kümeler **kod içinde sabittir**. Değişirse tek yer:
> `backend/app/excel_utils.py` içindeki `_BASE_INTERVALS_SIRIO` ve
> `_BASE_INTERVALS_DEFAULT`.

## Yapılan Değişiklikler

### `backend/app/excel_utils.py`
- `_BASE_INTERVALS_SIRIO`, `_BASE_INTERVALS_DEFAULT`, `_base_intervals_for()`
  ve `canonical_base_interval()` eklendi.
- **Excel okuma tamamen yenilendi (eksik alma düzeltmesi):**
  - Artık **tüm sayfalar** taranır (yalnızca ilk sayfa değil); katalog başlığı
    olmayan sayfalar otomatik atlanır.
  - **Başlık adına göre** eşleme yapılır (kolon SIRASI değil ADI önemli).
    İki düzen desteklenir: KB Toplu Liste (ARAÇ|BÖLGE|SİSTEM|EKİPMAN|BAKIM|
    BAKIM KALEMİ|KART KODU) ve hiyerarşik (ARAÇ|BAKIM|BAKIM KONUMU|EKİPMAN|
    ALT EKİPMAN|BAKIM KALEMİ 1..N).
  - **Bir satırdaki tüm "BAKIM KALEMİ 1..N" sütunları** ayrı madde olarak
    alınır (eskiden sadece ilki alınıyordu → maddelerin çoğu kayıptı).
  - **İleri doldurma:** birleştirilmiş/boş bırakılan üst kolonlar (ARAÇ/BAKIM/
    BÖLGE/SİSTEM/EKİPMAN) alttaki satırlara taşınır.
  - Başlık bulunamayan eski/başlıksız dosyalar için konumsal okuma yedeği var.
  - (Doğrulama: örnek Sayfa3 → 79 satır ama **169 madde** eksiksiz alındı.)
- **Tolerans varsayılanı -%50 → -%5 (+%10 aynı):** yeni planlarda `lower_pct`
  varsayılanı 5.0 yapıldı (`import_bulk_catalog`, `import_maintenance_tree`,
  ayrıca `models.py` ve `schemas.py` varsayılanları ve `PlansPage.tsx` formu).
- `import_bulk_catalog()`:
  - Temel olmayan (kat) km periyotları, bölen en büyük temel plana **katlanır**
    (ayrı plan açılmaz).
  - Aynı yol (bölge/sistem/ekipman/kalem) birden çok kattan gelirse **yaprak
    tekilleştirilir** (`seen_leaves`) — çift madde oluşmaz.
  - Dönüşe `collapsed_multiples` alanı eklendi (kullanıcıya raporlanır).
  - İçe aktarma sonunda, o modeldeki eski sahte planlar `purge_nonbase_plans`
    ile temizlenir.
- `purge_nonbase_plans()` eklendi (aşağıdaki script ile ortak kullanılır).

### `backend/cleanup_nonbase_plans.py` (YENİ)
Canlı veritabanındaki mevcut sahte planları temizleyen bağımsız script.
- Tamamlanmış (geçmiş) iş emirleri temel plana **taşınır** (geçmiş kaybolmaz).
- Açık iş emirleri silinir (km güncellemesinde yeniden açılır).
- Ağaç + 3D parça eşleşmeleri silinir; plan silinir (taşınacak temel plan yoksa
  ve geçmişi varsa yalnızca pasife alınır).

### `frontend/src/types.ts` + `frontend/src/pages/TreeEditorPage.tsx`
- `BulkCatalogImportResult.collapsed_multiples` tipi eklendi.
- Toplu katalog içe aktarma ekranında "X satır temel bakıma katlandı /
  Y eski sahte plan temizlendi" bilgisi gösterilir.

## Canlı Veritabanına Uygulama

Veritabanı Docker volume'ünde (`/app/data/ulasim_bakim.db`). Konteynerde çalıştır:

```bash
# 1) Önizleme (hiçbir şey değiştirmez):
docker compose exec backend python cleanup_nonbase_plans.py

# 2) Gerçekten uygula:
docker compose exec backend python cleanup_nonbase_plans.py --apply
```

Yeni kod devrede olduğu için **bundan sonraki** toplu katalog yüklemeleri
sahte plan oluşturmaz; script yalnızca mevcut kayıtları temizlemek içindir.
Uygulamadan önce yedek al (otomatik yedekler `backups` volume'ünde).

## Doğrulama (bu değişiklikte yapıldı)

- `canonical_base_interval` için 14 senaryo (SİRİO/Bozankaya, kat/temel) — geçti.
- Sentetik DB üzerinde `purge_nonbase_plans`: sahte plan silindi, tamamlanmış
  iş emri temel plana taşındı, açık iş emri ve ağaç silindi — geçti.
- Sentetik Excel ile `import_bulk_catalog`: yalnızca temel planlar oluştu,
  maddeler katlandı ve tekilleşti, sahte plan oluşmadı — geçti.

## İleride Gözden Geçirilecekler (ÖNEMLİ)

1. **Temel periyot kümeleri sabit.** Filoya yeni periyot/araç modeli girerse
   `_BASE_INTERVALS_*` güncellenmeli. Yeni bir model, adında "sirio" geçmiyorsa
   otomatik olarak **Diğer** kümesini kullanır.
2. **Model tespiti** `"sirio"` alt-dizgisiyle yapılıyor (`_base_intervals_for`).
   Farklı yazımlar (SIRIO/Sirio/SİRİO) normalize edilerek yakalanır; yeni bir
   marka için ayrı kümeye ihtiyaç olursa burası genişletilmeli.
3. **`import_maintenance_tree` (eski sütun-bloklu Excel / seed) DEĞİŞTİRİLMEDİ**
   — hâlâ yalnızca `_EXCLUDED_INTERVALS = {400_000}` kullanır. Canlı sistem
   toplu katalog yolunu kullandığı için buna dokunulmadı. Eğer o yol da
   kullanılacaksa aynı `canonical_base_interval` mantığı oraya da eklenmeli.
   (Not: "Diğer/Bozankaya" kümesine 50.000 temel periyot olarak EKLENDİ.)
4. **`vehicle_model` string tutarsızlığı (mevcut/pre-existing):** Planlar ARAÇ
   sütunundaki değeri saklar (ör. `"SİRİO"`), oysa `Vehicle.model`
   `_auto_vehicle_model` ile `"Sirio"` / `"Bozankaya Tip-1/2"` üretir.
   `import_bulk_catalog` içindeki "açık iş emirlerini sıfırla" filtresi
   (`Vehicle.model == vm`) bu yüzden eşleşmeyebilir. Bu düzeltmenin kapsamı
   dışında ama ayrıca ele alınmalı.
5. **Zamanlayıcı (scheduler) değiştirilmedi.** İş emirleri hâlâ kümülatif
   "Hedef KM" (ör. 1.200.000) ile açılır; ancak planın adı artık temel bakımdır
   (ör. "200.000 km Bakımı") — istenen davranış budur. Hedef KM sütunundaki
   büyük kümülatif değer, aracın o anki kilometresidir, sahte bir bakım değildir.
