# Ulaşım Bakım Yönetim Sistemi

Araç kilometresine (veya süresine) göre bakımları otomatik açan, sınırsız
seviyeli ağaç yapılı (tree view) checklist'lerle tek sayfadan yönetilen,
rol bazlı yetkilendirmeye sahip kurumsal filo bakım yönetim sistemi.

Sistem; bakımın **ne zaman açılacağını** (km/tarih toleransına göre otomatik),
**neyin kontrol edileceğini** (admin'in tanımladığı, plana özel dinamik ağaç),
**kimin yaptığını** (her madde için kullanıcı + tarih/saat kaydı) ve
**performansın nasıl göründüğünü** (Dashboard, Takvim, KPI raporları) tek bir
yerde toplar.

## İçindekiler

* [Öne Çıkan Özellikler](#öne-çıkan-özellikler)
* [Roller ve Yetkiler](#roller-ve-yetkiler)
* [Mimari](#mimari)
* [Docker ile Çalıştırma (önerilen)](#docker-ile-çalıştırma-önerilen--tek-komut)
* [Sunucuda otomatik güncelleme](#sunucuda-otomatik-güncelleme-elle-komut-yazmadan)
* [Aynı sunucuda birden fazla proje](#aynı-sunucuda-birden-fazla-proje-çalıştırıyorsanız-port-çakışması)
* [Docker'sız kurulum](#kurulum-dockersız-elle)
* [İlk Giriş](#i̇lk-giriş)
* [Ortam Değişkenleri](#ortam-değişkenleri)
* [İşleyiş](#i̇şleyiş)
* [Tıklanabilir Tramvay Diyagramı ve 3D Model](#tıklanabilir-tramvay-diyagramı-ve-3d-model)
* [Veritabanı, Migration ve Yedekleme](#veritabanı-migration-ve-yedekleme)
* [PostgreSQL'e Geçiş](#postgresqle-geçiş)
* [Kademeli / Kümülatif Bakımlar](#kademeli--kümülatif-bakımlar)
* [Gerçek Bakım Ağacını Yeniden İçe Aktarma](#gerçek-bakım-ağacını-yeniden-i̇çe-aktarma)
* [Testler ve CI](#testler-ve-ci)
* [Proje Yapısı](#proje-yapısı)
* [Sık Karşılaşılan Sorunlar](#sık-karşılaşılan-sorunlar)

> **Projeye yeni katılıyorsanız** sırasıyla: bu README'nin
> [Mimari](#mimari) ve [Proje Yapısı](#proje-yapısı) bölümleri →
> [`docs/sistem-aciklamasi.md`](docs/sistem-aciklamasi.md) (iş kuralları) →
> [`docs/gelistirme-onerileri.md`](docs/gelistirme-onerileri.md) (bilinen
> eksikler / yapılacaklar). Tüm doküman listesi:
> [`docs/README.md`](docs/README.md).

## Öne Çıkan Özellikler

**Bakım motoru**
* Kilometreye VEYA takvim süresine (ay/yıl) göre otomatik bakım açma — her
  bakım yalnızca bir kez açılır, alt/üst tolerans yüzdesi veya sabit km ile
  yapılandırılabilir.
* Aynı hedef birden fazla periyoda bölünüyorsa (ör. 100.000 km hem 25.000 hem
  50.000'e bölünür) tüm periyotların checklist'i **tek** bir iş emrinde
  birleştirilir (kademeli/kümülatif bakım, aşağıda detaylı).
* **Bakım başlatma onayı**: otomatik tespit edilen adaylar doğrudan görünmez;
  yetkili biri "Başlat" demeden iş emri Bakımlar listesinde/Takvim'de
  görünmez. "Başlat" derken ileri bir tarih de seçilebilir — o tarih
  geldiğinde arka plandaki zamanlayıcı iş emrini otomatik devreye alır.
* Sınırsız seviyeli, admin'in serbestçe düzenleyebildiği plana özel ağaç
  şablonları; her bakım açıldığında şablon o iş emrine **kopyalanır**
  (snapshot) — sonradan şablonda yapılan değişiklikler açık iş emirlerini
  etkilemez.
* En alt seviye checkbox maddeleri işaretlenince üst düğümler otomatik
  tamamlanıp kilitlenir; her madde için tamamlayan kişi + tarih/saat kaydı.
  Kilitli maddeyi yalnızca yetkili roller açabilir.
* Checklist maddesi tikilirken/incelenirken **tablet kamerasıyla veya
  galeriden fotoğraf** eklenebilir (kanıt/arıza görseli, madde şablonundaki
  referans görselinden ayrı).
* Madde başına serbest metin **yürütme notu**; ayrıca bakıma genel not
  eklenebilir.

**Takvim, Dashboard ve KPI**
* Haftalık/aylık **Takvim** görünümü: her bakım "en geç bu tarihte yapılmalı"
  tahminiyle birlikte gösterilir.
* **Dashboard**: bekleyen/devam eden/tamamlanan sayıları, periyoda göre
  tamamlanma grafiği, zamanı gelen/yaklaşan bakımlar, son tamamlananlar.
* **KPI Raporu**: esnek tarih aralığı (son 7/30/90 gün, bu ay, bu yıl, özel
  aralık) ile tamamlanma oranı/süresi, güncel kritik/gecikmiş bakım sayıları,
  araç/model bazlı ve personel/vardiya bazlı performans — hem sayfa içinde
  grafik/tablo olarak, hem de tek tıkla çok sayfalı **Excel raporu** olarak.

**Öneriler (hiyerarşik onay)**
* Usta/mühendis/amir bir araç modeli + periyot/plan için bakım ağacına
  eklenmesini önerdiği bir madde girebilir; öneri **herkese** açık şekilde
  görünür.
* İki aşamalı onay: önce **amir** karar verir; amir onaylarsa sıra
  **mühendis**e geçer (mühendisin kararı kesindir, gerekirse ağaca ekler);
  amir reddederse öneri mühendise hiç düşmez.

**Kullanıcılar, roller ve yetkiler**
* Dört rol: **Admin**, **Mühendis**, **Amir**, **Usta**. Yetki Yönetimi
  sayfasından (yalnızca Admin) her rolün hangi işlemi yapabileceği ince
  taneli olarak açılıp kapatılabilir (bkz. [Roller ve Yetkiler](#roller-ve-yetkiler)).
  Kullanıcı öz kaydı admin onayı gerektirir.
* **Vardiya modu**: açıksa, madde tamamlama/not ekleme seçicisinde yalnızca
  aynı vardiyadaki kişiler listelenir (paylaşımlı tablet senaryosu — tek
  cihazda birden fazla kişi, oturum açıp kapatmadan çalışır).

**Referans ve dokümantasyon**
* **Bakım Ağacı** maddelerine admin tarafından açıklama, fotoğraf, PDF,
  teknik talimat veya harici bağlantı (video vb.) eklenebilir.
* **Tıklanabilir tramvay diyagramı**: admin'in görsel üzerinde tanımladığı
  bölgeler, ilgili checklist maddesiyle eşleşir; madde tamamlanınca bölge
  yeşile döner.
* **3D model + parça eşleştirme**: yüklenen `.glb` modeldeki parçalar da
  aynı şekilde checklist maddeleriyle eşleştirilebilir.
* **Parça Bilgi Bankası**: sık kullanılan fiziksel parçaların (görsel,
  doküman, devre şeması, video) genel referans kataloğu; stok takibi ve
  düşük stok uyarısı da içerir.

**Geçmiş, denetim ve bildirimler**
* **Bakım Geçmişi**: tamamlanan bakımlar tek listede, tarih/araç/plan/
  personel/km filtreleriyle; Excel/PDF/CSV/Word'e aktarılabilir.
* **İşlem Kayıtları (Audit Log)**: her kritik işlem (oluşturma, düzenleme,
  silme, tamamlama, kilit açma, içe/dışa aktarma, otomatik açılan bakımlar)
  değiştirilemez şekilde, kim/ne zaman/eski→yeni değeriyle kaydedilir —
  yalnızca Admin görür.
* **Canlı güncelleme**: bir kullanıcı bakım tamamlayınca/km güncelleyince
  diğer açık ekranlar anında (SSE ile) yenilenir; kritik alarmlar için
  e-posta bildirimi de gönderilebilir (SMTP yapılandırılırsa).
* **Sistem Durumu** (Admin): hangi veritabanına bağlı olduğu, temel
  sayaçlar, yedekleme durumu ve çalışma süresi gibi tanı bilgileri.

**Veri işlemleri**
* Araç bilgilerini ve kilometreyi Excel ile toplu içe/dışa aktarma.
* Otomatik **günlük veritabanı yedeği** (SQLite) + elle tetiklenebilir yedek.
* SQLite (varsayılan, sıfır kurulum) veya PostgreSQL (üretim için) desteği;
  şema değişiklikleri sunucu her açıldığında otomatik ve veri kaybı olmadan
  uygulanır (SQLite için).

## Roller ve Yetkiler

| Rol | Türkçe karşılığı | Varsayılan yetki seviyesi |
|---|---|---|
| `admin` | Yönetici | Her şeyi yapabilir, Yetki Yönetimi'ni de o kullanır — tek rol içinde sabittir, özelleştirilemez. |
| `engineer` | Mühendis | Bakımları görüntüler/düzenler, kilit açar, iş emri başlatır/elle açar, Excel'e aktarır. Kullanıcı yönetimi kapalı. |
| `amir` | Amir (saha amiri) | Mühendisle aynı denetim seviyesinde; ayrıca Takvim sayfasını ve önerilerin ilk onay aşamasını yönetir. |
| `technician` | Usta | Checklist maddelerini işaretler, not/fotoğraf ekler. Bakım başlatma, elle bakım açma ve kilit açma varsayılan olarak kapalı. |

Yukarıdakiler **fabrika varsayılanıdır** — Admin, **Yetki Yönetimi**
sayfasından her rol için şu izinleri ayrı ayrı açıp kapatabilir (ve
"varsayılana sıfırla" ile geri alabilir):

`view_maintenance`, `edit_maintenance`, `delete_maintenance`,
`approve_maintenance` (kilit açma), `start_maintenance`, `create_maintenance`
(elle bakım açma), `delete_all_maintenance` (tüm geçmişi silme — çok yıkıcı,
ayrı bir izin), `export_excel`, `manage_users`, `view_dashboard_analytics`
(Dashboard/KPI sayfalarının görünürlüğü).

## Mimari

```
React + TypeScript (frontend/)
        ↓  /api
FastAPI + SQLAlchemy (backend/)
        ↓
SQLite (varsayılan)  →  PostgreSQL'e geçiş: DATABASE_URL ortam değişkeni (backend/.env)
```

Ek bileşenler: sunucu içi arka plan zamanlayıcılar (otomatik bakım açma, KM
simülasyonu, zamanlanmış başlatma, günlük yedek, kritik alarm e-postası),
SSE tabanlı canlı güncelleme, GitHub Actions ile CI (pytest + tsc/build) ve
GHCR'a otomatik Docker imajı yayını.

## Docker ile Çalıştırma (önerilen — tek komut)

Docker Desktop/Engine kuruluysa, tüm sistemi (arayüz derlemesi + backend +
veritabanı) tek komutla ayağa kaldırabilirsiniz — Python/Node kurmanıza veya
`npm run build` çalıştırmanıza gerek yoktur:

```
docker compose up -d --build
```

Ardından tarayıcıda **http://localhost:8001** açın (varsayılan host portu
8001 — bkz. aşağıdaki "Aynı sunucuda birden fazla proje" bölümü). İlk
çalıştırmada örnek veri (kullanıcılar, araçlar, gerçek bakım ağacı, başlangıç
3D modeli) otomatik yüklenir.

* Veritabanı `db-data`, yüklenen dosyalar (fotoğraf/PDF/3D model) `uploads`,
  otomatik yedekler `backups`, arka plan kaldırma modeli önbelleği
  `model-cache` ve loglar `logs` adlı **kalıcı volume**'lerde tutulur;
  konteyneri silseniz bile verileriniz kaybolmaz.
* Kodda değişiklik yaptığınızda tekrar `docker compose up -d --build` çalıştırın
  (arayüz otomatik yeniden derlenir).
* Durdurmak: `docker compose down` · Logları izlemek: `docker compose logs -f`
* Üretimde `backend/.env` içindeki `SECRET_KEY` değerini mutlaka değiştirin
  (bkz. [Ortam Değişkenleri](#ortam-değişkenleri)).

### Sunucuda otomatik güncelleme (elle komut yazmadan)

Bir sunucuda çalıştırıyorsanız, her kod değişikliğinde elle `docker compose
up -d --build` yazmanıza gerek yok:

1. GitHub Actions her push'ta (testler geçerse) imajı otomatik derleyip
   GHCR'a (`ghcr.io`) yükler.
2. Sunucuda **bir kez** `docker compose up -d` çalıştırın (ilk kurulum).
3. Bundan sonra `docker-compose.yml` içindeki `watchtower` servisi GHCR'ı
   periyodik (5 dakikada bir) kontrol eder, yeni bir sürüm görürse imajı
   indirip uygulamayı kendisi yeniden başlatır — tamamen otomatik.

GHCR paketi varsayılan olarak **private**'tır; Watchtower'ın indirebilmesi için
sunucuda bir kez `docker login ghcr.io -u <kullanıcı-adınız> -p <PAT>` ile
giriş yapın (PAT'a `read:packages` yetkisi yeterli), ya da paketi GHCR
ayarlarından **public** yapın (bu durumda login gerekmez).

Sunucunun kendisi yeniden başladığında (elektrik kesintisi, reboot vb.)
Docker'ın da otomatik açılması için (Linux, systemd):

```
sudo systemctl enable docker
```

`docker-compose.yml`'deki servislerde zaten `restart: unless-stopped`
tanımlı — Docker açılınca container'lar da kendiliğinden ayağa kalkar.

Sıfırdan başlamak (tüm veriyi silmek) isterseniz: `docker compose down -v`.

### Aynı sunucuda birden fazla proje çalıştırıyorsanız (port çakışması)

Konteyner İÇİNDE uygulama her zaman 8000 portunda çalışır — bu sabittir.
Ama sunucudaki (host) port `APP_PORT` ortam değişkeninden okunur (varsayılan
**8001**). Aynı sunucuda başka bir proje de Docker ile çalışıyorsa ve o da
aynı host portunu kullanmaya çalışırsa, ikisi birden ayakta duramaz ("port
zaten kullanımda" hatası alırsınız).

Çözüm: bu depoda (docker-compose.yml ile aynı klasörde) `.env.example`
dosyasını `.env` olarak kopyalayıp her proje için FARKLI bir `APP_PORT`
verin:

```
cp .env.example .env
# .env içinde: APP_PORT=8001   (diğer proje için ör. APP_PORT=8002 verin)
```

Bu `.env` dosyası **`backend/.env`'den farklıdır** — o dosya uygulama
sırlarını/SMTP ayarlarını tutar, bu dosya yalnızca `docker compose`'un
kendisinin okuduğu host portunu belirler; ikisi birbirine karışmaz. `docker
compose up -d` normal şekilde çalıştırılır, `.env`'i otomatik okur.

## Kurulum (Docker'sız, elle)

Gereksinimler: Python 3.10+ ve (arayüzü kendiniz derleyecekseniz) Node.js 18+.

### 1. Backend

```
cd backend
pip install -r requirements.txt
python seed.py        # örnek veri + kullanıcılar + gerçek bakım ağacı (yalnızca ilk kurulumda)
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`frontend/dist` klasörü mevcutsa arayüz de aynı adresten sunulur:
tarayıcıda **http://localhost:8000** açın. Yerel ağdaki diğer bilgisayarlar
`http://<sunucu-ip>:8000` ile erişir.

Sunucu her başlatıldığında veritabanı şeması otomatik kontrol edilir; önceki
sürümlerde oluşturulmuş bir veritabanınız varsa eksik sütunlar/tablolar
otomatik eklenir, veri kaybı olmaz (`app/migrate.py`).

### 2. Frontend'i yeniden derlemek (yalnızca kod değiştirirseniz)

```
cd frontend
npm install
npm run build         # frontend/dist oluşturur
```

Geliştirme modu: `npm run dev` (5173 portu, /api ve /uploads istekleri 8000'e yönlenir).

## İlk Giriş

`seed.py` üç kullanıcı oluşturur: bir Admin, bir Mühendis, bir Usta hesabı.
Giriş bilgileri yalnızca terminaldeki `seed.py` çıktısında görüntülenir —
güvenlik nedeniyle burada veya sohbet ekranında paylaşılmaz. İlk girişten
sonra Kullanıcılar sayfasından şifreleri değiştirmeniz önerilir.

Yeni kullanıcılar giriş ekranındaki **"Kayıt ol"** bağlantısıyla kendi
hesaplarını oluşturabilir; hesap varsayılan olarak **Usta** rolünde ve
**onay bekliyor (pasif)** durumda açılır. Admin, Kullanıcılar sayfasından
"Aktifleştir" ile onaylamadan bu hesapla giriş yapılamaz.

## Ortam Değişkenleri

Uygulama sırları `backend/.env` dosyasında tutulur (`backend/.env.example`'dan
kopyalanır, git'e asla eklenmez). Docker ile çalıştırırken bu dosya konteynere
otomatik enjekte edilir.

| Değişken | Açıklama | Varsayılan |
|---|---|---|
| `SECRET_KEY` | JWT imzalama anahtarı — **üretimde mutlaka değiştirin**. Üretmek için: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` | placeholder değer |
| `DATABASE_URL` | Veritabanı bağlantısı | `sqlite:///./ulasim_bakim.db` |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` | E-posta doğrulama, şifremi unuttum ve kritik alarm bildirimleri için | boş (e-posta özellikleri devre dışı kalır) |
| `ALLOWED_ORIGIN` | CORS — siteyi gerçek bir alan adından yayınlarken o adresi yazın | `*` (tüm kaynaklara izin, yalnızca iç ağ için güvenli) |
| `BACKUP_DIR` | Otomatik veritabanı yedeklerinin yazıldığı klasör | Docker'da `/app/backups` |
| `LOG_DIR` | Merkezi uygulama/hata/erişim logları | Docker'da `/app/logs` |
| `U2NET_HOME` | Parça Bilgi Bankası'nda arka plan kaldırma modelinin önbelleği | Docker'da `/app/model_cache` |

Kök dizindeki `.env` dosyası ise **yalnızca `docker compose`'un kendisinin**
okuduğu, sunucuya özel değerleri tutar (`.env.example`'dan kopyalanır):

| Değişken | Açıklama | Varsayılan |
|---|---|---|
| `APP_PORT` | Uygulamanın sunucudaki host portu — aynı sunucuda başka projeler de varsa her biri farklı olmalı | `8001` |

## İşleyiş

1. **Dashboard**: menünün en üstünde, giriş sonrası ilk açılan sayfa. Toplam
   araç, bekleyen/devam eden/tamamlanan bakım sayıları, onay bekleyen
   kullanıcı sayısı (Admin), yaklaşan bakımlar ve son tamamlananlar listelenir.
2. **Bakım Planları** (Admin): periyot (km veya ay) + alt limit + üst limit
   (yüzde veya sabit km/ay olarak). Örnek: 1.025.000 km bakımı, %5/%10
   toleransla → aralık 1.012.500 – 1.027.500 km (tolerans en fazla 3.000 km'de
   tavanlanır). Sistemde gerçek koruyucu bakım listesinden içe aktarılan
   25.000 / 50.000 / 100.000 / 200.000 / 300.000 / 400.000 / 500.000 km
   planları hazır gelir.
3. **Araçlar** ekranından KM güncellenir (tek tek veya Excel ile toplu). Araç
   aralığa girdiğinde sistem bir **aday** iş emri oluşturur; yetkili biri
   Bakımlar sayfasındaki "Başlatılmayı Bekleyenler" bölümünden "Başlat"
   demeden iş emri hiçbir listede görünmez ve çalışılamaz (isteğe bağlı
   olarak ileri bir başlatma tarihi de seçilebilir). Aynı bakım ikinci kez
   açılmaz.
   - **Excel'den İçe Aktar**: Kod, Ad, KM, Üretici, Model, Şasi No, Not
     sütunlarını içeren bir dosya yükleyin — mevcut araç kodları güncellenir
     (KM güncellemesi otomatik bakım kontrolünü tetikler), yeni kodlar eklenir.
   - **Excel'e Aktar**: güncel araç listesini indirir.
4. **Bakım Ağacı** (Admin): her bakım planının **kendi bağımsız ağacı** vardır
   (25.000 km bakımında kontrol edilenlerle 100.000 km bakımında kontrol
   edilenler farklı olduğundan). Sayfanın üstünden düzenlenecek plan seçilir;
   her seviyede (ana bölüm → alt bölüm → bileşen → kontrol maddesi) sınırsız
   ekleme, yeniden adlandırma, taşıma ve silme yapılabilir. Şablon, açılan
   her bakıma kopyalanır (snapshot); sonradan yapılan şablon değişiklikleri
   açık bakımları etkilemez.
5. Usta bakımı açar, yalnızca **en alt seviye** maddeleri işaretler (isteğe
   bağlı olarak fotoğraf çekip ekleyebilir). Bir bölümün tüm maddeleri
   bitince üst düğüm otomatik tamamlanır ve **kilitlenir**; tüm ağaç bitince
   bakım "Tamamlandı" olur.
6. Her işarette tamamlayan kullanıcı + tarih + saat kaydedilir ve ağaçta
   görünür.
7. Kilitli maddeyi yalnızca `approve_maintenance` yetkisi olan roller açabilir
   (madde ve alt ağacı sıfırlanır, üst düğümler yeniden açılır).
8. **Madde ekleri** (Admin): Bakım Ağacı ekranında her maddenin yanındaki
   "Bilgi/Ek" düğmesiyle o maddeye açıklama/talimat metni, fotoğraf, PDF,
   teknik talimat dosyası veya harici bağlantı (ör. video) eklenir. Usta
   bakımı açtığında bu bilgiler ilgili maddenin altında görünür.
9. **Takvim**: haftalık görünüm varsayılan olarak açılır (aylık sekmesi de
   var); her bakım, "en geç bu tarihte" tahminiyle birlikte gösterilir.
10. **Öneriler**: usta/mühendis/amir bir araç modeli + periyot için öneri
    girer; herkes görür. Önce amir, sonra (amir onaylarsa) mühendis
    değerlendirir — mühendisin kararı kesindir.
11. **KPI Raporu**: tarih aralığı seçip tamamlanma/kritik/araç/personel
    performansını inceleyin, tek tıkla Excel'e aktarın.
12. **Bakım Geçmişi**: Tamamlanan bakımlar araç bazında, en yeni üstte
    gruplanır. Tarih aralığı, araç, periyot, personel ve kilometre aralığına
    göre filtrelenir; sonuç Excel/PDF/CSV/Word'e aktarılabilir.
13. **İşlem Kayıtları / Audit Log** (Admin): Sistemdeki her kritik işlem
    yapan kişi, tarih-saat ve gerektiğinde eski→yeni değeriyle
    **değiştirilemez** olarak kaydedilir.

## Tıklanabilir Tramvay Diyagramı ve 3D Model

Her bakım planına, üzerinde tıklanabilir bölgeler ("hotspot") tanımlanabilen bir
görsel (tramvay şeması/fotoğrafı) eklenebilir. **Bakım Ağacı** sayfasının en
altında, seçili plan için: görsel yükleyin, sonra görsel üzerinde fare ile
sürükleyerek bir bölge çizin ve o bölgeyi ağaçtaki bir maddeyle eşleştirin
(ör. tramvay üzerindeki pantograf bölgesini "Pantograf Kontrolü" maddesiyle
eşleştirin). Bölge sayısı ve konumu tamamen admin'in belirlediği şekildedir.

Usta bir bakımı açtığında, bu diyagram checklist'in üstünde görünür; ilgili
madde tamamlandıkça bağlı bölge yeşile döner ve üzerinde ✓ işareti çıkar,
bölgeye tıklayınca ilgili checklist maddesine gidilir.

Ayrıca yüklenen bir `.glb` 3D modeldeki mesh/parçalar da (fiziksel tramvay
plan'dan bağımsızdır, üzerindeki eşleşmeler plana özeldir) aynı mantıkla
checklist maddeleriyle eşleştirilebilir.

İlk kurulumda (`seed.py`), SketchUp'tan çıkarılan bir başlangıç görseli/model
otomatik olarak eklenir — admin bunun üzerine bölge çizebilir veya kendi
görselini/modelini yükleyip kullanabilir.

## Veritabanı, Migration ve Yedekleme

* Sunucu her başlatıldığında (`app/migrate.py`) veritabanı şeması otomatik
  kontrol edilir; eksik sütun/tablo varsa **veri kaybı olmadan** eklenir —
  önceki sürümlerden yükseltmede elle bir şey yapmanız gerekmez.
* SQLite kullanılıyorsa, sunucu açıkken **günde bir kez otomatik yedek**
  alınır (30 dakikada bir kontrol edilir, gerekiyorsa hemen alınır); Sistem
  Durumu sayfasından (Admin) elle de tetiklenebilir. Yedekler ayrı bir
  klasörde/volume'de tutulur (`BACKUP_DIR`).

## PostgreSQL'e Geçiş

`backend/.env` dosyasında:

```
DATABASE_URL=postgresql+psycopg2://kullanici:sifre@sunucu:5432/ulasim_bakim
```

satırını etkinleştirin ve `pip install psycopg2-binary` çalıştırın. Ayrıca
üretimde `SECRET_KEY` değerini mutlaka değiştirin. Not: otomatik şema
migration'ı yalnızca SQLite için çalışır; PostgreSQL'e geçişte tablo
şemaları `Base.metadata.create_all` ile (boş veritabanında) veya elle/Alembic
ile yönetilmelidir. Otomatik günlük yedekleme de yalnızca SQLite içindir —
PostgreSQL'de kendi yedekleme stratejinizi (ör. `pg_dump`) kullanmalısınız.

## Kademeli / Kümülatif Bakımlar

Bir hedef kilometre birden fazla planın periyoduna tam bölünüyorsa (ör.
100.000 km → 25.000, 50.000 ve 100.000'e bölünür) **tek** bir iş emri açılır
ve checklist bu planların ağaçlarının **birleşimi** olur. Her periyot, iş
emrinde kendi **"… km Kontrolleri"** başlığı altında ayrı bir grup olarak
görünür — böylece 100.000 km bakımının aynı zamanda 25.000 ve 50.000 km'yi de
kapsadığı açıkça bellidir. İş emrinin adını, bölünen planlar arasında periyodu
en büyük olan verir. Bakım detayında "Birleşik Görünüm / Ayrı Görünüm"
sekmeleriyle tüm maddeler bir arada ya da tek bir periyoda ait olarak
incelenebilir.

## Gerçek Bakım Ağacını Yeniden İçe Aktarma

`backend/data/koruyucu_bakim.xlsx` dosyası, üretilen 25.000-500.000 km bakım
planları ve ağaçlarının kaynağıdır (Sayfa1 sekmesi). Dosya güncellenirse:

```
cd backend
python import_tree.py data/koruyucu_bakim.xlsx
```

Bu işlem her periyodun ağacını Excel'deki güncel haliyle yeniden oluşturur
(idempotenttir, tekrar çalıştırmak güvenlidir). Açık bakımların snapshot'ları
etkilenmez.

## Testler ve CI

pytest tabanlı geniş bir test paketi (kullanıcı, bakım, ağaç, dashboard, KPI,
takvim, öneriler, Excel, 3D model/hotspot, plan/araç silme akışları dahil)
canlı bir sunucu gerektirmez — FastAPI'nin TestClient'ı ile izole, geçici bir
SQLite veritabanına karşı çalışır:

```
cd backend
pip install -r requirements-dev.txt
pytest -v
```

Frontend tip kontrolü:

```
cd frontend
npm install
npx tsc --noEmit -p tsconfig.app.json
```

Her push/PR'da GitHub Actions (`.github/workflows/ci.yml`) hem backend
testlerini hem frontend tip kontrolü + build'ini otomatik çalıştırır; ana
dala yapılan push'larda testler geçerse Docker imajı da otomatik GHCR'a
yayınlanır (bkz. [Sunucuda otomatik güncelleme](#sunucuda-otomatik-güncelleme-elle-komut-yazmadan)).

## Proje Yapısı

Kök dizinde yalnızca çalıştırma/dağıtım dosyaları durur; tüm dokümanlar
`docs/`, tüm kod `backend/` ve `frontend/` altındadır.

```
backend/                       FastAPI uygulaması
  app/
    main.py                    HTTP uç noktaları (router katmanı)
    services.py                İş kuralları (bakım açma, yetki, KPI, stok)
    models.py                  SQLAlchemy tabloları
    schemas.py                 Pydantic şemaları (API giriş/çıkış)
    database.py                Motor/oturum kurulumu, DATABASE_URL
    migrate.py                 Hafif otomatik şema migration'ları (SQLite)
    auth.py                    JWT kimlik doğrulama + yetki bağımlılıkları
    config.py                  Ortam değişkeni okuma
    events.py                  SSE canlı güncelleme yayını
    excel_utils.py             Excel içe/dışa aktarma (araç, katalog, KPI)
    export_utils.py            PDF / Word / CSV çıktıları
    email_utils.py             SMTP bildirimleri
    backup_utils.py            Otomatik + elle veritabanı yedeği
    logging_setup.py           Log biçimi ve dosyaya yazma
    rate_limit.py              İstek hız sınırı
    qr_utils.py                QR kod üretimi
    bg_remove.py               Parça görselinden arka plan temizleme
    assets/                    E-posta şablonu görselleri
  tests/                       pytest test paketi (24 dosya)
  data/                        Gerçek bakım kataloğu, varsayılan diyagram/3D model
  seed.py                      İlk kurulum örnek verisi
  import_tree.py               Excel'den bakım ağacını içe aktarma
  cleanup_nonbase_plans.py     Tek seferlik bakım betiği
  reset_bulk_models.py         Tek seferlik bakım betiği
  requirements.txt             Çalışma bağımlılıkları
  requirements-dev.txt         Test/geliştirme bağımlılıkları
  (uploads/ backups/ logs/ *.db  → çalışma zamanında oluşur, repo'ya girmez)

frontend/                      React + TypeScript + Vite
  src/
    pages/                     Her ana sayfa (Dashboard, Bakımlar, Takvim, KPI, ...)
    components/                Paylaşılan arayüz bileşenleri (ui.tsx, toast, 3D, ...)
    api.ts                     Backend'e istek yardımcıları
    types.ts                   Paylaşılan TypeScript tipleri
    format.ts                  Tarih/sayı biçimleme
    liveEvents.ts              SSE istemcisi
    index.css                  Tüm stiller (tek dosya)
  (dist/ node_modules/         → derleme çıktısı, repo'ya girmez)

docs/                          Tüm proje dokümantasyonu — bkz. docs/README.md
  sistem-aciklamasi.md
  gelistirme-onerileri.md
  sunucu-kurulumu.md
  bakim-periyot-duzeltmesi-notlari.md
  sunumlar/                    Yönetim/proje sunumları (.pptx)

docker-compose.yml             Üretim/geliştirme Docker düzeni (app + watchtower)
Dockerfile                     Çok aşamalı build (frontend derleme → backend imajı)
docker-entrypoint.sh           Konteyner başlangıç betiği
baslat.bat                     Windows'ta Docker'sız hızlı başlatma
.github/workflows/ci.yml       CI (pytest + tsc/build) ve GHCR imaj yayını
.env.example / backend/.env.example   Ortam değişkeni şablonları
```

## Sık Karşılaşılan Sorunlar

**"Kodda değişiklik yaptım ama arayüzde görünmüyor"** — Docker'sız
çalıştırıyorsanız `frontend/dist`'i yeniden derlediniz mi (`npm run build`)?
Docker ile çalıştırıyorsanız `--build` ile mi çalıştırdınız? Hangi
veritabanına/imaja bağlı olduğunuzdan emin değilseniz Admin olarak **Sistem
Durumu** sayfasına bakın.

**"Port zaten kullanımda" hatası** — Aynı sunucuda başka bir proje aynı host
portunu kullanıyor olabilir; bkz.
[Aynı sunucuda birden fazla proje](#aynı-sunucuda-birden-fazla-proje-çalıştırıyorsanız-port-çakışması).

**Watchtower imajı güncellemiyor** — GHCR paketi private ise sunucuda
`docker login ghcr.io` yapılmamış olabilir; `docker compose logs watchtower`
ile kontrol edin.

**Bir bakım "Başlatılmayı Bekleyenler"de görünmüyor ama araç toleransa
girmiş görünüyor** — Otomatik açılan iş emirleri, yetkili biri "Başlat"
demeden hiçbir listede görünmez (bkz. İşleyiş, madde 3) — bu, yanlışlıkla
erken/yanlış bir iş emrinin sahaya çıkmasını önlemek için bilinçli bir
tasarımdır.

**Sunucu yeniden başlayınca uygulama açılmıyor** — Docker'ın sunucu
açılışında otomatik başladığından emin olun (`sudo systemctl enable docker`,
yalnızca Linux). `docker-compose.yml`'deki `restart: unless-stopped` ancak
Docker çalışıyorsa devreye girer.
