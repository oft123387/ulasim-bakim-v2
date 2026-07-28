# Ulaşım Bakım Yönetim Sistemi

Araç kilometresine göre otomatik bakım açan, ağaç yapılı (tree view) checklist ile tek sayfadan yönetilen kurumsal bakım yönetim sistemi.

**Kapsam:** Bakım periyotları ve alt/üst limitler, kilometreye göre otomatik bakım açma (her bakım yalnızca bir kez), bakım iş emri, tek sayfada genişleyip daralan bakım ağacı, admin'in sınırsız seviyede düzenleyebildiği plana özel dinamik ağaç şablonları, en alt seviyede checkbox kontrol maddeleri, alt maddeler bitince üst düğümlerin otomatik tamamlanması ve kilitlenmesi (LOCK), admin kilit kaldırma, her madde için "kim yaptı / tarih / saat" kaydı, rol bazlı paneller (Admin / Mühendis / Usta), kullanıcı öz kaydı (admin onaylı), üst Dashboard, araç bilgilerini Excel ile içe/dışa aktarma, admin'in tanımladığı bölgelerle tıklanabilir tramvay diyagramı.

## Mimari

```
React + TypeScript (frontend/)
        ↓  /api
FastAPI + SQLAlchemy (backend/)
        ↓
SQLite (varsayılan)  →  PostgreSQL'e geçiş: DATABASE_URL ortam değişkeni (backend/.env)
```

## Docker ile Çalıştırma (önerilen — tek komut)

Docker Desktop kuruluysa, tüm sistemi (arayüz derlemesi + backend + veritabanı)
tek komutla ayağa kaldırabilirsiniz — Python/Node kurmanıza veya `npm run build`
çalıştırmanıza gerek yoktur:

```
docker compose up -d --build
```

Ardından tarayıcıda **http://localhost:8000** açın. İlk çalıştırmada örnek veri
(kullanıcılar, araçlar, gerçek bakım ağacı, başlangıç 3D modeli) otomatik
yüklenir.

* Veritabanı `db-data`, yüklenen dosyalar (fotoğraf/PDF/3D model) `uploads`
  adlı **kalıcı volume**'lerde tutulur; konteyneri silseniz bile verileriniz kaybolmaz.
* Kodda değişiklik yaptığınızda tekrar `docker compose up -d --build` çalıştırın
  (arayüz otomatik yeniden derlenir).
* Durdurmak: `docker compose down` · Logları izlemek: `docker compose logs -f`
* Üretimde `docker-compose.yml` içindeki `SECRET_KEY` değerini mutlaka değiştirin.

### Sunucuda otomatik güncelleme (elle komut yazmadan)

Bir sunucuda çalıştırıyorsanız, her kod değişikliğinde elle `docker compose
up -d --build` yazmanıza gerek yok:

1. GitHub Actions her push'ta imajı otomatik derleyip GHCR'a (`ghcr.io`) yükler.
2. Sunucuda **bir kez** `docker compose up -d` çalıştırın (ilk kurulum).
3. Bundan sonra `docker-compose.yml` içindeki `watchtower` servisi GHCR'ı
   periyodik (5 dakikada bir) kontrol eder, yeni bir sürüm görürse imajı
   indirip uygulamayı kendisi yeniden başlatır — tamamen otomatik.

GHCR paketi varsayılan olarak **private**'tır; Watchtower'ın indirebilmesi için
sunucuda bir kez `docker login ghcr.io -u <kullanıcı-adınız> -p <PAT>` ile
giriş yapın (PAT'a `read:packages` yetkisi yeterli), ya da paketi GHCR
ayarlarından **public** yapın (bu durumda login gerekmez).

Sıfırdan başlamak (tüm veriyi silmek) isterseniz: `docker compose down -v`.

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

## İşleyiş

1. **Dashboard**: menünün en üstünde, giriş sonrası ilk açılan sayfa. Toplam
   araç, bekleyen/devam eden/tamamlanan bakım sayıları, onay bekleyen
   kullanıcı sayısı (Admin), yaklaşan bakımlar ve son tamamlananlar listelenir.
2. **Bakım Planları** (Admin): periyot (ör. 25.000 km) + alt limit % + üst
   limit %. Örnek: 1.025.000 km bakımı → aralık 1.012.500 – 1.027.500 km.
   Sistemde gerçek koruyucu bakım listesinden içe aktarılan 25.000 / 50.000 /
   100.000 / 200.000 / 300.000 / 400.000 / 500.000 km planları hazır gelir.
3. **Araçlar** ekranından KM güncellenir (tek tek veya Excel ile toplu). Araç
   aralığa girdiğinde sistem bakımı **otomatik** açar ("Bekliyor") ve iş emri
   oluşturur. Aynı bakım ikinci kez açılmaz.
   - **Excel'den İçe Aktar**: Kod, Ad, KM, Üretici, Model, Şasi No, Not
     sütunlarını içeren bir dosya yükleyin — mevcut araç kodları güncellenir
     (KM güncellemesi otomatik bakım kontrolünü tetikler), yeni kodlar eklenir.
   - **Excel'e Aktar**: güncel araç listesini indirir.
4. **Bakım Ağacı** (Admin): her bakım planının **kendi bağımsız ağacı** vardır
   (25.000 km bakımında kontrol edilenlerle 100.000 km bakımında kontrol
   edilenler farklı olduğundan). Sayfanın üstünden düzenlenecek plan seçilir;
   her seviyede (ana bölüm → alt bölüm → bileşen → kontrol maddesi) sınırsız
   ekleme, yeniden adlandırma, taşıma ve silme yapılabilir — örn. "Araç İçi"
   altındaki "Yolcu Kabini" bölümüne yeni alt maddeler eklenip çıkarılabilir,
   aynı şekilde onun altındaki kontrol maddeleri de düzenlenebilir. Şablon,
   açılan her bakıma kopyalanır (snapshot); sonradan yapılan şablon
   değişiklikleri açık bakımları etkilemez.
5. Usta bakımı açar, yalnızca **en alt seviye** maddeleri işaretler. Bir
   bölümün tüm maddeleri bitince üst düğüm otomatik tamamlanır ve
   **kilitlenir**; tüm ağaç bitince bakım "Tamamlandı" olur.
6. Her işarette tamamlayan kullanıcı + tarih + saat kaydedilir ve ağaçta
   görünür.
7. Kilitli maddeyi yalnızca Admin açabilir (madde ve alt ağacı sıfırlanır,
   üst düğümler yeniden açılır).
8. **Madde ekleri** (Admin): Bakım Ağacı ekranında her maddenin yanındaki
   "Bilgi/Ek" düğmesiyle o maddeye açıklama/talimat metni, fotoğraf, PDF,
   teknik talimat dosyası veya harici bağlantı (ör. video) eklenir. Usta
   bakımı açtığında bu bilgiler ilgili maddenin altında görünür (fotoğraflar
   küçük önizleme, dosyalar indirilebilir bağlantı olarak).
9. **Bakım Geçmişi**: Tamamlanan bakımlar araç bazında, en yeni üstte
   gruplanır. Tarih aralığı, araç, periyot, personel ve kilometre aralığına
   göre filtrelenir; sonuç Excel'e aktarılabilir. Bir kayda tıklayınca o
   bakımın eski checklist detayı açılır.
10. **İşlem Kayıtları / Audit Log** (Admin): Sistemdeki her kritik işlem
    (oluşturma, düzenleme, silme, tamamlama, kilit açma, içe/dışa aktarma ve
    otomatik açılan bakımlar) yapan kişi, tarih-saat ve gerektiğinde eski→yeni
    değeriyle **değiştirilemez** olarak kaydedilir. Bu kayıtlar yalnızca Admin
    tarafından görüntülenir; aksiyon türü, kayıt türü, personel, tarih ve
    serbest metinle filtrelenir.

## Tıklanabilir Tramvay Diyagramı

Her bakım planına, üzerinde tıklanabilir bölgeler ("hotspot") tanımlanabilen bir
görsel (tramvay şeması/fotoğrafı) eklenebilir. **Bakım Ağacı** sayfasının en
altında, seçili plan için: görsel yükleyin, sonra görsel üzerinde fare ile
sürükleyerek bir bölge çizin ve o bölgeyi ağaçtaki bir maddeyle eşleştirin
(ör. tramvay üzerindeki pantograf bölgesini "Pantograf Kontrolü" maddesiyle
eşleştirin). Bölge sayısı ve konumu tamamen admin'in belirlediği şekildedir.

Usta bir bakımı açtığında, bu diyagram checklist'in üstünde görünür; ilgili
madde tamamlandıkça bağlı bölge yeşile döner ve üzerinde ✓ işareti çıkar,
bölgeye tıklayınca ilgili checklist maddesine gidilir.

İlk kurulumda (`seed.py`), SketchUp'tan (`Tramvay+1.skp`) çıkarılan bir
başlangıç görseli otomatik olarak her plana eklenir — admin bunun üzerine
bölge çizebilir veya kendi görselini yükleyip kullanabilir.

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

## Testler

pytest tabanlı uçtan uca test paketi (kullanıcı, bakım, ağaç, dashboard,
Excel, 3D model/hotspot, plan/araç silme akışları) canlı bir sunucu
gerektirmez — FastAPI'nin TestClient'ı ile izole, geçici bir SQLite
veritabanına karşı çalışır ve her push/PR'da GitHub Actions'ta
(`.github/workflows/ci.yml`) otomatik çalışır:

```
cd backend
pip install -r requirements-dev.txt
pytest -v
```

## PostgreSQL'e Geçiş

`backend/.env` dosyasında:

```
DATABASE_URL=postgresql+psycopg2://kullanici:sifre@sunucu:5432/ulasim_bakim
```

satırını etkinleştirin ve `pip install psycopg2-binary` çalıştırın. Ayrıca
üretimde `SECRET_KEY` değerini mutlaka değiştirin. Not: otomatik şema
migration'ı yalnızca SQLite için çalışır; PostgreSQL'e geçişte tablo
şemaları `Base.metadata.create_all` ile (boş veritabanında) veya elle/Alembic
ile yönetilmelidir.

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

## Tamamlanan Fazlar (bu sürümde eklendi)

* **Madde ekleri (Faz 10)** — ağaç maddelerine not/fotoğraf/PDF/talimat/bağlantı;
  usta ekranında görüntülenir.
* **Bakım geçmişi + filtreleme (Faz 13-14)** — araç bazlı, en yeni üstte;
  tarih/araç/plan/personel/km filtreleri.
* **Excel rapor dışa aktarma (Faz 16)** — Bakımlar ve Bakım Geçmişi
  sayfalarından, aktif filtrelerle.
* **Audit log (Faz 17)** — değiştirilemez işlem kayıtları, yalnızca Admin.

Bu değişikliklerden sonra **arayüzü yeniden derlemek gerekir**
(`cd frontend && npm run build`); backend ilk başlatmada eksik tablo/sütunları
otomatik ekler (`app/migrate.py` + `create_all`).
