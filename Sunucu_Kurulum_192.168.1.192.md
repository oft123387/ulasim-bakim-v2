# Ulaşım Bakım — 192.168.1.192 sunucusuna kurulum (SSH ile)

Hedef: `http://192.168.1.192:8001`

Projede Dockerfile, docker-compose.yml ve Watchtower zaten hazır. Aşağıdaki
adımlar sunucuda **bir kez** yapılır; sonrasında güncellemeler otomatiktir.

---

## 1. Sunucuya bağlanın

Windows'ta PowerShell açıp:

```powershell
ssh kullanici@192.168.1.192
```

(`kullanici` yerine sunucudaki kendi kullanıcı adınız.)

Docker'ı doğrulayın:

```bash
docker --version
docker compose version
```

`docker compose version` hata verirse eski sürüm olabilir; `docker-compose
version` deneyin ve aşağıdaki komutlarda `docker compose` yerine
`docker-compose` yazın.

Ayrıca kullanıcınız docker grubunda değilse komutların başına `sudo` ekleyin
ya da bir kez şunu yapıp yeniden giriş yapın:

```bash
sudo usermod -aG docker $USER
```

---

## 2. Projeyi sunucuya alın

```bash
mkdir -p ~/apps && cd ~/apps
git clone https://github.com/beyziki/ulasim-bakim-v2.git ulasim-bakim
cd ulasim-bakim
```

Depo private ise git kullanıcı adı + **Personal Access Token** soracaktır
(GitHub şifresi değil).

> Git kullanmak istemezseniz alternatif: Windows'tan dosyaları kopyalayın —
> `scp -r C:\Users\monster\Documents\Ulasim-Bakim kullanici@192.168.1.192:~/apps/ulasim-bakim`
> (bu durumda `node_modules`, `.git`, `backend/data` gibi klasörler de
> kopyalanacağı için yavaş olur; git tavsiye edilir).

---

## 3. Ayar dosyalarını oluşturun

`backend/.env` git'e girmez, sunucuda elle oluşturulması gerekir.

**a) Kök `.env` (host portu):**

```bash
cp .env.example .env
# İçinde APP_PORT=8001 yazıyor — 8001 kullanacaksanız değiştirmeye gerek yok.
```

Sunucuda 8001 portu başka bir proje tarafından kullanılıyorsa `.env` içindeki
`APP_PORT` değerini boş bir porta çevirin. Kontrol:

```bash
sudo ss -lntp | grep :8001
```

**b) `backend/.env` (uygulama sırları):**

```bash
cp backend/.env.example backend/.env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
nano backend/.env
```

`nano` içinde:

- `SECRET_KEY=` satırına yukarıdaki komutun ürettiği uzun değeri yapıştırın
  (**üretimde mutlaka değiştirin**).
- `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` satırlarına Windows'taki
  `backend\.env` dosyanızdaki gerçek Gmail bilgilerini yazın (uygulama
  şifresi). E-posta doğrulama / şifremi unuttum bunlarla çalışır.
- `DATABASE_URL` satırına dokunmayın — docker-compose zaten kalıcı volume
  yolunu (`/app/data/ulasim_bakim.db`) enjekte ediyor.

Kaydet: `Ctrl+O`, `Enter`, çık: `Ctrl+X`.

---

## 4. Başlatın

```bash
docker compose up -d --build
```

İlk derleme birkaç dakika sürer (npm build + pip install). Bittiğinde:

```bash
docker compose ps
docker compose logs -f app
```

Loglarda `Sunucu başlatılıyor` ve `Uvicorn running` satırlarını görmelisiniz.
`Ctrl+C` ile log takibinden çıkın (container durmaz).

---

## 5. Güvenlik duvarını açın

Sunucuda ufw varsa:

```bash
sudo ufw allow 8001/tcp
sudo ufw reload
```

firewalld varsa:

```bash
sudo firewall-cmd --permanent --add-port=8001/tcp
sudo firewall-cmd --reload
```

---

## 6. Test

Sunucunun kendisinden:

```bash
curl -I http://localhost:8001
```

Kendi bilgisayarınızdan tarayıcıda: **http://192.168.1.192:8001**

İlk açılışta örnek veri (kullanıcılar, araçlar, bakım ağacı) otomatik
yüklenmiş olur — README'deki varsayılan giriş bilgileriyle girip **şifreleri
hemen değiştirin**.

---

## Sonrası

| İşlem | Komut |
|---|---|
| Durum | `docker compose ps` |
| Log | `docker compose logs -f app` |
| Yeniden başlat | `docker compose restart app` |
| Durdur | `docker compose down` |
| Kod güncelleme (elle) | `git pull && docker compose up -d --build` |
| Sıfırdan başlat (TÜM VERİ SİLİNİR) | `docker compose down -v` |

**Otomatik güncelleme:** `docker-compose.yml` içindeki Watchtower servisi her
5 dakikada bir GHCR'ı kontrol eder; GitHub'a push yaptığınızda Actions imajı
derleyip yükler, Watchtower da sunucudaki container'ı otomatik yeniler. GHCR
paketi private ise sunucuda bir kez:

```bash
docker login ghcr.io -u beyziki -p <read:packages yetkili PAT>
```

**Sunucu yeniden başlarsa:** `restart: unless-stopped` sayesinde container
kendiliğinden ayağa kalkar. Docker servisinin açılışta başladığından emin
olun: `sudo systemctl enable --now docker`.

**Veri kalıcılığı:** veritabanı, yüklenen dosyalar, yedekler ve loglar Docker
volume'lerinde tutulur; `docker compose down` (`-v` olmadan) veri silmez.

---

## Sık karşılaşılan sorunlar

**"port is already allocated"** — 8001 dolu. `.env` içindeki `APP_PORT`
değerini değiştirip `docker compose up -d` tekrar çalıştırın.

**Tarayıcıdan açılmıyor ama `curl localhost:8001` çalışıyor** — güvenlik
duvarı (adım 5) veya ağdaki bir engelleme.

**`docker-entrypoint.sh: not found` / `exec format error`** — dosya Windows
satır sonlarıyla (CRLF) kopyalanmış. Git ile clone ettiyseniz oluşmaz;
scp ile kopyaladıysanız sunucuda: `sed -i 's/\r$//' docker-entrypoint.sh`
sonra tekrar build edin.

**Build sırasında npm/pip hataları** — sunucunun internet erişimi ve yeterli
disk alanı olduğundan emin olun (`df -h`). İmajın derlenmesi ~2 GB alan ister.
