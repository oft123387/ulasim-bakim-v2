# Ulaşım Bakım Yönetim Sistemi — Sistem Açıklaması

## 1. Proje Özeti

Ulaşım Bakım Yönetim Sistemi, bir tramvay filosunun periyodik bakımlarını uçtan uca yönetmek için geliştirilmiş bir web uygulamasıdır. Kağıt veya Excel üzerinden takip edilen bakım periyotlarının yerini alır: araç kilometresi bir bakım periyoduna ulaştığında ilgili iş emri sistem tarafından otomatik açılır, sahadaki usta ve mühendisler bu iş emirlerini bir kontrol listesi (bakım ağacı) üzerinden tamamlar, admin ise tüm süreci (kullanıcılar, yetkiler, planlar, raporlar) tek bir panelden yönetir.

Sistemi dört farklı rol kullanır: **admin** (tam yetkili), **mühendis**, **amir** (saha amiri/gözetmen, mühendisle aynı denetim seviyesinde) ve **usta**. Her rolün göreceği ve yapabileceği işlemler ayrıntılı bir izin matrisiyle kontrol edilir.

## 2. Sistem Gereksinimleri (Teknoloji Yığını)

**Backend**
- Python 3.12
- FastAPI (REST API çatısı)
- SQLAlchemy 2.0 (ORM)
- SQLite (veritabanı)
- python-jose (JWT üretimi/doğrulaması)
- passlib + bcrypt (şifre hashleme)

**Frontend**
- React 19 + TypeScript
- Vite (derleme aracı)
- Three.js (tramvayın 3D modelinin görüntülenmesi)
- Recharts (dashboard grafikleri)
- Tek sayfa uygulama (SPA) — sunucudan tek bir `index.html` ve derlenmiş JS/CSS paketi olarak sunulur

**Altyapı / Dağıtım**
- Docker (çok aşamalı build)
- docker-compose (tek komutla kurulum ve orkestrasyon)
- SMTP entegrasyonu (e-posta doğrulama ve şifre sıfırlama kodları için)

Bu yığın, küçük/orta ölçekli bir filo için tek sunucuda (hatta tek Docker konteynerinde) düşük bakım maliyetiyle çalışacak şekilde seçildi — ayrı bir veritabanı sunucusuna veya web sunucusuna ihtiyaç duymaz.

## 3. Mimari

Uygulama üç mantıksal katmandan oluşur, ancak tek bir Docker imajından dağıtılır:

1. **Tarayıcı (React SPA)** — kullanıcı arayüzü, JWT token'ı `Authorization: Bearer` başlığıyla her istekte gönderir.
2. **FastAPI Sunucusu** — REST API uçları, JWT doğrulama, rol/izin kontrolü, iş mantığı (otomatik bakım açma, bildirimler, yedekleme vb.).
3. **SQLite + Dosya Deposu** — veritabanı ve yüklenen dosyalar (fotoğraf, PDF, 3D model) diskte saklanır.

FastAPI, derlenmiş React çıktısını (`frontend/dist`) doğrudan kendi sürecinden statik dosya olarak sunar; böylece tek bir konteyner/süreç hem API'yi hem arayüzü karşılar, ayrı bir Nginx/Apache katmanına gerek kalmaz.

## 4. Veri Modeli

Veritabanında 18 tablo bulunur, dört ana kategoride gruplanabilir:

- **Kullanıcı & Erişim:** `User`, `Shift` (vardiya), `VerificationCode` (e-posta doğrulama/şifre sıfırlama kodları), `RolePermission` (izin matrisi)
- **Araç & Bakım:** `Vehicle`, `MaintenancePlan` (bakım periyodu tanımı), `TreeNode` (bakım ağacı şablonu), `Maintenance` (açılan iş emri), `MaintenanceItem` (iş emrine kopyalanan kontrol maddeleri), `MaintenanceNote`
- **İçerik & Medya:** `Model3D`, `PartHotspot` (3D model üzerindeki parça eşleşmeleri), `NodeAttachment`, `PartCatalogItem` (parça bilgi bankası, hiyerarşik), `PartCatalogAttachment`
- **Sistem & Kayıt:** `AuditLog` (değiştirilemez işlem kaydı), `AppSetting`, `Suggestion` (saha önerileri)

## 5. Proje Özellikleri

### Bakım Yönetimi Çekirdeği
- **Otomatik iş emri açma:** Araç kilometresi bir periyoda ulaştığında ilgili bakım kendiliğinden açılır; geçmiş/gereksiz hedefler atlanır.
- **Kademeli / birleşik bakım:** Bir km hedefi birden fazla periyoda denk geldiğinde (ör. 150.000 km hem 25.000 hem 50.000 km planına denk gelir) tek iş emrinde birleşik, tekilleştirilmiş bir kontrol listesi oluşturulur.
- **Canlı Alarm Merkezi & Takvim:** Geciken/yaklaşan bakımlar aciliyet seviyesine göre renklendirilir; takvim görünümü tahmini bakım tarihlerini gösterir.
- **Excel içe/dışa aktarma:** Bakım ağacı şablonları ve iş emri kayıtları toplu olarak aktarılabilir.

### Saha Operasyonları
- **Sınırsız derinlikte bakım ağacı:** Admin, ana başlık/alt madde yapısında istediği kadar seviyeye sahip kontrol listesi tanımlar.
- **Madde bazlı saha notu:** Usta/mühendis, bir maddeyi tikleme sırasında bulgu notu ekleyebilir; notlu maddeler ayrı bir panelde toplu görülür.
- **Vardiyalı çalışma modu:** Paylaşımlı tablet senaryosunda Vardiya 1/2/3 seçilir; madde tamamlama o vardiyadaki kişiye atfedilir. Vardiya modu kapalıyken tamamlama otomatik olarak oturum sahibine atanır, ayrıca isim seçmeye gerek kalmaz.
- **3D model üzerinde işaretleme:** Tramvayın 3D modelinde bir parçaya tıklanarak ilgili bakım maddesine doğrudan gidilebilir.

### Roller ve Erişim Yönetimi
- 4 rol: Admin, Mühendis, Amir, Usta.
- Her rol için görüntüleme, düzenleme, silme, onaylama, Excel dışa aktarma ve kullanıcı yönetimi izinleri ayrı ayrı açılıp kapatılabilir (ayrıntılı izin matrisi) — admin bu kısıtlamalardan her zaman muaftır.
- Yeni kayıtlar admin onayına düşer.

### Ek Modüller
- **Parça Bilgi Bankası:** Hiyerarşik alt parça yapısı (bir parçanın içine alt parçalar eklenebilir, sürükle-bırak ile sıralanabilir), doküman/şema/video ekleri.
- **Saha Önerileri:** Usta/mühendis/amir bir araç ve isteğe bağlı bir bakım periyodu için öneri gönderir; admin onaylar, reddeder veya bakım ağacına ekler. Yeni öneriler admin'e bildirim olarak düşer.
- **Değiştirilemez Denetim Kaydı:** Tüm oluşturma/güncelleme/silme işlemleri kalıcı, yalnızca-ekleme (append-only) bir kayda düşer; yalnızca admin görüntüler.
- **Global Arama & Bildirimler:** Araç, bakım, parça, kullanıcı çapında tek arama kutusu; bildirim merkezi ve otomatik günlük yedekleme.

## 6. Önemli Kod Örnekleri

### 6.1 Otomatik Bakım Açma Algoritması (`services.py`)

```python
# check_and_open_maintenances — basitleştirilmiş gösterim
base_unit = gcd(*[p.interval_km for p in active_plans])

checkpoint = base_unit
while checkpoint <= vehicle.current_km + tolerance:
    for plan in active_plans:
        if checkpoint % plan.interval_km == 0:
            open_or_merge_work_order(vehicle, plan, checkpoint)
    checkpoint += base_unit
```

Bu algoritmanın önemi: tüm aktif planların periyotlarının ortak bölenini (GCD) baz alarak tek bir kontrol noktası dizisi üretir. Böylece 150.000 km gibi bir hedef hem 25.000 hem 50.000 km planına denk geliyorsa TEK bir iş emri açılır ve kontrol listeleri birleştirilir; zaten geçilmiş veya alakasız uzak hedefler adaya alınmaz, kullanıcıya her zaman en yakın gerçek bakım gösterilir. `(vehicle_id, plan_id, target_km)` üzerindeki benzersizlik kısıtı, aynı iş emrinin iki kez açılmasını veritabanı seviyesinde engeller.

### 6.2 Katmanlı Yetkilendirme (`auth.py`)

```python
def require_permission(permission: str):
    def dep(user=Depends(get_current_user), db=Depends(get_db)):
        if not services.has_permission(db, user, permission):
            raise HTTPException(403, "Yetkiniz yok")
        return user
    return dep

@app.patch("/maintenances/{id}/assign")
def assign(user=Depends(require_permission("edit_maintenance"))):
    ...
```

Her uç nokta, FastAPI'nin `Depends` zinciriyle önce JWT'yi doğrular, sonra rol/izin kontrolünden geçer. İzinler kod içine gömülü değildir — `RolePermission` tablosunda rol bazlı açık/kapalı tutulur ve admin panelinden değiştirilebilir. Admin her zaman tüm izinlere sahiptir, bu yüzden yanlışlıkla kendi kendini kilitleme riski yoktur. Aynı desen, sistemdeki 89 API ucunun tamamında tutarlı şekilde uygulanır.

## 7. Güvenlik

### Kimlik Doğrulama
- **Bcrypt ile şifre hashleme:** Şifreler asla düz metin tutulmaz; `passlib.CryptContext` üzerinden bcrypt algoritmasıyla hashlenir.
- **JWT tabanlı oturum (HS256):** Girişte üretilen imzalı token 12 saat geçerlidir; sunucu tarafında oturum durumu tutulmaz (stateless), token `sub` (kullanıcı id) ve `role` bilgisini taşır.
- **E-posta doğrulama kodu:** Kayıt ve şifre sıfırlama kodları veritabanında yalnızca hash olarak saklanır (düz metin asla saklanmaz); 15 dakika geçerlidir, en fazla 5 deneme hakkı vardır.
- **Hesap onay akışı:** Yeni kayıtlar admin onayına düşer; onaylanmayan hesap sisteme giriş yapamaz.

### Yetkilendirme ve Denetim
- **Rol bazlı erişim:** Admin / Mühendis / Amir / Usta rolleri; her uç nokta ilgili role veya izin setine kilitlidir.
- **Ayrıntılı izin matrisi:** Görüntüleme, düzenleme, silme, onaylama, dışa aktarma, kullanıcı yönetimi — her biri rol bazında ayrı açılıp kapatılabilir.
- **Değiştirilemez denetim kaydı:** Kim, ne zaman, neyi değiştirdi — silinemez/değiştirilemez şekilde kayıt altına alınır.
- **Bildirim merkezi:** Yeni öneri, geciken bakım, yeni kullanıcı gibi kritik olaylar ilgili rollere anlık bildirim olarak düşer.

### Veri Bütünlüğü ve Süreklilik
- **Otomatik günlük yedekleme:** Her gün otomatik alınan yedekler ayrı bir alanda saklanır, en yeni 30 yedek tutulur, eski yedekler otomatik temizlenir.
- **İzole yedek deposu:** Yedekler, ana veritabanından fiziksel olarak ayrı bir Docker volume'ünde tutulur — veritabanı bozulsa da yedekler etkilenmez.
- **`.env` tabanlı sır yönetimi:** Gizli anahtar (`SECRET_KEY`), e-posta şifresi gibi bilgiler koda gömülmez; ortam dosyasından okunur, Docker imajına dahil edilmez.
- **Her zaman güncel arayüz:** Ana sayfa (`index.html`) önbelleğe alınmaz (`Cache-Control: no-store`) — hangi adresten girilirse girilsin en güncel sürüm otomatik yüklenir.

## 8. Dağıtım Mimarisi

Tek bir `Dockerfile`, iki aşamalı build kullanır:

1. `node:20-slim` → React/Vite arayüzü derlenir (`frontend/dist`)
2. `python:3.12-slim` → backend bağımlılıkları kurulur, derlenmiş arayüz bu imajın içine kopyalanır
3. Tek konteyner, port 8000 üzerinden hem API'yi hem arayüzü sunar

`docker-compose.yml` dört kalıcı volume tanımlar:

| Volume | İçerik |
|---|---|
| `db-data` | SQLite veritabanı |
| `uploads` | Fotoğraf, PDF, 3D model dosyaları |
| `backups` | Otomatik yedekler (db-data'dan fiziksel olarak ayrı) |
| `model-cache` | Arka plan kaldırma AI modelinin önbelleği |

Kod güncellemesi sonrası tek komutla (`docker compose up -d --build`) hem backend hem frontend yeniden derlenip devreye alınır; veritabanı ve dosyalar kalıcı volume'lerde korunur.
