# Ulaşım Bakım — Sistem İncelemesi ve Geliştirme Önerileri

*Kod tabanı (backend/FastAPI + SQLAlchemy, frontend/React 19 + TypeScript) satır satır incelenerek hazırlanmıştır. Aşağıdaki bulgular varsayım değil, koddaki somut karşılıklarıyla birlikte verilmiştir.*

## Genel Değerlendirme

Proje, boyutuna göre oldukça olgun. Özellikle şu noktalar beklenenin üzerinde:

- **Yedekleme sistemi** (`backup_utils.py`): SQLite'ın native backup API'si, günlük otomatik yedek, VACUUM ile sıkıştırma, eski yedeklerin otomatik temizlenmesi, audit kaydı — hepsi düzgün kurulmuş.
- **SQLite ayarları** (`database.py`): WAL modu + `busy_timeout` ile çoklu kullanıcıda "database is locked" hatalarına karşı önlem alınmış; `DATABASE_URL` üzerinden PostgreSQL'e geçiş zaten destekleniyor (tek satır değişiklik).
- **Kimlik doğrulama**: JWT + kısa ömürlü erişim jetonu + uzun ömürlü/iptal edilebilir yenileme jetonu, IP bazlı hız sınırlama, hesap bazlı kilitleme (5 hatalı denemede 15 dk kilit) — kurumsal seviyede.
- **Dosya yükleme**: Tehlikeli uzantı kara listesi, SVG script/event temizliği, UUID ile yeniden adlandırma (path traversal önlenmiş).
- **Token saklama**: `sessionStorage` kullanılmış (XSS'e karşı `localStorage`'a göre daha güvenli, sekme kapanınca temizlenir) — bilinçli bir tercih.

Aşağıdaki öneriler bunun üzerine, sistemi "çalışıyor"dan "kurumsal/üretime tamamen hazır"a taşımak için.

---

## 1. Güvenlik — Öncelikli

### 1.1 Varsayılan `SECRET_KEY` sessizce kullanılabiliyor
`backend/app/auth.py`:
```python
SECRET_KEY = config.get("SECRET_KEY", "degistir-bu-anahtari-uretimde")
```
`.env` dosyasında `SECRET_KEY` unutulursa/silinirse sistem **hiçbir uyarı vermeden** herkesin bildiği bu varsayılan anahtarla çalışmaya devam eder — bu, JWT'lerin sahtelenebilmesi anlamına gelir. Öneri: uygulama açılışında (`@app.on_event("startup")`) bu değerin hâlâ varsayılan olup olmadığı kontrol edilip, öyleyse loglara **kritik** seviyede uyarı basılmalı (ideal olarak `DATABASE_URL` postgresql ise, yani "üretim" kabul edilebilecek bir ortamdaysa, açılışı reddetmeli).

### 1.2 SQLite'ta yabancı anahtar (foreign key) zorlaması kapalı
`database.py`'de `PRAGMA journal_mode/synchronous/busy_timeout` ayarlanıyor ama `PRAGMA foreign_keys=ON` **yok**. Bu yüzden `models.py`'deki `ondelete="CASCADE"` / `ondelete="SET NULL"` tanımları SQLite tarafında fiilen **hiçbir işe yaramıyor** — bütünlük tamamen uygulama kodunun disiplinine kalmış durumda (nitekim bu yüzden kullanıcı silme özelliğinde referansları elle NULL'lamak gerekti). Öneri: `_set_sqlite_pragmas` içine `cursor.execute("PRAGMA foreign_keys=ON")` eklenmeli; ekledikten sonra tüm CRUD akışları (özellikle silme uçları) yeniden test edilmeli çünkü bazı yerlerde artık gerçek DB seviyesinde hata fırlayabilir.

### 1.3 Dosya yüklemede içerik (MIME/magic-byte) doğrulaması yok
Uzantı kara listesi var ama **hiçbir yerde** `file.content_type` kontrolü ya da dosyanın gerçek baytlarına bakan bir doğrulama yok. Biri `.jpg` uzantılı ama içeriği farklı bir dosya yükleyebilir. Öneri: `python-magic` veya Pillow'un kendi `Image.open().verify()`'ı ile resim yüklemelerinde içerik doğrulaması eklenmeli.

### 1.4 İstek/dosya boyutu sınırı yok
Hiçbir yerde `Content-Length` veya body-size sınırlayan bir middleware yok — biri çok büyük bir dosya göndererek diski/RAM'i zorlayabilir. Öneri: Starlette seviyesinde basit bir boyut-sınırlama middleware'i (ör. 25 MB) veya ters proxy (nginx `client_max_body_size`) seviyesinde sınır eklenmeli — ikisi de yoksa şu an hiçbir sınır yok demektir.

### 1.5 `ALLOWED_ORIGIN` varsayılanı `*`
`docker-compose.yml`'de `ALLOWED_ORIGIN: "*"` — yalnızca iç ağda kullanılıyorsa sorun değil, ama dışa açılırsa (`bakim.sirketiniz.com` gibi) mutlaka gerçek alan adına çevrilmesi gerektiği zaten yorumda belirtilmiş; bunu unutmamak için deploy checklist'ine eklenmeli.

---

## 2. Performans / Ölçeklenebilirlik

### 2.1 `compute_upcoming_maintenances` içinde N+1 sorgu
`backend/app/services.py:400-480` — her aktif araç için, her aday hedef km için ayrı bir `db.query(models.Maintenance)...first()` çağrısı var. ~80 araçlık filoda bu, tek çağrıda 100+ ayrı SQL sorgusu demek. Bu fonksiyon Dashboard (30 sn'de bir otomatik yenilenir), Araçlar sayfası uyarıları ve Bildirimler tarafından çağrılıyor. Öneri: ilgili `(vehicle_id, plan_id, target_km)` üçlülerini tek seferde `IN (...)` ile çekip Python tarafında sözlükle eşleştirmek — sorgu sayısını onlarcadan 1-2'ye indirir.

### 2.2 `/api/maintenances` sayfalama yok
`backend/app/main.py:1907` — `list_maintenances` filtrelerden geçtikten sonra `q.all()` ile **her şeyi** döndürüyor, `limit`/`offset` yok. Sistem "yıllarca kesintisiz" kullanılacağı için (kod yorumlarında defalarca vurgulanmış) bu tablo yıllar içinde on binlerce satıra ulaşacak; o noktada hem API yanıtı hem de tarayıcıda tüm listeyi state'te tutup filtreleyen `MaintenancesPage.tsx` yavaşlayacak. Bugün acil değil ama roadmap'e alınmalı — audit log uç noktasında zaten `limit/offset` deseni var, aynısı buraya da taşınabilir.

### 2.3 Frontend'de tam liste + istemci tarafı filtreleme
`VehiclesPage`, `MaintenancesPage`, `UsersPage` gibi sayfalar tüm veriyi çekip React state'inde filtreliyor. Bugünkü ölçekte (onlarca-yüzlerce kayıt) sorun değil; veri büyüdükçe sunucu taraflı filtre/sayfalama gerekecek.

---

## 3. Mimari / Sürdürülebilirlik

### 3.1 `main.py` tek dosyada 3512 satır
Tüm API uçları (auth, araçlar, bakımlar, kullanıcılar, parçalar, alarm, öneriler, yedekleme…) tek dosyada. Çalışıyor ama okunabilirlik/gezinme zorlaşıyor, git çakışma riski artıyor (birden fazla kişi aynı anda dokunursa). Öneri: FastAPI `APIRouter` ile alan bazlı bölme (`routers/vehicles.py`, `routers/maintenances.py`, `routers/users.py`, `routers/auth.py`...) — davranışı değiştirmeden, kademeli olarak yapılabilir.

### 3.2 Alembic yok, migration'lar "sütun ekle" scripti
`migrate.py` yalnızca eksik sütunları ekliyor, PostgreSQL'de bu devre dışı ("elle/Alembic ile yönetilmeli" diye kod içinde de belirtilmiş). Yani sistem PostgreSQL'e geçmeye teknik olarak hazır ama **migration altyapısı hazır değil** — bu an itibarıyla Postgres'e geçilse şema elle kurulmak zorunda. Öneri: Alembic kurulumu (mevcut SQLAlchemy modelleriyle uyumlu, ilk migration mevcut şemadan "baseline" olarak üretilebilir).

### 3.3 Büyük frontend sayfa dosyaları
`MaintenancesPage.tsx` (1979 satır), `VehiclesPage.tsx` (1020 satır), `PartsCatalogPage.tsx` (766 satır), `TreeEditorPage.tsx` (738 satır) — her biri birden fazla alt bileşen + form + iş mantığını tek dosyada barındırıyor. Öneri: her sayfayı `pages/maintenances/` gibi bir klasöre bölüp alt bileşenleri (`TreeItem`, `MaintenanceDetail`, `Model3DPanel` gibi zaten ayrı fonksiyonlar) kendi dosyalarına taşımak.

### 3.4 Paylaşılan mantık için `hooks/`/`utils/` katmanı yok
localStorage tabanlı liste okuma/yazma (favoriler, son açılanlar), arama debounce'u, "dışarı tıklayınca kapat" deseni gibi kalıplar birkaç dosyada tekrarlanıyor. Küçük bir `src/hooks/` klasörü (`useLocalStorageList`, `useDebouncedValue`, `useClickOutside`) tekrarı azaltır.

---

## 4. Test ve CI/CD — En Büyük Boşluk

### 4.1 `test_e2e.py` aslında bir pytest paketi değil
508 satır ama içinde `pytest` tarafından tanınacak `def test_...` fonksiyonu **yok** — çalışan bir sunucuya `urllib` ile istek atan, elle çalıştırılan (`python test_e2e.py`) bir script. Otomatik olarak (CI'da, her push'ta) çalıştırılamıyor.

### 4.2 Karmaşık iş mantığı hiç birim testi almıyor
`services.py`'deki kümülatif km penceresi hesaplama, kademeli bakım birleştirme (GCD tabanlı hedef üretimi), otomatik bakım açma gibi mantıklar — hata payı en yüksek yerler — izole birim testi olmadan yalnızca manuel/e2e testle doğrulanıyor.

### 4.3 Frontend'de sıfır test
`frontend/src`'de `.test.`/`.spec.` uzantılı hiçbir dosya yok, `package.json`'da bir test kütüphanesi (Vitest, Jest, Testing Library) tanımlı değil.

### 4.4 CI/CD pipeline yok
`.github/workflows` (veya başka bir CI config) bulunmuyor. Yani her push'ta otomatik derleme/test/lint kontrolü yapılmıyor — hatalar ancak elle fark edilirse yakalanıyor.

**Öneri sırası:** (1) `test_e2e.py`'yi gerçek `pytest` + `TestClient` (FastAPI'nin kendi test istemcisi, ayrı sunucu süreci gerektirmez) formatına taşı, (2) `services.py`'deki hesaplama fonksiyonları için saf birim testleri ekle, (3) basit bir GitHub Actions workflow'u (`pytest` + `npx tsc --noEmit` + `npm run build`) her push'ta çalışsın, (4) frontend'e Vitest + Testing Library ile kritik akışlar (login, checkbox tamamlama) için birkaç smoke test.

---

## 5. Güvenilirlik / Hata Yönetimi

### 5.1 React Error Boundary yok
`main.tsx`/`App.tsx`'te hiçbir yerde bir Error Boundary tanımlı değil. Herhangi bir sayfa bileşeninde beklenmeyen bir render hatası (ör. API'den beklenmedik `null` gelmesi) olursa kullanıcı **beyaz boş ekranla** kalır, hiçbir açıklama görmez. Öneri: `App`'i saran basit bir `ErrorBoundary` bileşeni — hata yakalanınca "Bir şeyler ters gitti, sayfayı yenileyin" gösterip isteğe bağlı olarak hatayı backend'e (audit/log) bildirsin.

### 5.2 Merkezi frontend hata/log toplama yok
Üretimde bir kullanıcıda hata oluşursa (konsola düşen JS hatası gibi) bunu geliştirici hiçbir şekilde göremiyor — kullanıcının şikayet etmesi gerekiyor. Sentry gibi ücretsiz katmanı olan bir hata izleme aracı (ya da en azından backend'e "client error" gönderen basit bir endpoint) eklenmesi, sorunları kullanıcıdan önce fark etmeyi sağlar.

---

## 6. Yedekleme — Küçük Ama Değerli Eksik

Sistem zaten iyi durumda (bkz. Genel Değerlendirme); eksik olan tek şey **otomatik bütünlük/geri yükleme doğrulaması**. Şu an yedekler alınıyor ama hiçbiri gerçekten "geri yüklenebilir mi" diye test edilmiyor. Öneri: periyodik olarak (ör. haftada bir) son yedek dosyası üzerinde `PRAGMA integrity_check` çalıştırıp sonucu audit kaydına/loglara yazan küçük bir kontrol eklenmesi — bozuk bir yedekle yıllar sonra karşılaşmamak için ucuz bir sigorta.

---

## Önceliklendirilmiş Yol Haritası

| Öncelik | Madde | Efor |
|---|---|---|
| Kritik | SECRET_KEY varsayılanı için açılış uyarısı/reddi | Düşük |
| Kritik | `PRAGMA foreign_keys=ON` + ardından tüm silme akışlarının testi | Orta |
| Yüksek | React Error Boundary | Düşük |
| Yüksek | `test_e2e.py`'yi pytest'e taşı + CI pipeline | Orta |
| Yüksek | Dosya yüklemede içerik/MIME doğrulaması + istek boyutu sınırı | Orta |
| Orta | `compute_upcoming_maintenances` N+1 düzeltmesi | Orta |
| Orta | Alembic'e geçiş | Orta |
| Orta | `main.py`'yi router'lara bölme | Yüksek (kademeli yapılabilir) |
| Orta | Yedek bütünlük kontrolü | Düşük |
| Düşük | `/api/maintenances` sayfalama | Düşük (bugün acil değil) |
| Düşük | Büyük sayfa dosyalarını bölme, `hooks/` katmanı | Yüksek (kademeli) |
| Düşük | Frontend test altyapısı (Vitest) | Orta |

Bu belge yalnızca **gözlemleri** listeler; hangisiyle başlamak istediğine karar verirsen, o maddeyi tek tek koda dökebilirim.
