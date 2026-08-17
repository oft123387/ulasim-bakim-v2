# Mimari ve Kod Rehberi

Bu doküman projeye yeni katılan bir yazılımcının "hangi dosya ne yapıyor,
bir istek nereden nereye gidiyor" sorusunu tek okumada cevaplaması için
yazıldı. Kurulum/çalıştırma için kök [`../README.md`](../README.md).

---

## 1. Genel resim

Tek bir Docker konteyneri içinde çalışan **monolit** bir uygulama:

```
Tarayıcı (React SPA)
   │  fetch  /api/...              (JSON, JWT Bearer)
   │  SSE    /api/events/stream    (canlı güncelleme)
   ▼
FastAPI  →  backend/app/main.py        HTTP katmanı: doğrulama, yetki, dosya
   │                                    yükleme, yanıt biçimlendirme
   ▼
         backend/app/services.py       İŞ KURALLARI — bakım ne zaman açılır,
   │                                    madde tamamlanınca ne olur, KPI nasıl
   │                                    hesaplanır
   ▼
SQLAlchemy → backend/app/models.py     24 tablo
   ▼
SQLite (varsayılan)  |  PostgreSQL (DATABASE_URL ile)
```

Frontend derlenmiş hâlde (`frontend/dist`) FastAPI tarafından statik olarak
servis edilir — ayrı bir web sunucusu yoktur (`main.py` sonunda
`app.mount("/", StaticFiles(...))`). Yani üretimde tek port, tek process.

**Önemli tasarım kararı:** Redis/Celery gibi harici bir altyapı bilinçli
olarak kullanılmıyor. Zamanlanmış işler FastAPI'nin kendi `startup`
olaylarında başlayan `asyncio` döngüleri; canlı güncelleme process içi bir
kuyruk. Bu, tek konteyner varsayımına dayanır — **birden fazla worker/replika
ile çalıştırılırsa bozulur** (bkz. bölüm 6).

---

## 2. Dizin dizin

### Kök dizin

| Yol | Ne işe yarar |
| --- | --- |
| `docker-compose.yml` | Üretim düzeni: `app` servisi + `watchtower` (imaj otomatik güncelleme). Volume'ler: veritabanı, `uploads/`, `backups/`, `logs/`. |
| `Dockerfile` | Çok aşamalı build: 1) Node ile `frontend` derlenir, 2) Python imajına kopyalanır. |
| `docker-entrypoint.sh` | Konteyner açılışında çalışır (izinler, uvicorn başlatma). |
| `baslat.bat` | Windows'ta Docker'sız hızlı başlatma (geliştirme). |
| `.env` / `.env.example` | **Sunucuya özel** ayarlar — `docker-compose.yml` okur (ör. `APP_PORT`). Commit edilmez. |
| `.github/workflows/ci.yml` | Her push/PR'da pytest + `tsc`/build; `main`'de imajı GHCR'a yayınlar. |
| `docs/` | Tüm dokümantasyon (bkz. [`README.md`](README.md)). |

### `backend/app/` — çekirdek

Dosyalar rolüne göre gruplanmış hâlde:

**Altyapı (küçük, önce bunlar okunmalı)**

| Dosya | Satır | Ne yapar |
| --- | --- | --- |
| `config.py` | 22 | `backend/.env` dosyasını okur. Öncelik: gerçek ortam değişkeni > `.env` > varsayılan. |
| `database.py` | 55 | Motor + `SessionLocal` + `get_db()` bağımlılığı. SQLite için WAL, `busy_timeout`, `foreign_keys=ON` pragma'larını her bağlantıda açar — bu pragma olmadan `ondelete=CASCADE` tanımları çalışmaz. |
| `auth.py` | 132 | JWT üretme/doğrulama, `get_current_user`, `require_admin` gibi FastAPI bağımlılıkları. |
| `logging_setup.py` | 53 | Log biçimi, `backend/logs/app.log`'a yazma. |
| `rate_limit.py` | 105 | Giriş/şifre sıfırlama gibi uçlarda istek hız sınırı. |
| `events.py` | 68 | Canlı güncelleme yayıncısı. `asyncio.Queue` yerine `threading.Lock` + `deque` kullanır; çünkü mutasyon uçları senkron `def` olduğu için Starlette onları thread pool'da çalıştırır ve `asyncio.Queue` thread-safe değildir. |

**Veri**

| Dosya | Satır | Ne yapar |
| --- | --- | --- |
| `models.py` | 939 | 24 SQLAlchemy tablosu (aşağıda). |
| `schemas.py` | 1206 | Pydantic giriş/çıkış şemaları. API sözleşmesi burada. |
| `migrate.py` | 265 | Elle yazılmış hafif migration'lar. Alembic **yok**: her açılışta çalışır, eksik sütunu `ALTER TABLE` ile ekler. SQLite'a özeldir. |

**İş kuralları**

| Dosya | Satır | Ne yapar |
| --- | --- | --- |
| `services.py` | 3032 | Projenin beyni. Bakım açma, ağaç snapshot'ı, madde tamamlama/kilit, yaklaşan bakım hesabı, takvim projeksiyonu, stok düşme, yetki matrisi, öneri onayı, KM simülasyonu. |
| `main.py` | 5484 | 145 HTTP ucu + 8 startup görevi + statik dosya mount'ları. |

**Yardımcılar (çıktı/entegrasyon)**

| Dosya | Satır | Ne yapar |
| --- | --- | --- |
| `excel_utils.py` | 1530 | Excel içe/dışa aktarma: araç listesi, KM, bakım ağacı kataloğu, KPI raporu (çok sayfalı). |
| `export_utils.py` | 547 | PDF / Word / CSV çıktıları (bakım geçmişi, iş emri, audit log). |
| `email_utils.py` | 149 | SMTP bildirimleri (kritik alarm, doğrulama kodu, şifre sıfırlama). |
| `backup_utils.py` | 144 | Günlük otomatik + elle tetiklenen SQLite yedeği → `backend/backups/`. |
| `qr_utils.py` | 35 | Araç ve parça QR kodları. |
| `bg_remove.py` | 73 | Parça fotoğrafından arka plan temizleme. |

### `backend/` kökü — betikler

* `seed.py` — ilk kurulum örnek verisi.
* `import_tree.py` — `data/koruyucu_bakim.xlsx`'ten gerçek bakım ağacını içe aktarır.
* `cleanup_nonbase_plans.py`, `reset_bulk_models.py` — tek seferlik veri düzeltme betikleri. Yeniden çalıştırmadan önce ne yaptıklarını okuyun.
* `tests/` — 25 dosyalık pytest paketi; iş kurallarının fiilî dokümantasyonu.
* `data/`, `uploads/`, `backups/`, `logs/`, `*.db` — çalışma zamanı verisi, repo'ya girmez (Docker'da volume).

### `frontend/src/`

Router kütüphanesi **yok**. `App.tsx` içindeki `page` state'i hangi sayfanın
render edileceğini belirler (`{page === 'dashboard' && <DashboardPage/>}`).
Yetki kontrolü de burada yapılır (`isAdmin`, `isStaff`, `p.view_...`).

| Dosya | Satır* | Ne yapar |
| --- | --- | --- |
| `main.tsx` | 336 B | Giriş noktası. |
| `App.tsx` | 24 KB | Kabuk: oturum, menü, sayfa seçimi, yetki kapıları, canlı olay bağlantısı. |
| `api.ts` | 4.8 KB | `api()` yardımcısı — token ekleme, 401'de refresh, hata çevirisi. |
| `types.ts` | 23 KB | Backend şemalarının TypeScript karşılığı. **Backend `schemas.py` değişince burası da elle güncellenmeli** — otomatik üretim yok. |
| `liveEvents.ts` | 2.9 KB | SSE istemcisi; olay gelince ilgili sayfa kendini yeniler. |
| `format.ts` | 5.3 KB | Tarih/km/sayı biçimleme. |
| `index.css` | 90 KB | Tüm stiller tek dosyada. CSS framework yok. |
| `useDetailHistory.ts` | 2.8 KB | Tarayıcı geri tuşunun detay panellerinde çalışması için. |

`pages/` — her biri bir menü öğesi: `DashboardPage`, `MaintenancesPage`
(118 KB, en büyük dosya — iş emri listesi + checklist ağacı + tamamlama
akışı), `CalendarPage`, `HistoryPage`, `KPIPage`, `VehiclesPage`,
`PlansPage`, `TreeEditorPage` (bakım ağacı şablon editörü),
`PartsCatalogPage`, `KnowledgeBasePage`, `SuggestionsPage`, `UsersPage`,
`PermissionsPage`, `AuditLogPage`, `SystemStatusPage`, `ProfilePage`,
`HelpPage`, `Login`.

`components/` — paylaşılanlar: `ui.tsx` (buton/modal/input temel seti),
`toast.tsx`, `ErrorBoundary.tsx`, `MaintenanceCalendar.tsx`,
`PendingCalendar.tsx`, `TopbarTools.tsx` (arama, bildirim),
`Tram3D.tsx` (three.js ile .glb model + hotspot),
`ParticleNetwork.tsx` (giriş ekranı animasyonu),
`onboarding.tsx` (ilk kullanım turu).

\* boyutlar dosya büyüklüğü.

---

## 3. Veri modeli (24 tablo)

Konularına göre:

* **Kimlik:** `users`, `refresh_tokens`, `verification_codes`, `shifts`, `role_permissions`
* **Filo:** `vehicles`, `vehicle_attachments`
* **Şablon (ne kontrol edilecek):** `maintenance_plans`, `tree_nodes`,
  `tree_node_vehicle_exceptions`, `tree_node_part_usages`, `node_attachments`
* **İş emri (fiilen yapılan):** `maintenances`, `maintenance_items`,
  `item_exec_attachments`, `maintenance_notes`
* **Görsel eşleme:** `models_3d`, `part_hotspots`
* **Parça bankası:** `part_catalog_items`, `part_repair_materials`, `part_catalog_attachments`
* **Sistem:** `audit_logs`, `app_settings`, `suggestions`

### En kritik ayrım: şablon ≠ iş emri

`tree_nodes` **şablondur** (admin düzenler). Bir bakım açıldığında şablon
`maintenance_items` tablosuna **kopyalanır** (`services._copy_tree_snapshot`).
Bu snapshot mantığı bilinçlidir: şablonda sonradan yapılan değişiklik
açık iş emirlerini etkilemez, aksi hâlde sahadaki bir iş emrinin maddeleri
altından değişirdi.

Bu yüzden `tree_nodes`'a dokunan her değişiklikte "açık iş emirleri ne
olacak?" sorusu ayrıca cevaplanır — `resync_maintenance_tiers` ve
`resync_open_items_mandatory_state` fonksiyonları tam olarak bunun içindir.

---

## 4. Ana akışlar

### A) Bakım otomatik açılıyor

1. Araç KM'si güncellenir (elle, Excel içe aktarma veya KM simülasyonu).
2. `services.check_and_open_maintenances` — aracın modeline uyan planlar
   (`_model_matches`) arasında hedef KM'ye tolerans penceresi içinde
   girilmiş olan var mı bakar (`plan_window`).
3. Aynı hedefe birden fazla periyot düşüyorsa (100.000 km hem 25.000 hem
   50.000'e bölünür) hepsinin checklist'i **tek** iş emrinde birleştirilir —
   kademeli/kümülatif bakım.
4. İş emri `is_activated = False` olarak oluşur: **hiçbir listede görünmez.**
5. Yetkili "Başlat" der → `activate_maintenance`. İleri tarih seçilirse
   `schedule_maintenance_start`, arka plan döngüsü o gün geldiğinde
   `run_scheduled_maintenance_starts` ile devreye alır.

Süre bazlı (ay/yıl) bakımlar için aynı akışın ikizi:
`check_and_open_time_maintenances`.

### B) Checklist maddesi tamamlanıyor

`POST /api/items/{id}/complete` → `services.complete_item`:

1. Maddeye tamamlayan kullanıcı + zaman damgası yazılır (vardiya modu
   açıksa seçilebilecek kişiler aynı vardiyayla sınırlıdır).
2. `_cascade_up` — tüm kardeşleri biten üst düğüm otomatik tamamlanıp
   **kilitlenir**. Kilidi yalnızca yetkili açabilir (`unlock_item`, açılınca
   `_reset_subtree` alt ağacı sıfırlar).
3. `_adjust_stock_for_node` — maddeye bağlı parça varsa stok düşülür.
4. `_check_maintenance_done` — tüm zorunlu maddeler bittiyse iş emri
   tamamlanır, `_auto_capture_completed_km` ile bitiş KM'si kaydedilir.
5. `log_action` audit'e yazar, `events.publish` bağlı ekranları tazeler.

### C) Bir istek uçtan uca

```
MaintenancesPage.tsx
  → api('/api/items/12/complete', {method:'POST', ...})   api.ts token ekler
    → main.py @app.post("/api/items/{item_id}/complete")
        auth.py         kim bu? yetkisi var mı? (has_permission)
        schemas.py      gövde doğrulama
        services.py     iş kuralı  ← asıl mantık burada
        models.py       DB yazma
        events.py       "yenile" yayını
    ← JSON yanıt
  ← SSE olayı → diğer açık ekranlar da yenilenir
```

Kural: **iş mantığı `main.py`'ye yazılmaz.** `main.py` doğrulama + yetki +
çağrı + yanıt; karar veren kod `services.py`'de olmalı.

---

## 5. Arka plan görevleri

Hepsi `main.py` sonundaki `@app.on_event("startup")` bloklarında başlar:

| Görev | Aralık | Ne yapar |
| --- | --- | --- |
| Süre bazlı bakım kontrolü | 30 dk | Ay/yıl dolan bakımları açar |
| Zamanlanmış başlatma | 15 dk | Tarihi gelen iş emirlerini aktive eder |
| KM simülasyonu | 30 dk | Demo/test için araç KM'sini otomatik ilerletir |
| Otomatik yedek | 30 dk | Günlük SQLite yedeği alır |
| Açılışta tek seferlik | — | Yetki varsayılanları, vardiyalar, baseline temizliği, `completed_km` backfill |

Hepsi `try/except` içinde: **arka plan görevi asla process'i çökertmemeli.**

---

## 6. Bilinmesi gereken kısıtlar

* **Tek process varsayımı.** SSE kuyruğu ve zamanlayıcılar process içidir.
  `uvicorn --workers 2` veya birden fazla replika → yedekler ikişer alınır,
  canlı güncelleme istemcilerin sadece bir kısmına gider.
* **Alembic yok.** Şema değişikliği `migrate.py`'ye elle eklenir ve pratikte
  SQLite'a göre yazılmıştır. PostgreSQL'e geçilirse bu dosya gözden
  geçirilmeli.
* **Tip senkronizasyonu elle.** `schemas.py` ↔ `types.ts` arasında otomatik
  üretim yok; birini değiştirip diğerini unutmak en sık hata kaynağı.
* **Dev dosyalar.** `main.py` 5.5 bin, `services.py` 3 bin,
  `MaintenancesPage.tsx` 2.762 satır. Bölünmeleri gerekiyor ama testler
  yeşilken, parça parça yapılmalı.
* **`index.css` tek dosya, 90 KB.** Sınıf adı çakışmasına dikkat.

## 7. Değişiklik yaparken

1. Önce ilgili testi okuyun — `backend/tests/` iş kurallarının en güncel
   anlatımıdır (ör. tolerans mantığı için `test_criticality_thresholds.py`,
   kademeli bakım için `test_manual_maintenance_tier_merge.py`).
2. İş kuralını `services.py`'de değiştirin, ucu `main.py`'de bırakın.
3. Şema değiştiyse: `models.py` + `migrate.py` + `schemas.py` + `types.ts` —
   dördü birlikte.
4. `cd backend && pytest` ve `cd frontend && npm run build` yeşil olmadan
   push etmeyin; CI zaten ikisini de çalıştırır.
