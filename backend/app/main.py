import asyncio
import io
import json
import math
import os
import re
import time
import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from sqlalchemy import or_
from sqlalchemy.orm import Session

from starlette.concurrency import run_in_threadpool

from . import backup_utils, bg_remove, config, email_utils, events, excel_utils, export_utils, logging_setup, models, qr_utils, rate_limit, schemas, services
from .auth import (
    ALGORITHM,
    SECRET_KEY,
    create_token,
    get_current_user,
    hash_password,
    issue_refresh_token,
    require_admin,
    require_permission,
    require_staff,
    revoke_refresh_token,
    rotate_refresh_token,
    verify_password,
)
from .database import Base, IS_SQLITE, SessionLocal, engine, get_db
from .migrate import run_light_migrations

Base.metadata.create_all(bind=engine)
run_light_migrations(engine)

log = logging_setup.logger

app = FastAPI(title="Ulaşım Bakım Yönetim Sistemi")

# Süreç (worker) ne zaman başladı — "Sistem Durumu" sayfasında çalışma
# süresini göstermek için. Docker'da konteyner yeniden başlatılınca/imaj
# yeniden derlenince bu da sıfırlanır — dolaylı olarak "kod ne zaman en son
# gerçekten devreye girdi" sorusuna da yanıt verir.
_APP_STARTED_AT = datetime.now()


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    """Ayrıntılı hata logları (Faz 23 — Güvenlik sertleştirme). Beklenmeyen
    (kod hatası kaynaklı) her istisna tam yığın izi (stack trace) ile
    merkezi log dosyasına yazılır; istemciye ise ayrıntı SIZDIRMADAN genel
    bir mesaj döner (bilgi ifşası / information disclosure önlemi —
    Python traceback'leri sunucunun iç yapısı hakkında saldırgana bilgi
    verebilir). HTTPException'lar (400/403/404 vb. — kasıtlı, beklenen
    hatalar) FastAPI'nin kendi mekanizmasıyla ayrıca ele alınır, bu
    handler'a hiç düşmezler."""
    log.exception("Beklenmeyen hata: %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Sunucuda beklenmeyen bir hata oluştu."}, status_code=500)


# Değiştirilmemiş varsayılan anahtarla üretimde çalışmak, JWT jetonlarının
# sahtelenebileceği anlamına gelir (herkes bu değeri kaynak koddan görebilir).
_DEFAULT_SECRET_KEY = "degistir-bu-anahtari-uretimde"


@app.on_event("startup")
def _secret_key_startup_check():
    """Kritik güvenlik kontrolü: backend/.env dosyasında SECRET_KEY hiç
    ayarlanmamışsa auth.py sessizce herkesçe bilinen bir varsayılana düşer.
    DATABASE_URL PostgreSQL gösteriyorsa (bu, yerel SQLite kurulumundan
    farklı olarak bir 'üretim' ortamının işareti sayılır) varsayılan
    anahtarla açılış tamamen REDDEDİLİR; SQLite'ta (küçük/iç ağ kurulumu
    varsayımı) açılışa izin verilir ama loglara kritik seviyede uyarı
    yazılır ki fark edilmeden geçilmesin."""
    if SECRET_KEY != _DEFAULT_SECRET_KEY:
        return
    hint = 'python -c "import secrets; print(secrets.token_hex(32))"'
    if not IS_SQLITE:
        raise RuntimeError(
            "GÜVENLİK: SECRET_KEY ayarlanmamış (varsayılan değerle çalışılıyor) ve "
            "DATABASE_URL PostgreSQL gösteriyor — bu üretim ortamı kabul edilir. "
            "backend/.env dosyasına benzersiz, rastgele bir SECRET_KEY eklemeden sunucu "
            f"başlatılmayacak. Üretmek için: {hint}"
        )
    log.critical(
        "GUVENLIK UYARISI: SECRET_KEY ayarlanmamis, herkesce bilinen varsayilan degerle "
        "calisiyorsunuz! JWT oturum jetonlari sahtelenebilir. backend/.env dosyasina "
        "benzersiz bir SECRET_KEY ekleyin: %s",
        hint,
    )

# Gerçek bir alan adından yayınlarken ALLOWED_ORIGIN ortam değişkenini o
# adrese ayarlayın (docker-compose.yml). Ayarlanmazsa/"*" ise tüm
# kaynaklara izin verilir — yalnızca dış erişimi olmayan/iç ağ
# kullanımında güvenli kabul edilmelidir.
_allowed_origin = config.get("ALLOWED_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_allowed_origin] if _allowed_origin != "*" else ["*"],
    allow_credentials=_allowed_origin != "*",
    allow_methods=["*"],
    allow_headers=["*"],
)


class _MaxBodySizeExceeded(Exception):
    pass


class MaxBodySizeMiddleware:
    """Saf ASGI middleware — istek gövdesi (dosya yüklemeleri dahil) bir
    sınırı aşarsa 413 döner. Starlette'in kendi BaseHTTPMiddleware'i tüm
    gövdeyi işlemeden önce belleğe tamponladığı için (büyük dosyalarda
    gereksiz bellek kullanımı) burada ham ASGI seviyesinde, gövde
    akarken sayarak çalışılır — sınır aşılır aşılmaz istek daha fazla
    veri okumadan kesilir. Content-Length başlığı varsa (çoğu istekte
    öyledir) önce onunla hızlı bir ön kontrol yapılır; olmasa/yanlış
    olsa bile gerçek akan baytlar sayıldığı için yine de korunur."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_bytes:
                    await self._reject(send)
                    return
            except ValueError:
                pass

        total = 0

        async def limited_receive():
            nonlocal total
            message = await receive()
            if message.get("type") == "http.request":
                total += len(message.get("body") or b"")
                if total > self.max_bytes:
                    raise _MaxBodySizeExceeded()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _MaxBodySizeExceeded:
            await self._reject(send)

    @staticmethod
    async def _reject(send):
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json; charset=utf-8")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": '{"detail":"Yüklenen dosya/istek çok büyük"}'.encode("utf-8"),
            }
        )


# Varsayılan 50 MB — 3D model (.glb) dosyaları da bu uçtan yüklendiği için
# yalnızca fotoğraf/PDF düşünülerek çok düşük tutulmadı; ortam değişkeniyle
# (MAX_UPLOAD_MB) değiştirilebilir.
_MAX_UPLOAD_BYTES = int(config.get("MAX_UPLOAD_MB", "50")) * 1024 * 1024
app.add_middleware(MaxBodySizeMiddleware, max_bytes=_MAX_UPLOAD_BYTES)


@app.middleware("http")
async def _capture_request_context(request: Request, call_next):
    """Denetim kaydına (audit log) IP/tarayıcı bilgisi düşürebilmek için,
    her isteğin başında bu bilgiyi bir ContextVar'a yazar — services.log_action
    her çağrıda ayrıca `request` parametresi almadan bu bilgiye ulaşabilir,
    böylece 90 uç noktanın imzasını tek tek değiştirmeye gerek kalmaz."""
    ip = request.client.host if request.client else ""
    # Ters proxy arkasındaysa gerçek istemci IP'si X-Forwarded-For'da olabilir.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
    ua = request.headers.get("user-agent", "")
    token_ip = services.REQUEST_IP.set(ip)
    token_ua = services.REQUEST_UA.set(ua)
    try:
        return await call_next(request)
    finally:
        services.REQUEST_IP.reset(token_ip)
        services.REQUEST_UA.reset(token_ua)


@app.middleware("http")
async def _access_log(request: Request, call_next):
    """Merkezi erişim logu (Faz 23 — Güvenlik sertleştirme). Her API
    isteğinin yöntem/yol/durum kodu/süresini merkezi log dosyasına yazar —
    olağan dışı bir erişim düzenini (ör. tek bir IP'den anormal istek
    hacmi) sonradan incelemek için (audit_log tablosu YALNIZCA anlamlı iş
    olaylarını tutar — bu ise ham HTTP trafiğinin kaydıdır)."""
    start = time.monotonic()
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        duration_ms = (time.monotonic() - start) * 1000
        log.info(
            "%s %s -> %d (%.0fms)",
            request.method, request.url.path, response.status_code, duration_ms,
        )
    return response


@app.middleware("http")
async def _rate_limit(request: Request, call_next):
    """API hız sınırlama (Faz 23 — Güvenlik sertleştirme). Yalnızca /api/*
    uç noktaları sayılır (statik dosyalar/SPA kabuğu hariç). Asıl kaba
    kuvvet/DoS koruması ters proxy veya güvenlik duvarı seviyesinde
    olmalıdır; bu, ikinci bir savunma katmanıdır."""
    if request.url.path.startswith("/api/"):
        ip = request.client.host if request.client else "anon"
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            ip = forwarded.split(",")[0].strip()
        if not rate_limit.api_limiter.allow(ip):
            retry = rate_limit.api_limiter.retry_after(ip)
            return JSONResponse(
                {"detail": "Çok fazla istek gönderildi, lütfen biraz sonra tekrar deneyin."},
                status_code=429,
                headers={"Retry-After": str(retry)},
            )
    return await call_next(request)


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Tarayıcı tarafı güvenlik başlıkları (Faz 23 — Güvenlik sertleştirme /
    XSS-clickjacking sertleştirme). Tam bir Content-Security-Policy şimdilik
    eklenmedi — SPA'nın derlenmiş varlıklarıyla (Vite çıktısı) test edilmeden
    eklenmesi siteyi bozma riski taşır; bu yüzden düşük riskli/evrensel
    başlıklarla sınırlı tutuldu."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    return response


UPLOADS_DIR = Path(config.get("UPLOAD_DIR", str(Path(__file__).resolve().parent.parent / "uploads")))
MODELS_DIR = UPLOADS_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
ATTACH_DIR = UPLOADS_DIR / "attachments"  # Faz 10: madde ekleri (foto/PDF/talimat)
ATTACH_DIR.mkdir(parents=True, exist_ok=True)
PARTS_DIR = UPLOADS_DIR / "parts"  # Parça Bilgi Bankası: kapak görselleri + ekler
PARTS_DIR.mkdir(parents=True, exist_ok=True)
VEHICLES_DIR = UPLOADS_DIR / "vehicles"  # Araç Profil Sayfası: kapak fotoğrafları
VEHICLES_DIR.mkdir(parents=True, exist_ok=True)
NOTES_DIR = UPLOADS_DIR / "notes"  # Bakım notlarına eklenen fotoğraflar
NOTES_DIR.mkdir(parents=True, exist_ok=True)

# Otomatik veritabanı yedeklerinin yazıldığı klasör (ayrı volume —
# db-data bozulsa/silinse bile yedekler korunur).
BACKUP_DIR = Path(config.get("BACKUP_DIR", str(Path(__file__).resolve().parent.parent / "backups")))

# Faz 10: dosya uzantısından ek türünü belirle
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".heic", ".heif"}


def _attachment_kind(ext: str) -> str:
    """Bakım ağacı maddesine yüklenen dosyanın türü. Faz 27 (Bakım Bilgi
    Bankası) ile video ve CAD/teknik çizim de ayrı tür olarak işaretlenir —
    böylece bilgi bankasında video doğrudan oynatıcıda açılabilir, eski
    'file' davranışıyla indirmek zorunda kalınmaz."""
    ext = ext.lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _CAD_EXTS:
        return "cad"
    return "file"


_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"}
# CAD/STEP ve teknik çizim dosyaları (Parça Bilgi Bankası + Araç Belgeleri
# ortak ek sisteminde ayrı bir tür olarak işaretlenir — ör. servis manueli
# yanında STEP/DWG gibi mühendislik dosyaları da yüklenebilsin diye).
_CAD_EXTS = {".step", ".stp", ".iges", ".igs", ".dwg", ".dxf", ".sldprt", ".sldasm", ".catpart", ".catproduct", ".x_t", ".x_b"}


# Bilgi bankasında gömülü oynatıcıda açılabilen video barındırma servisleri.
# (Yalnızca kind'ı 'video' işaretlemek için kullanılır — bağlantı her hâlükârda
# saklanır, tanınmayan adresler 'link' olarak kalmaya devam eder.)
_VIDEO_URL_HINTS = ("youtube.com/", "youtu.be/", "vimeo.com/", "dailymotion.com/")


def _is_video_url(url: str) -> bool:
    u = (url or "").strip().lower()
    if any(h in u for h in _VIDEO_URL_HINTS):
        return True
    return any(u.split("?")[0].endswith(ext) for ext in _VIDEO_EXTS)


def _part_attachment_kind(ext: str) -> str:
    ext = ext.lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext == ".pdf":
        return "pdf"
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _CAD_EXTS:
        return "cad"
    return "file"


# ---------------------------------------------------------------- Girdi doğrulama (Faz 23 — Güvenlik sertleştirme)
# /uploads statik olarak (kimlik doğrulama olmadan) servis edildiği için,
# tarayıcıda DOĞRUDAN AÇILDIĞINDA çalıştırılabilir/render edilebilir
# uzantılar (HTML/script/çalıştırılabilir dosyalar) kesinlikle reddedilir —
# aksi halde biri kötü amaçlı bir .html yükleyip başka bir (oturum açmış)
# kullanıcıya linkini gönderirse, uygulamanın kendi kaynağında (same-origin)
# çalışan bir script sessionStorage'daki oturum jetonunu okuyabilirdi.
_DANGEROUS_EXTS = {
    ".html", ".htm", ".xhtml", ".js", ".mjs", ".jsx", ".ts",
    ".exe", ".bat", ".cmd", ".com", ".scr", ".msi", ".ps1", ".vbs", ".vbe",
    ".jar", ".sh", ".php", ".phtml", ".php3", ".php4", ".php5", ".pl",
    ".dll", ".apk", ".jsp", ".asp", ".aspx", ".cgi", ".wsf", ".reg", ".hta",
}


def _reject_dangerous_ext(ext: str) -> None:
    if ext.lower() in _DANGEROUS_EXTS:
        raise HTTPException(400, "Bu dosya türü güvenlik nedeniyle yüklenemez")


# SVG, tarayıcıda doğrudan açıldığında <script> veya olay (onload= vb.)
# içerebildiği için teorik bir XSS vektörüdür (bkz. yukarıdaki not). Tamamen
# yasaklamak yerine (elektrik şeması gibi meşru kullanımlar için gerekli),
# yüklenen SVG'den bu tehlikeli parçalar kaba bir regex geçişiyle temizlenir.
_SVG_SCRIPT_RE = re.compile(rb"<\s*script\b[^>]*>.*?<\s*/\s*script\s*>", re.IGNORECASE | re.DOTALL)
_SVG_EVENT_ATTR_RE = re.compile(rb"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)


def _sanitize_if_svg(content: bytes, ext: str) -> bytes:
    if ext.lower() != ".svg":
        return content
    content = _SVG_SCRIPT_RE.sub(b"", content)
    content = _SVG_EVENT_ATTR_RE.sub(b"", content)
    return content


# Uzantı kara listesi yalnızca "adı .exe/.php gibi bariz tehlikeli bir
# şeyle bitmesin" diye kontrol eder — dosyanın GERÇEK İÇERİĞİNİN uzantıyla
# uyuştuğunu doğrulamaz. Biri ".jpg" uzantılı ama içeriği tamamen farklı
# (bozuk/uydurma) bir dosya yükleyebilirdi. Aşağıdaki fonksiyon, "resim"
# olarak sınıflandırılan yüklemelerde Pillow ile gerçekten açılabilir bir
# resim olup olmadığını, PDF'lerde ise dosyanın "%PDF-" imzasıyla
# başlayıp başlamadığını kontrol eder. SVG (metin/XML tabanlı olduğu için
# Pillow ile açılamaz) ve diğer türler (CAD/video/ofis belgesi) için ağır
# bağımlılık gerektirmeyeceğinden şimdilik atlanır — uzantı kara listesi +
# (SVG'de) script temizliği onlar için hâlâ geçerli.
def _validate_upload_content(content: bytes, ext: str, kind: str) -> None:
    ext = ext.lower()
    if kind == "image" and ext != ".svg":
        try:
            from PIL import Image

            with Image.open(io.BytesIO(content)) as img:
                img.verify()
        except ImportError:
            # Pillow kurulu değilse (ör. minimal bir kurulum) sessizce atla —
            # bu doğrulama "best effort"tur, dosya yüklemeyi tamamen
            # engelleyecek sert bir bağımlılık haline getirilmemiştir.
            pass
        except Exception:
            raise HTTPException(
                400, "Dosya, uzantısıyla uyuşan geçerli bir resim gibi görünmüyor"
            )
    elif kind == "pdf":
        if not content.startswith(b"%PDF-"):
            raise HTTPException(400, "Dosya geçerli bir PDF gibi görünmüyor")


def fmt_km(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _fmt_qty(q: float) -> str:
    """Parça/malzeme miktarını kompakt biçimde yazar: 1.0 -> '1', 0.20 ->
    '0.2' — stok kesirli olabildiği için (bkz. models.PartCatalogItem.
    stock_qty) log/bildirim metinlerinde '1.0 adet' gibi çirkin gösterim
    yerine kullanılır."""
    if q == int(q):
        return str(int(q))
    return f"{q:.3f}".rstrip("0").rstrip(".")


def _log_opened_maintenances(db, actor, vehicle, opened) -> None:
    """KM güncellemesi/araç ekleme/zaman kontrolü sonucu otomatik açılan
    bakımları audit'e işler (Faz 17 — sistemin kendi otomatik kararları da
    iz bırakır)."""
    for m in opened:
        if m.target_date is not None:
            detail = f"Hedef tarih {m.target_date.strftime('%d.%m.%Y')}"
        else:
            detail = f"Hedef {fmt_km(m.target_km)} km, açılış {fmt_km(m.km_at_open)} km"
        services.log_action(
            db, actor, "create", "maintenance", m.id,
            f"Otomatik bakım açıldı: {vehicle.code} — {m.plan.name}",
            detail=detail,
        )


def _recheck_vehicles_after_tree_import(db: Session, actor, vehicle_models: list[str]) -> int:
    """Bakım ağacı (tekli ya da toplu katalog) içe aktarıldığında/güncellen-
    diğinde, o modeldeki araçların otomatik bakım adaylarını HEMEN yeniden
    değerlendirir — admin ayrıca aracı elle güncellemek ya da km simülasyo-
    nunu beklemek zorunda kalmaz; kimin bakımı gelmiş kiminkinin gelmediği
    içe aktarma bitince anında "Otomatik Bakım / Başlatılmayı Bekleyenler"
    listesinde görünür. `vehicle_models` içinde "" (Genel) varsa TÜM
    araçlar, aksi halde yalnızca o modeldeki araçlar kontrol edilir."""
    q = db.query(models.Vehicle)
    if "" not in vehicle_models:
        q = q.filter(models.Vehicle.model.in_(vehicle_models))
    vehicles = q.all()
    total_opened = 0
    for v in vehicles:
        opened = services.check_and_open_maintenances(db, v)
        opened += services.check_and_open_time_maintenances(db, v)
        if opened:
            _log_opened_maintenances(db, actor, v, opened)
            total_opened += len(opened)
    return total_opened


def tree_root(node: models.TreeNode, db: Session) -> models.TreeNode:
    """Return the root node that defines a template's plan scope."""
    while node.parent_id is not None:
        parent = db.get(models.TreeNode, node.parent_id)
        if parent is None:
            break
        node = parent
    return node


def ensure_node_belongs_to_plan(
    node: models.TreeNode, plan_id: int, db: Session
) -> None:
    if tree_root(node, db).plan_id != plan_id:
        raise HTTPException(400, "Tree node does not belong to the selected plan")


# ================================================================ Auth
@app.post("/api/auth/login", response_model=schemas.Token)
def login(request: Request, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    # Kaba kuvvet (brute-force) koruması — IP başına 5 dakikada en fazla
    # 10 giriş denemesi (Faz 23 — Güvenlik sertleştirme).
    ip = request.client.host if request.client else "anon"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
    if not rate_limit.login_limiter.allow(ip):
        retry = rate_limit.login_limiter.retry_after(ip)
        raise HTTPException(
            429, f"Çok fazla giriş denemesi. {max(1, retry // 60) or 1} dakika sonra tekrar deneyin.",
            headers={"Retry-After": str(retry)},
        )

    # Hesap kilitleme (Faz 24 — Güvenlik sertleştirme 2. tur): yukarıdaki
    # login_limiter IP bazlıdır; bu ise KULLANICI ADI bazlıdır — farklı
    # IP'lerden aynı hesaba yapılan dağıtık kaba kuvvet denemesini de yakalar.
    username_key = form.username.strip().lower()
    locked_for = rate_limit.account_lockout.check(username_key)
    if locked_for:
        raise HTTPException(
            423, f"Çok fazla başarısız deneme nedeniyle bu hesap geçici olarak kilitlendi. "
            f"{max(1, locked_for // 60)} dakika sonra tekrar deneyin.",
        )

    user = db.query(models.User).filter_by(username=form.username).first()
    if not user or not verify_password(form.password, user.password_hash):
        just_locked_for = rate_limit.account_lockout.record_failure(username_key)
        if just_locked_for:
            raise HTTPException(
                423, f"Çok fazla başarısız deneme. Hesap {max(1, just_locked_for // 60)} dakika kilitlendi.",
            )
        raise HTTPException(401, "Kullanıcı adı veya şifre hatalı")
    if not user.is_active:
        raise HTTPException(403, "Hesap pasif durumda veya onay bekliyor")
    rate_limit.account_lockout.record_success(username_key)
    refresh = issue_refresh_token(db, user, ip=ip, user_agent=request.headers.get("user-agent", ""))
    return schemas.Token(access_token=create_token(user), refresh_token=refresh)


@app.post("/api/auth/refresh", response_model=schemas.Token)
def refresh_token(request: Request, body: schemas.RefreshTokenBody, db: Session = Depends(get_db)):
    """Erişim jetonu süresi dolmadan önce (veya doldıktan hemen sonra) ön
    yüzün sessizce çağırdığı uç nokta — geçerli bir yenileme jetonu
    karşılığında yeni bir erişim jetonu VE yeni bir yenileme jetonu
    (rotation — eskisi bu noktada iptal edilir) döner."""
    ip = request.client.host if request.client else "anon"
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        ip = forwarded.split(",")[0].strip()
    result = rotate_refresh_token(db, body.refresh_token, ip=ip, user_agent=request.headers.get("user-agent", ""))
    if result is None:
        raise HTTPException(401, "Oturum süresi doldu, lütfen tekrar giriş yapın")
    new_refresh, user = result
    return schemas.Token(access_token=create_token(user), refresh_token=new_refresh)


@app.post("/api/auth/logout")
def logout(body: schemas.RefreshTokenBody, db: Session = Depends(get_db)):
    """Çıkış yapılırken yenileme jetonunu iptal eder — böylece o cihazdaki
    jeton çalınmış olsa bile artık erişim jetonu yenilenemez."""
    revoke_refresh_token(db, body.refresh_token)
    return {"ok": True}


@app.post("/api/auth/forgot-password", response_model=schemas.ForgotPasswordResult)
def forgot_password(body: schemas.UsernameOnlyBody, db: Session = Depends(get_db)):
    """Giriş sayfasındaki 'Şifremi Unuttum' — kullanıcı adıyla arar, yalnızca
    DOĞRULANMIŞ bir e-postası varsa sıfırlama kodu gönderir. Hesap
    numaralandırmasını önlemek için (kullanıcı var mı, e-postası var mı)
    her durumda aynı genel mesajla döner; gerçek gönderim durumu dışarı
    sızdırılmaz."""
    user = db.query(models.User).filter_by(username=body.username.strip()).first()
    generic = schemas.ForgotPasswordResult(
        message="Bu kullanıcıya ait doğrulanmış bir e-posta varsa, sıfırlama kodu gönderildi."
    )
    if not user or not user.email or not user.email_verified:
        return generic
    code = services.create_verification_code(db, user, "reset_password", user.email, hash_password)
    email_utils.send_verification_code(user.email, code, "reset_password")
    return generic


@app.post("/api/auth/reset-password", response_model=schemas.ForgotPasswordResult)
def reset_password(body: schemas.ResetPasswordBody, db: Session = Depends(get_db)):
    user = db.query(models.User).filter_by(username=body.username.strip()).first()
    if not user:
        raise HTTPException(400, "Kullanıcı bulunamadı")
    ok, err = services.verify_verification_code(db, user, "reset_password", body.code, verify_password)
    if not ok:
        raise HTTPException(400, err)
    pw_error = services.validate_password_strength(body.new_password)
    if pw_error:
        raise HTTPException(400, pw_error)
    user.password_hash = hash_password(body.new_password)
    db.commit()
    services.log_action(
        db, user, "update", "user", user.id,
        f"Şifre e-posta koduyla sıfırlandı: {user.full_name}",
    )
    return schemas.ForgotPasswordResult(message="Şifreniz güncellendi. Şimdi giriş yapabilirsiniz.")


@app.post("/api/auth/register", response_model=schemas.RegisterResult)
def register(body: schemas.RegisterBody, db: Session = Depends(get_db)):
    """Herkese açık kayıt uç noktası. Yeni kullanıcı 'usta' rolüyle ve
    pasif (onay bekliyor) durumda oluşturulur; admin onaylayana kadar
    giriş yapamaz."""
    username = body.username.strip()
    full_name = body.full_name.strip()
    if len(username) < 3:
        raise HTTPException(400, "Kullanıcı adı en az 3 karakter olmalı")
    if not full_name:
        raise HTTPException(400, "Ad Soyad gerekli")
    pw_error = services.validate_password_strength(body.password)
    if pw_error:
        raise HTTPException(400, pw_error)
    if db.query(models.User).filter_by(username=username).first():
        raise HTTPException(400, "Bu kullanıcı adı zaten alınmış")
    email = body.email.strip()
    if not email:
        raise HTTPException(400, "E-posta (Gmail) adresi gerekli")
    if not services.is_valid_email(email):
        raise HTTPException(400, "Geçersiz e-posta adresi")
    if db.query(models.User).filter_by(email=email).first():
        raise HTTPException(400, "Bu e-posta adresi zaten kayıtlı")
    user = models.User(
        username=username,
        full_name=full_name,
        role="technician",
        password_hash=hash_password(body.password),
        is_active=False,
        email=email,
        email_verified=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    code = services.create_verification_code(db, user, "verify_email", email, hash_password)
    email_sent = email_utils.send_verification_code(email, code, "verify_email")

    message = "Kaydınız alındı. Hesabınız yönetici onayından sonra aktif olacaktır."
    message += (
        " E-posta adresinizi doğrulamak için gönderilen kodu girin."
        if email_sent
        else " E-posta gönderilemedi; e-postanızı daha sonra profil sayfanızdan doğrulayabilirsiniz."
    )
    return schemas.RegisterResult(message=message, email_sent=email_sent)


@app.post("/api/auth/register/resend-code", response_model=schemas.RegisterResult)
def register_resend_code(body: schemas.UsernameOnlyBody, db: Session = Depends(get_db)):
    """Kayıt sırasında girilen e-postaya doğrulama kodunu yeniden gönderir.
    Henüz giriş yapılamadığı için (hesap admin onayı bekliyor olabilir)
    kullanıcı adıyla tanımlanır."""
    user = db.query(models.User).filter_by(username=body.username.strip()).first()
    if not user or not user.email:
        # Kullanıcı numaralandırmasını (var mı yok mu) sızdırmamak için
        # genel bir mesajla dönülür.
        return schemas.RegisterResult(message="Bu kullanıcı için kayıtlı bir e-posta yoksa kod gönderilemez.")
    if user.email_verified:
        return schemas.RegisterResult(message="E-posta zaten doğrulanmış.", email_sent=False)
    code = services.create_verification_code(db, user, "verify_email", user.email, hash_password)
    sent = email_utils.send_verification_code(user.email, code, "verify_email")
    return schemas.RegisterResult(
        message="Kod gönderildi." if sent else "E-posta gönderilemedi, daha sonra tekrar deneyin.",
        email_sent=sent,
    )


@app.post("/api/auth/register/verify-email", response_model=schemas.RegisterResult)
def register_verify_email(body: schemas.RegisterVerifyBody, db: Session = Depends(get_db)):
    """Kayıt sırasında verilen e-postayı, henüz giriş yapmadan (kullanıcı
    adı + kod ile) doğrular."""
    user = db.query(models.User).filter_by(username=body.username.strip()).first()
    if not user:
        raise HTTPException(400, "Kullanıcı bulunamadı")
    ok, err = services.verify_verification_code(db, user, "verify_email", body.code, verify_password)
    if not ok:
        raise HTTPException(400, err)
    user.email_verified = True
    db.commit()
    return schemas.RegisterResult(message="E-posta doğrulandı.")


@app.get("/api/auth/me", response_model=schemas.UserOut)
def me(user: models.User = Depends(get_current_user)):
    return user


@app.post("/api/auth/change-password")
def change_password(
    body: schemas.ChangePasswordBody,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Herkes kendi şifresini değiştirebilir (herhangi bir izin gerektirmez —
    mevcut şifrenin doğru girilmesi yeterli güvenlik kontrolüdür)."""
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Mevcut şifre yanlış")
    pw_error = services.validate_password_strength(body.new_password)
    if pw_error:
        raise HTTPException(400, pw_error)
    user.password_hash = hash_password(body.new_password)
    db.commit()
    services.log_action(
        db, user, "update", "user", user.id,
        f"Kendi şifresini değiştirdi: {user.full_name}",
    )
    return {"ok": True}


@app.post("/api/auth/email/send-code", response_model=schemas.RegisterResult)
def profile_email_send_code(
    body: schemas.EmailSendCodeBody,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Profil sayfasından e-posta ekleme/değiştirme — sisteme daha önce
    e-posta girmeden kayıt olmuş kullanıcılar da buradan ekleyebilir.
    Adres hemen kaydedilir (doğrulanmamış olarak); kod onaylanana kadar
    email_verified False kalır."""
    email = body.email.strip()
    if not services.is_valid_email(email):
        raise HTTPException(400, "Geçersiz e-posta adresi")
    if db.query(models.User).filter(models.User.email == email, models.User.id != user.id).first():
        raise HTTPException(400, "Bu e-posta adresi başka bir hesapta kayıtlı")
    user.email = email
    user.email_verified = False
    db.commit()
    code = services.create_verification_code(db, user, "verify_email", email, hash_password)
    sent = email_utils.send_verification_code(email, code, "verify_email")
    return schemas.RegisterResult(
        message="Doğrulama kodu gönderildi." if sent else "E-posta gönderilemedi (SMTP yapılandırılmamış olabilir); sistem yöneticinize danışın.",
        email_sent=sent,
    )


@app.post("/api/auth/email/verify", response_model=schemas.UserOut)
def profile_email_verify(
    body: schemas.EmailVerifyBody,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    ok, err = services.verify_verification_code(db, user, "verify_email", body.code, verify_password)
    if not ok:
        raise HTTPException(400, err)
    user.email_verified = True
    db.commit()
    db.refresh(user)
    services.log_action(db, user, "update", "user", user.id, f"E-posta doğrulandı: {user.email}")
    return user


# ================================================================ Users (manage_users izni)
@app.get("/api/users", response_model=list[schemas.UserOut])
def list_users(db: Session = Depends(get_db), _=Depends(require_permission("manage_users"))):
    return db.query(models.User).all()


@app.post("/api/users", response_model=schemas.UserOut)
def create_user(
    body: schemas.UserCreate,
    db: Session = Depends(get_db),
    actor=Depends(require_permission("manage_users")),
):
    if db.query(models.User).filter_by(username=body.username).first():
        raise HTTPException(400, "Bu kullanıcı adı zaten var")
    if body.role not in ("admin", "engineer", "technician", "amir"):
        raise HTTPException(400, "Geçersiz rol")
    if body.role == "admin" and actor.role != "admin":
        raise HTTPException(403, "Yalnızca admin, admin kullanıcı oluşturabilir")
    pw_error = services.validate_password_strength(body.password)
    if pw_error:
        raise HTTPException(400, pw_error)
    user = models.User(
        username=body.username,
        full_name=body.full_name,
        role=body.role,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    services.log_action(
        db, actor, "create", "user", user.id,
        f"Kullanıcı oluşturuldu: {user.full_name} ({user.username}, {user.role})",
    )
    return user


@app.patch("/api/users/{user_id}", response_model=schemas.UserOut)
def update_user(
    user_id: int,
    body: schemas.UserUpdate,
    db: Session = Depends(get_db),
    actor=Depends(require_permission("manage_users")),
):
    user = db.get(models.User, user_id)
    if not user:
        raise HTTPException(404, "Kullanıcı bulunamadı")
    if body.role == "admin" and actor.role != "admin":
        raise HTTPException(403, "Yalnızca admin, admin rolü atayabilir")
    changes: list[str] = []
    old_role = user.role
    if body.full_name is not None and body.full_name != user.full_name:
        changes.append(f"ad: {user.full_name} → {body.full_name}")
        user.full_name = body.full_name
    if body.role is not None and body.role != user.role:
        changes.append(f"rol: {user.role} → {body.role}")
        user.role = body.role
    if body.is_active is not None and body.is_active != user.is_active:
        changes.append("aktifleştirildi" if body.is_active else "pasifleştirildi")
        user.is_active = body.is_active
    if body.password:
        pw_error = services.validate_password_strength(body.password)
        if pw_error:
            raise HTTPException(400, pw_error)
        changes.append("şifre değiştirildi")
        user.password_hash = hash_password(body.password)
    if "shift_id" in body.model_fields_set and body.shift_id != user.shift_id:
        if body.shift_id is not None and not db.get(models.Shift, body.shift_id):
            raise HTTPException(400, "Geçersiz vardiya")
        changes.append("vardiya kaldırıldı" if body.shift_id is None else f"vardiya: {body.shift_id}")
        # Kişi eski vardiyasının SORUMLUSUYSA, vardiyadan ayrılınca sorumluluk
        # da düşer — aksi halde artık üyesi olmadığı bir vardiyada başkaları
        # adına tikleme yetkisi taşımaya devam ederdi.
        old_shift = db.get(models.Shift, user.shift_id) if user.shift_id else None
        if old_shift is not None and old_shift.supervisor_id == user.id:
            old_shift.supervisor_id = None
            changes.append(f"{old_shift.name} vardiya sorumluluğu kaldırıldı")
        user.shift_id = body.shift_id
    db.commit()
    db.refresh(user)
    services.log_action(
        db, actor, "update", "user", user.id,
        f"Kullanıcı güncellendi: {user.full_name}",
        detail="; ".join(changes),
        old_value=old_role if old_role != user.role else "",
        new_value=user.role if old_role != user.role else "",
    )
    return user


@app.delete("/api/users/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    actor=Depends(require_permission("manage_users")),
):
    """Pasife almaktan farklı olarak hesabı kalıcı olarak siler. Geçmiş
    kayıtlardaki ('kim tamamladı', 'kim not ekledi' gibi) bağlantılar
    NULL'a çekilir — metinler (madde adı, denetim özeti) zaten ayrıca
    saklandığı için geçmiş kaybolmaz (bkz. services.delete_user_permanently)."""
    target = db.get(models.User, user_id)
    if not target:
        raise HTTPException(404, "Kullanıcı bulunamadı")
    if target.id == actor.id:
        raise HTTPException(400, "Kendi hesabınızı silemezsiniz")
    if target.role == "admin":
        other_admins = (
            db.query(models.User)
            .filter(
                models.User.role == "admin",
                models.User.id != target.id,
                models.User.is_active == True,  # noqa: E712
            )
            .count()
        )
        if other_admins == 0:
            raise HTTPException(400, "Sistemde en az bir aktif admin hesabı kalmalı")
    summary = f"Kullanıcı silindi: {target.full_name} ({target.username}, {target.role})"
    services.delete_user_permanently(db, target)
    services.log_action(db, actor, "delete", "user", user_id, summary)
    return {"ok": True}


@app.get("/api/vehicle-models", response_model=list[str])
def list_vehicle_models(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Şu ana kadar kullanılmış tüm araç modeli adlarının birleşimi
    (hem Vehicle.model'den hem MaintenancePlan.vehicle_model'den) — Bakım
    Ağacı sayfasındaki model seçici ve araç ekleme formundaki öneriler
    için kullanılır. Boş/"genel" değerler hariç tutulur."""
    from_vehicles = {
        m for (m,) in db.query(models.Vehicle.model).distinct().all() if m and m.strip()
    }
    from_plans = {
        m for (m,) in db.query(models.MaintenancePlan.vehicle_model).distinct().all() if m and m.strip()
    }
    return sorted(from_vehicles | from_plans)


@app.post("/api/vehicle-models/merge", response_model=schemas.VehicleModelMergeResult)
def merge_vehicle_models(
    body: schemas.VehicleModelMergeIn,
    db: Session = Depends(get_db),
    actor=Depends(require_admin),
):
    """Bir araç modelini bir diğerine birleştirir — yanlış yazılmış/eski bir
    model adının (ör. Excel'den 'BOZANKAYA' olarak gelmiş, filodaki gerçek
    modelle 'Bozankaya Tip-1' birebir eşleşmeyen kayıtlar) gerçek filo
    modeliyle tek elde toplanmasını sağlar. Bu modeldeki TÜM araçlar ve
    planlar (varsa aynı periyotta çakışan planların tamamlanmış geçmişi
    korunarak) hedef modele taşınır — bkz. excel_utils.merge_vehicle_model
    için tam kural seti."""
    from_model = body.from_model.strip()
    to_model = body.to_model.strip()
    result = excel_utils.merge_vehicle_model(db, from_model, to_model, commit=False)
    db.commit()
    services.log_action(
        db, actor, "update", "vehicle", None,
        f"Araç modeli birleştirildi: {from_model!r} -> {to_model!r} "
        f"({result['vehicles_moved']} araç, {len(result['plans_renamed'])} plan yeniden adlandırıldı, "
        f"{len(result['plans_merged'])} plan birleştirildi)",
        detail="\n".join(
            [f"Yeniden adlandırıldı: {p['name']} ({p['interval_km']} km)" for p in result["plans_renamed"]]
            + [
                f"Birleştirildi: {p['name']} ({p['interval_km']} km) -> plan #{p['merged_into_plan_id']}, "
                f"{p['action']}, {p['moved_completed']} tamamlanmış kayıt taşındı"
                for p in result["plans_merged"]
            ]
        ),
    )
    return schemas.VehicleModelMergeResult(**result)


# Dashboard'daki "Zamanı Gelen ve Yaklaşan Bakımlar" tablosuna gönderilen en
# fazla satır sayısı (hem açık iş emirleri hem yaklaşanlar için ayrı ayrı).
# Arayüz bunun ilk 10'unu gösterir, gerisini "Daha fazlasını gör" ile açar
# (bkz. DashboardPage.tsx → DUE_TABLE_LIMIT).
DASHBOARD_DUE_MAX = 50


def _compute_upcoming_merged(
    db: Session,
    vehicles: list[models.Vehicle] | None = None,
    plans: list[models.MaintenancePlan] | None = None,
    limit: int | None = None,
    include_open: bool = False,
) -> list[dict]:
    """KM bazlı (compute_upcoming_maintenances) ve süre bazlı
    (compute_upcoming_time_maintenances) "yaklaşan bakım" sonuçlarını
    birleştirir. İki tür farklı birimlerde (km / takvim günü) çalıştığı
    için ortak, karşılaştırılabilir sıralama anahtarı `estimated_days`'tir
    — km tarafı zaten günlük ortalama km varsayımıyla gün tahmini üretiyor,
    süre tarafı ise doğrudan takvim farkını kullanıyor (bkz. services.py)."""
    merged = services.compute_upcoming_maintenances(db, vehicles, plans, include_open=include_open)
    merged += services.compute_upcoming_time_maintenances(db, vehicles, plans, include_open=include_open)
    merged.sort(key=lambda u: u["estimated_days"] if u["estimated_days"] is not None else 0)
    if limit is not None:
        merged = merged[:limit]
    return merged


# ================================================================ Vehicles
@app.get("/api/vehicles", response_model=list[schemas.VehicleOut])
def list_vehicles(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return db.query(models.Vehicle).order_by(models.Vehicle.code).all()


@app.get("/api/vehicles/alerts", response_model=list[schemas.UpcomingItem])
def vehicle_alerts(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Tüm araçlar için -%5/+%10 önizleme penceresindeki yaklaşan bakımlar.
    Araçlar sayfasında ilgili satırı kırmızı vurgulamak için kullanılır;
    Dashboard'daki ilk-8-ile-sınırlı listenin aksine TÜM araçları kapsar.
    include_open=True: iş emri açılmış ama henüz tamamlanmamış bakımlar da
    uyarı olarak görünmeye devam eder."""
    return [
        schemas.UpcomingItem(**item)
        for item in _compute_upcoming_merged(db, include_open=True)
    ]


@app.get("/api/vehicles/alerts/export")
def export_vehicle_alerts(
    plan_name: str | None = None,
    vehicle_model: str | None = None,
    started: bool | None = None,
    shift_id: int | None = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Bakımlar sayfasındaki 'Bakım Akışı' listesini, sayfada o an seçili
    olan periyot/araç tipi/sekme (Başlatılanlar=started=true,
    Başlatılmayı Bekleyenler=started=false)/vardiya filtreleriyle BİREBİR
    aynı şekilde, durum kategorisine göre çok sayfalı Excel olarak indirir."""
    if not services.has_permission(db, user, "export_excel"):
        raise HTTPException(403, "Dışa aktarma yetkiniz yok")
    items = _compute_upcoming_merged(db, include_open=True)
    if plan_name:
        items = [it for it in items if it["plan_name"] == plan_name]
    if vehicle_model:
        items = [it for it in items if it["vehicle"].model == vehicle_model]
    if started is not None:
        items = [it for it in items if (it["maintenance_id"] is not None) == started]
    if shift_id is not None:
        items = [it for it in items if it.get("assigned_shift_id") == shift_id]
    content = export_utils.build_flow_export(items, title="Bakım Akışı")
    services.log_action(
        db, user, "export", "maintenance", None,
        f"Bakım Akışı Excel'e aktarıldı ({len(items)} kayıt)",
    )
    return Response(
        content=content,
        media_type=export_utils.MEDIA_TYPES["xlsx"],
        headers={"Content-Disposition": "attachment; filename=bakim_akisi.xlsx"},
    )


@app.get("/api/vehicles/{vehicle_id}/profile", response_model=schemas.VehicleProfile)
def vehicle_profile(
    vehicle_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)
):
    """Araç Profil Sayfası — bir aracın tüm bakım özetini tek yerde toplar:
    toplam/tamamlanan/açık bakım sayıları, son tamamlanan bakım, yaklaşan
    sonraki bakım, en son eklenen bakım notu (son arıza/uyarı) ve son
    bakımların listesi."""
    v = db.get(models.Vehicle, vehicle_id)
    if not v:
        raise HTTPException(404, "Araç bulunamadı")

    all_m = db.query(models.Maintenance).filter_by(vehicle_id=vehicle_id).all()
    completed = [m for m in all_m if m.status == "completed"]
    open_m = [m for m in all_m if m.status in ("pending", "in_progress")]
    pending = [m for m in open_m if m.status == "pending"]
    in_progress = [m for m in open_m if m.status == "in_progress"]

    last_completed = max(
        completed, key=lambda m: m.completed_at or datetime.min, default=None
    )
    recent = sorted(all_m, key=lambda m: m.created_at, reverse=True)[:8]

    plans = db.query(models.MaintenancePlan).filter_by(is_active=True).all()
    ups = _compute_upcoming_merged(db, [v], plans, include_open=True)
    next_upcoming = schemas.UpcomingItem(**ups[0]) if ups else None

    last_note = (
        db.query(models.MaintenanceNote)
        .join(models.Maintenance, models.MaintenanceNote.maintenance_id == models.Maintenance.id)
        .filter(models.Maintenance.vehicle_id == vehicle_id)
        .order_by(models.MaintenanceNote.created_at.desc())
        .first()
    )

    return schemas.VehicleProfile(
        vehicle=v,
        total_maintenances=len(all_m),
        completed_count=len(completed),
        open_count=len(open_m),
        pending_count=len(pending),
        in_progress_count=len(in_progress),
        last_completed=last_completed,
        next_upcoming=next_upcoming,
        last_note=last_note,
        attachments=v.attachments,
        recent=recent,
    )


@app.get("/api/vehicles/{vehicle_id}/qrcode")
def vehicle_qrcode(
    vehicle_id: int, request: Request, download: bool = False, db: Session = Depends(get_db)
):
    """Aracın QR kodu (Faz 22) — telefonla okutulunca uygulama doğrudan bu
    aracın profil/bakım geçmişi sayfasını açar. QR, isteği yapan tarayıcının
    o an kullandığı adresle (localhost/LAN IP fark etmeksizin) üretilir.
    Bir görsel olduğu için /uploads ile tutarlı şekilde girişsiz servis
    edilir (yalnızca dahili bir deep-link URL'si kodlar, veri döndürmez)."""
    v = db.get(models.Vehicle, vehicle_id)
    if not v:
        raise HTTPException(404, "Araç bulunamadı")
    url = f"{str(request.base_url).rstrip('/')}/?vehicle_id={v.id}"
    png = qr_utils.build_qr_png(url)
    headers = {"Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = f"attachment; filename=qr_{v.code}.png"
    return Response(content=png, media_type="image/png", headers=headers)


@app.post("/api/vehicles/{vehicle_id}/attachments", response_model=schemas.VehicleAttachmentOut)
async def upload_vehicle_attachment(
    vehicle_id: int,
    label: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    actor=Depends(require_staff),
):
    """Araca belge yükler: PDF, servis manueli, fotoğraf, CAD/STEP mühendislik
    dosyası, elektrik şeması vb. — türü uzantıdan otomatik belirlenir."""
    v = db.get(models.Vehicle, vehicle_id)
    if not v:
        raise HTTPException(404, "Araç bulunamadı")
    original = file.filename or "dosya"
    ext = os.path.splitext(original)[1].lower()
    content = await file.read()
    content, ext = await run_in_threadpool(bg_remove.convert_to_browser_image, content, ext)
    _reject_dangerous_ext(ext)
    kind = _part_attachment_kind(ext)
    _validate_upload_content(content, ext, kind)
    content = _sanitize_if_svg(content, ext)
    fname = f"{uuid.uuid4().hex}{ext}"
    (VEHICLES_DIR / fname).write_bytes(content)
    att = models.VehicleAttachment(
        vehicle_id=vehicle_id,
        kind=kind,
        label=label,
        filename=fname,
        original_name=original,
    )
    db.add(att)
    db.commit()
    db.refresh(att)
    services.log_action(
        db, actor, "create", "vehicle_attachment", att.id,
        f"Araca belge yüklendi: {v.code}", detail=f"{att.kind}: {original}",
    )
    return att


@app.post("/api/vehicles/{vehicle_id}/attachment-link", response_model=schemas.VehicleAttachmentOut)
def add_vehicle_attachment_link(
    vehicle_id: int,
    body: schemas.AttachmentLinkBody,
    db: Session = Depends(get_db),
    actor=Depends(require_staff),
):
    """Araca harici bir bağlantı ekler (ör. bulut üzerindeki bir doküman)."""
    v = db.get(models.Vehicle, vehicle_id)
    if not v:
        raise HTTPException(404, "Araç bulunamadı")
    att = models.VehicleAttachment(vehicle_id=vehicle_id, kind="link", label=body.label, url=body.url)
    db.add(att)
    db.commit()
    db.refresh(att)
    services.log_action(
        db, actor, "create", "vehicle_attachment", att.id,
        f"Araca bağlantı eklendi: {v.code}", detail=body.url,
    )
    return att


@app.delete("/api/vehicle-attachments/{attachment_id}")
def delete_vehicle_attachment(
    attachment_id: int, db: Session = Depends(get_db), actor=Depends(require_staff)
):
    att = db.get(models.VehicleAttachment, attachment_id)
    if not att:
        raise HTTPException(404, "Ek bulunamadı")
    vehicle_id = att.vehicle_id
    fpath = VEHICLES_DIR / att.filename if att.filename else None
    label = att.label or att.original_name or att.url
    db.delete(att)
    db.commit()
    if fpath and fpath.is_file():
        try:
            fpath.unlink()
        except OSError:
            pass
    services.log_action(db, actor, "delete", "vehicle_attachment", vehicle_id, f"Araç eki silindi: {label}")
    return {"ok": True}


@app.post("/api/vehicles/{vehicle_id}/photo", response_model=schemas.VehicleOut)
async def upload_vehicle_photo(
    vehicle_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    actor=Depends(require_staff),
):
    """Araç profil kapak fotoğrafını yükler/değiştirir."""
    v = db.get(models.Vehicle, vehicle_id)
    if not v:
        raise HTTPException(404, "Araç bulunamadı")
    original = file.filename or "foto"
    ext = os.path.splitext(original)[1].lower()
    content = await file.read()
    content, ext = await run_in_threadpool(bg_remove.convert_to_browser_image, content, ext)
    if ext not in _IMAGE_EXTS:
        raise HTTPException(400, "Yalnızca resim dosyası yüklenebilir")
    _validate_upload_content(content, ext, "image")
    content = _sanitize_if_svg(content, ext)
    old_fname = v.photo_filename
    fname = f"{uuid.uuid4().hex}{ext}"
    (VEHICLES_DIR / fname).write_bytes(content)
    v.photo_filename = fname
    db.commit()
    db.refresh(v)
    if old_fname:
        old_path = VEHICLES_DIR / old_fname
        if old_path.is_file():
            try:
                old_path.unlink()
            except OSError:
                pass
    services.log_action(db, actor, "update", "vehicle", v.id, f"Araç fotoğrafı güncellendi: {v.code}")
    return v


@app.delete("/api/vehicles/{vehicle_id}/photo", response_model=schemas.VehicleOut)
def delete_vehicle_photo(
    vehicle_id: int, db: Session = Depends(get_db), actor=Depends(require_staff)
):
    """Araç profil fotoğrafını kaldırır."""
    v = db.get(models.Vehicle, vehicle_id)
    if not v:
        raise HTTPException(404, "Araç bulunamadı")
    old_fname = v.photo_filename
    v.photo_filename = ""
    db.commit()
    db.refresh(v)
    if old_fname:
        old_path = VEHICLES_DIR / old_fname
        if old_path.is_file():
            try:
                old_path.unlink()
            except OSError:
                pass
    services.log_action(db, actor, "update", "vehicle", v.id, f"Araç fotoğrafı kaldırıldı: {v.code}")
    return v


@app.post("/api/vehicles", response_model=schemas.VehicleOut)
def create_vehicle(
    body: schemas.VehicleCreate, db: Session = Depends(get_db), actor=Depends(require_staff)
):
    if db.query(models.Vehicle).filter_by(code=body.code).first():
        raise HTTPException(400, "Bu araç kodu zaten var")
    v = models.Vehicle(**body.model_dump())
    if v.current_km:
        v.last_km_update_at = datetime.now()
    # Başlangıç (baseline) km: aracın sisteme girildiği andaki km. Bunun
    # ALTINDA kalan bakımlar (sistemden önce yapılmış sayılır) asla açılmaz;
    # yalnızca bunun ÜSTÜNE gelen yeni bakımlar araç yaklaştıkça açılır.
    v.baseline_km = v.current_km
    # Süre bazlı (aylık/yıllık) bakımların "kaç ay geçti" hesabı için bir
    # referans tarihe her zaman ihtiyaç var — admin girmediyse aracın
    # sisteme eklendiği gün varsayılan olarak kullanılır.
    if v.commissioning_date is None:
        v.commissioning_date = date.today()
    db.add(v)
    db.commit()
    db.refresh(v)
    services.log_action(
        db, actor, "create", "vehicle", v.id,
        f"Araç eklendi: {v.code} ({fmt_km(v.current_km)} km)",
    )
    # Yeni eklenen araç için, girildiği km'nin zaten geçmiş olduğu bakım
    # hedefleri (ör. 250.000 km'de girilen araç için 250.000 km bakımı)
    # otomatik açılmaz — yalnızca aracın henüz ulaşmadığı bir sonraki
    # hedef (ör. 275.000 km) toleransa girince açılır. Süre bazlı bakımlar
    # için de aynı mantık (skip_passed_targets) uygulanır.
    opened = services.check_and_open_maintenances(db, v, skip_passed_targets=True)
    opened += services.check_and_open_time_maintenances(db, v, skip_passed_targets=True)
    _log_opened_maintenances(db, actor, v, opened)
    return v


@app.patch("/api/vehicles/{vehicle_id}", response_model=schemas.VehicleOut)
def update_vehicle(
    vehicle_id: int,
    body: schemas.VehicleUpdate,
    db: Session = Depends(get_db),
    actor=Depends(require_staff),
):
    v = db.get(models.Vehicle, vehicle_id)
    if not v:
        raise HTTPException(404, "Araç bulunamadı")
    commissioning_changed = "commissioning_date" in body.model_fields_set
    for k, val in body.model_dump(exclude_none=True).items():
        setattr(v, k, val)
    db.commit()
    db.refresh(v)
    if commissioning_changed:
        # Hizmete başlama tarihi (yeni) girildiyse/değiştiyse, sıradaki
        # otomatik kontrolü beklemeden süre bazlı bakımları hemen değerlendir.
        opened = services.check_and_open_time_maintenances(db, v, skip_passed_targets=True)
        _log_opened_maintenances(db, actor, v, opened)
    services.log_action(db, actor, "update", "vehicle", v.id, f"Araç güncellendi: {v.code}")
    return v


@app.put("/api/vehicles/{vehicle_id}/km", response_model=schemas.VehicleOut)
def update_km(
    vehicle_id: int,
    body: schemas.VehicleKmUpdate,
    db: Session = Depends(get_db),
    actor=Depends(require_staff),
):
    """KM güncellendiğinde sistem bakım aralığını otomatik kontrol eder."""
    v = db.get(models.Vehicle, vehicle_id)
    if not v:
        raise HTTPException(404, "Araç bulunamadı")
    if body.current_km < v.current_km:
        raise HTTPException(400, "Kilometre geriye alınamaz")
    old_km = v.current_km
    v.current_km = body.current_km
    v.last_km_update_at = datetime.now()
    db.commit()
    services.log_action(
        db, actor, "update", "vehicle", v.id,
        f"KM güncellendi: {v.code}",
        detail=f"{fmt_km(old_km)} → {fmt_km(v.current_km)} km",
        old_value=str(old_km), new_value=str(v.current_km),
    )
    opened = services.check_and_open_maintenances(db, v)
    _log_opened_maintenances(db, actor, v, opened)
    db.refresh(v)
    return v


@app.delete("/api/vehicles/{vehicle_id}")
def delete_vehicle(
    vehicle_id: int, db: Session = Depends(get_db), actor=Depends(require_admin)
):
    """Aracı ve ona bağlı tüm bakım kayıtlarını (iş emri + alt maddeleri)
    kalıcı olarak siler. Silinen araç hiçbir listede (Araçlar, Bakımlar)
    artık görünmez."""
    v = db.get(models.Vehicle, vehicle_id)
    if not v:
        raise HTTPException(404, "Araç bulunamadı")
    code = v.code
    photo_fname = v.photo_filename
    att_filenames = [a.filename for a in v.attachments if a.filename]
    maintenances = db.query(models.Maintenance).filter_by(vehicle_id=vehicle_id).all()
    for m in maintenances:
        db.delete(m)
    db.delete(v)
    db.commit()
    for fname in ([photo_fname] if photo_fname else []) + att_filenames:
        fpath = VEHICLES_DIR / fname
        if fpath.is_file():
            try:
                fpath.unlink()
            except OSError:
                pass
    services.log_action(
        db, actor, "delete", "vehicle", vehicle_id,
        f"Araç silindi: {code}",
        detail=f"{len(maintenances)} bakım kaydı da silindi",
    )
    return {"ok": True, "deleted_maintenances": len(maintenances)}


@app.post("/api/vehicles/import", response_model=schemas.ImportResult)
async def import_vehicles(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    actor=Depends(require_staff),
):
    """Excel dosyasından araç km ve bilgilerini içe aktarır.

    Başlık satırı esnek eşlenir (Kod/Araç Kodu/Araç No, Ad, KM/Kilometre,
    Üretici, Model, Şasi No, Not). Var olan araç kodları güncellenir,
    yeni kodlar oluşturulur. KM güncellemesi otomatik bakım kontrolünü
    tetikler.
    """
    content = await file.read()
    try:
        rows = excel_utils.parse_vehicles_workbook(content)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not rows:
        raise HTTPException(400, "Dosyada işlenecek satır bulunamadı")

    created = 0
    updated = 0
    errors: list[str] = []
    touched_ids: list[int] = []
    new_vehicle_ids: set[int] = set()

    for row in rows:
        code = row["code"]
        v = db.query(models.Vehicle).filter_by(code=code).first()
        is_new = v is None
        if is_new:
            v = models.Vehicle(code=code)
            db.add(v)
            created += 1
        else:
            updated += 1

        if row["name"]:
            v.name = row["name"]
        if row["manufacturer"] is not None:
            v.manufacturer = row["manufacturer"]
        if row["model"] is not None:
            v.model = row["model"]
        if row["chassis_no"] is not None:
            v.chassis_no = row["chassis_no"]
        if row["notes"] is not None:
            v.notes = row["notes"]

        if row["current_km"] is not None:
            # Excel'e sadık kalınır: değer küçük de büyük de olsa doğrudan
            # uygulanır, atlanmaz (Excel şirketin güncel/gerçek km kaynağıdır).
            v.current_km = row["current_km"]
            v.last_km_update_at = datetime.now()
        # Yeni araç: girildiği (ilk yüklenen) km başlangıç/baseline kabul edilir
        # — bunun ALTINDA kalan bakımlar asla açılmaz, yalnızca üstüne gelen
        # yeni bakımlar araç yaklaştıkça açılır (bkz. check_and_open_maintenances).
        # Mevcut araçların baseline'ı korunur (yeniden yazılmaz).
        if is_new and v.baseline_km is None:
            v.baseline_km = v.current_km
        db.flush()
        touched_ids.append(v.id)
        if is_new:
            new_vehicle_ids.add(v.id)

    db.commit()

    for vid in touched_ids:
        v = db.get(models.Vehicle, vid)
        if v:
            # Yeni satırla eklenen araçlar için de, girildiği km'nin zaten
            # geçmiş olduğu hedefler otomatik açılmaz (bkz. create_vehicle).
            # Var olan bir aracın km'si güncellenmişse bu kısıtlama uygulanmaz.
            opened = services.check_and_open_maintenances(
                db, v, skip_passed_targets=vid in new_vehicle_ids
            )
            _log_opened_maintenances(db, actor, v, opened)

    services.log_action(
        db, actor, "import", "vehicle", None,
        f"Araçlar Excel'den içe aktarıldı: {created} yeni, {updated} güncelleme",
        detail=("; ".join(errors) if errors else ""),
    )
    return schemas.ImportResult(created=created, updated=updated, errors=errors)


@app.get("/api/vehicles/export")
def export_vehicles(
    db: Session = Depends(get_db),
    actor=Depends(require_permission("export_excel")),
):
    """Tüm araçları Excel'e aktarır."""
    vehicles = db.query(models.Vehicle).order_by(models.Vehicle.code).all()
    content = excel_utils.build_vehicles_workbook(vehicles)
    services.log_action(
        db, actor, "export", "vehicle", None,
        f"Araç listesi Excel'e aktarıldı ({len(vehicles)} araç)",
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=araclar.xlsx"},
    )


# ================================================================ Plans
@app.get("/api/plans", response_model=list[schemas.PlanOut])
def list_plans(
    include_catalog_only: bool = False,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    """Varsayılan olarak katalog-amaçlı (is_catalog_only=True — bir temel
    periyodun katı, ör. 800.000 km) planları DÖNDÜRMEZ; Bakım Planları
    sayfası bu şekilde her zaman yalnızca gerçekten bakım tetikleyen
    planları listeler. Bakım Ağacı sayfası (periyot seçici, katalog içeriği
    kaybolmasın diye) `include_catalog_only=true` ile TÜMÜNÜ ister."""
    q = db.query(models.MaintenancePlan)
    if not include_catalog_only:
        q = q.filter(models.MaintenancePlan.is_catalog_only == False)  # noqa: E712
    return q.order_by(models.MaintenancePlan.interval_km).all()


@app.post("/api/plans", response_model=schemas.PlanOut)
def create_plan(
    body: schemas.PlanCreate, db: Session = Depends(get_db), actor=Depends(require_admin)
):
    data = body.model_dump()
    if not data.get("name"):
        data["name"] = services.default_plan_name(
            data["plan_type"], data["interval_km"], data.get("interval_months"), data.get("vehicle_model")
        )
    p = models.MaintenancePlan(**data)
    db.add(p)
    db.commit()
    db.refresh(p)
    period_txt = (
        f"{p.interval_months} ay" if p.plan_type == "time" else f"{fmt_km(p.interval_km)} km"
    )
    services.log_action(
        db, actor, "create", "plan", p.id,
        f"Bakım planı oluşturuldu: {p.name} ({period_txt})",
    )
    return p


@app.post("/api/plans/normalize-sirio-names")
def normalize_sirio_plan_names(db: Session = Depends(get_db), actor=Depends(require_admin)):
    """Var olan TÜM aktif Sirio km planlarının adını kısa M/Y koduna
    (ör. "1.440.000 km Bakımı" -> "12Y") çevirir — bkz.
    services.normalize_sirio_plan_names. Yeni oluşturulan Sirio planları
    zaten otomatik bu biçimde adlandırılır (bkz. default_plan_name); bu uç
    yalnızca ondan ÖNCE oluşturulmuş planları geriye dönük düzeltmek için."""
    renamed = services.normalize_sirio_plan_names(db)
    if renamed:
        services.log_action(
            db, actor, "rename", "plan", 0,
            f"{len(renamed)} Sirio planının adı kısa periyot koduna (M/Y) çevrildi.",
        )
    return {"renamed": renamed, "count": len(renamed)}


@app.patch("/api/plans/{plan_id}", response_model=schemas.PlanOut)
def update_plan(
    plan_id: int,
    body: schemas.PlanUpdate,
    db: Session = Depends(get_db),
    actor=Depends(require_admin),
):
    p = db.get(models.MaintenancePlan, plan_id)
    if not p:
        raise HTTPException(404, "Plan bulunamadı")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    services.sync_open_maintenance_trees(db)
    services.log_action(db, actor, "update", "plan", p.id, f"Bakım planı güncellendi: {p.name}")
    return p


@app.delete("/api/plans/{plan_id}")
def delete_plan(plan_id: int, db: Session = Depends(get_db), actor=Depends(require_admin)):
    """Bir planı ve ağacını siler. Bu plana ait açılmış bakım (iş emri)
    varsa geçmiş kaybolmasın diye silinmez, yalnızca pasife alınır."""
    p = db.get(models.MaintenancePlan, plan_id)
    if not p:
        raise HTTPException(404, "Plan bulunamadı")
    name = p.name
    if db.query(models.Maintenance).filter_by(plan_id=plan_id).first():
        p.is_active = False
        db.commit()
        services.sync_open_maintenance_trees(db)
        services.log_action(
            db, actor, "update", "plan", plan_id,
            f"Bakım planı pasife alındı (açık bakımları var): {name}",
        )
        return {"ok": True, "deactivated": True}
    roots = db.query(models.TreeNode).filter_by(parent_id=None, plan_id=plan_id).all()
    for r in roots:
        db.delete(r)
    db.query(models.PartHotspot).filter_by(plan_id=plan_id).delete()
    db.delete(p)
    db.commit()
    services.sync_open_maintenance_trees(db)
    services.log_action(db, actor, "delete", "plan", plan_id, f"Bakım planı silindi: {name}")
    return {"ok": True, "deactivated": False}


# ================================================================ Tree template
@app.get("/api/tree", response_model=list[schemas.TreeNodeOut])
def get_tree(
    plan_id: int | None = None,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    """plan_id verilirse o plana özel kök düğümler döner (yoksa boş liste
    döner — plana özel ağaç henüz tanımlanmamış demektir). plan_id
    verilmezse genel/ortak şablon (plan_id IS NULL) döner."""
    return (
        db.query(models.TreeNode)
        .filter_by(parent_id=None, plan_id=plan_id)
        .order_by(models.TreeNode.order_index)
        .all()
    )


def _plan_period_label(p: models.MaintenancePlan) -> str:
    if p.plan_type == "unknown":
        return "Belirsiz (?)"
    if p.plan_type == "time":
        mo = p.interval_months or 0
        return f"{mo // 12} yıl" if mo and mo % 12 == 0 else f"{mo} ay"
    return f"{fmt_km(p.interval_km)} km"


def _merged_walk(node: models.TreeNode, bucket: dict, plan: models.MaintenancePlan, label: str) -> None:
    if not node.children:
        bucket["leaves"].append({
            "name": node.name,
            "card_code": node.card_code or "",
            "note": node.note or "",
            "period_label": label,
            "plan_id": plan.id,
            "is_catalog_only": plan.is_catalog_only,
            "vehicle_model": plan.vehicle_model or "",
        })
        return
    for child in sorted(node.children, key=lambda c: c.order_index):
        sub = bucket["branches"].setdefault(child.name, {"branches": {}, "leaves": []})
        _merged_walk(child, sub, plan, label)


def _bucket_to_merged(name: str, bucket: dict, counter: list[int]) -> dict:
    counter[0] += 1
    my_id = counter[0]
    children = [_bucket_to_merged(cname, cbucket, counter) for cname, cbucket in bucket["branches"].items()]
    for leaf in bucket["leaves"]:
        counter[0] += 1
        children.append({"id": counter[0], "children": [], **leaf})
    return {
        "id": my_id, "name": name, "card_code": "", "note": "",
        "period_label": None, "plan_id": None, "is_catalog_only": False, "vehicle_model": None,
        "children": children,
    }


@app.get("/api/tree/merged", response_model=list[schemas.MergedTreeNodeOut])
def get_merged_tree(
    vehicle_model: str = "",
    all_models: bool = False,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    """"Tümü" (birleşik) görünüm.

    Varsayılan: yalnızca `vehicle_model` ile TAM eşleşen planları birleştirir
    (ör. yalnızca "Sirio"). `all_models=true` verilirse (Araç Modeli
    seçicisinde "Genel (mevcut filo)" seçiliyken kullanılır) `vehicle_model`
    parametresi YOK SAYILIR — filodaki TÜM modellerin (Genel + Sirio +
    Bozankaya Tip-1/2 + ...) TÜM aktif planları tek ağaçta birleştirilir;
    her yaprağın yanında hem periyodu (period_label) hem de HANGİ araç
    modeline ait olduğu (vehicle_model — boşsa "Genel") görünür.

    Salt-okunur — düzenleme bu uç noktadan yapılmaz, düzenleme için ilgili
    plana ait normal /api/tree?plan_id=... kullanılmalı."""
    q = db.query(models.MaintenancePlan).filter(models.MaintenancePlan.is_active == True)  # noqa: E712
    if not all_models:
        q = q.filter(models.MaintenancePlan.vehicle_model == vehicle_model)
    plans = q.order_by(models.MaintenancePlan.vehicle_model, models.MaintenancePlan.interval_km).all()

    root_bucket = {"branches": {}, "leaves": []}
    for plan in plans:
        label = _plan_period_label(plan)
        roots = (
            db.query(models.TreeNode)
            .filter_by(parent_id=None, plan_id=plan.id)
            .order_by(models.TreeNode.order_index)
            .all()
        )
        for r in roots:
            sub = root_bucket["branches"].setdefault(r.name, {"branches": {}, "leaves": []})
            _merged_walk(r, sub, plan, label)
    counter = [0]
    return [_bucket_to_merged(name, b, counter) for name, b in root_bucket["branches"].items()]


@app.get("/api/tree/flat", response_model=list[schemas.TreeNodeFlatOut])
def get_tree_flat(
    plan_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    """3D model editöründe parça atarken kullanılacak, ağacın tüm
    derinliğini içeren düz liste."""
    roots = (
        db.query(models.TreeNode)
        .filter_by(parent_id=None, plan_id=plan_id)
        .order_by(models.TreeNode.order_index)
        .all()
    )
    result: list[models.TreeNode] = []

    def walk(node: models.TreeNode):
        result.append(node)
        for c in sorted(node.children, key=lambda x: x.order_index):
            walk(c)

    for r in roots:
        walk(r)
    return result


@app.post("/api/tree", response_model=schemas.TreeNodeOut)
def create_node(
    body: schemas.TreeNodeCreate, db: Session = Depends(get_db), actor=Depends(require_admin)
):
    if body.plan_id is not None and not db.get(models.MaintenancePlan, body.plan_id):
        raise HTTPException(404, "Plan not found")
    if body.parent_id is not None:
        parent = db.get(models.TreeNode, body.parent_id)
        if parent is not None and body.plan_id is not None:
            ensure_node_belongs_to_plan(parent, body.plan_id, db)
    if body.parent_id is not None and not db.get(models.TreeNode, body.parent_id):
        raise HTTPException(404, "Üst düğüm bulunamadı")
    data = body.model_dump()
    if data["parent_id"] is not None:
        data["plan_id"] = None  # yalnızca kök düğümler plan taşır
    n = models.TreeNode(**data)
    db.add(n)
    db.commit()
    db.refresh(n)
    services.sync_open_maintenance_trees(db)
    services.log_action(db, actor, "create", "tree_node", n.id, f"Ağaç maddesi eklendi: {n.name}")
    return n


@app.patch("/api/tree/{node_id}", response_model=schemas.TreeNodeOut)
def update_node(
    node_id: int,
    body: schemas.TreeNodeUpdate,
    db: Session = Depends(get_db),
    actor=Depends(require_admin),
):
    n = db.get(models.TreeNode, node_id)
    if not n:
        raise HTTPException(404, "Düğüm bulunamadı")
    data = body.model_dump(exclude_unset=True)
    if "parent_id" in data:
        source_plan_id = tree_root(n, db).plan_id
        # Döngü kontrolü: düğüm kendi altına taşınamaz
        pid = data["parent_id"]
        if pid is not None and not db.get(models.TreeNode, pid):
            raise HTTPException(404, "Parent tree node not found")
        cursor = pid
        while cursor is not None:
            if cursor == node_id:
                raise HTTPException(400, "Düğüm kendi altına taşınamaz")
            parent = db.get(models.TreeNode, cursor)
            cursor = parent.parent_id if parent else None
        target_plan_id = (
            tree_root(db.get(models.TreeNode, pid), db).plan_id
            if pid is not None
            else source_plan_id
        )
        if source_plan_id != target_plan_id:
            raise HTTPException(400, "Nodes cannot be moved between plan templates")
        n.parent_id = pid
        if pid is not None:
            n.plan_id = None  # artık kök değil
        if pid is None:
            n.plan_id = source_plan_id
    if data.get("name") is not None:
        n.name = data["name"]
    if data.get("order_index") is not None:
        n.order_index = data["order_index"]
    if "note" in data and data["note"] is not None:
        n.note = data["note"]
    db.commit()
    db.refresh(n)
    services.sync_open_maintenance_trees(db)
    services.log_action(db, actor, "update", "tree_node", n.id, f"Ağaç maddesi güncellendi: {n.name}")
    return n


@app.patch("/api/tree/{node_id}/mandatory", response_model=schemas.TreeNodeOut)
def update_node_mandatory(
    node_id: int,
    body: schemas.TreeNodeMandatoryUpdate,
    db: Session = Depends(get_db),
    actor=Depends(require_staff),
):
    """Bir ağaç maddesinin GENEL (tüm araçlar için geçerli) zorunluluk
    varsayılanını değiştirir — "hepsine uygula". Yeni açılan iş emirlerinin
    yanı sıra, ZATEN AÇIK (tamamlanmamış) iş emirlerindeki karşılık gelen
    maddeler de HEMEN yeni duruma göre güncellenir (bkz.
    services.resync_open_items_mandatory_state) — usta üzerinde çalıştığı
    bakımda değişikliği anında görür. Bir teknisyen tarafından GERÇEKTEN
    tamamlanmış maddelere dokunulmaz. Admin, mühendis ve amir kullanabilir
    (Bakım Ağacı'nın geri kalanının aksine — o yalnızca admin'e açık)."""
    n = db.get(models.TreeNode, node_id)
    if not n:
        raise HTTPException(404, "Düğüm bulunamadı")
    n.is_mandatory = body.is_mandatory
    db.commit()
    db.refresh(n)
    changed = services.resync_open_items_mandatory_state(db, n.id)
    services.log_action(
        db, actor, "update", "tree_node", n.id,
        f"'{n.name}' maddesi {'zorunlu' if body.is_mandatory else 'zorunlu değil'} olarak işaretlendi"
        + (f" ({changed} açık iş emrinde madde güncellendi)" if changed else ""),
    )
    return n


@app.get("/api/tree/{node_id}/vehicle-exceptions", response_model=list[schemas.TreeNodeVehicleExceptionOut])
def list_node_vehicle_exceptions(
    node_id: int, db: Session = Depends(get_db), actor=Depends(require_staff),
):
    """Bir ağaç maddesi için, genel varsayılanı geçersiz kılan araca-özel
    istisnaların listesi (ör. genelde zorunlu ama 3805'te zaten yapıldığı
    için opsiyonel)."""
    if not db.get(models.TreeNode, node_id):
        raise HTTPException(404, "Düğüm bulunamadı")
    return (
        db.query(models.TreeNodeVehicleException)
        .filter_by(node_id=node_id)
        .order_by(models.TreeNodeVehicleException.id)
        .all()
    )


@app.post("/api/tree/{node_id}/vehicle-exceptions", response_model=schemas.TreeNodeVehicleExceptionOut)
def add_or_update_node_vehicle_exception(
    node_id: int,
    body: schemas.TreeNodeVehicleExceptionIn,
    db: Session = Depends(get_db),
    actor=Depends(require_staff),
):
    """Belirli TEK bir araç için bu maddenin zorunluluk durumunu genel
    varsayılandan farklı yapar ("araca göre seç"). Aynı araç için zaten bir
    istisna varsa değeri günceller (upsert). Yeni açılan iş emirlerinin
    yanı sıra, bu aracın ZATEN AÇIK (tamamlanmamış) iş emrindeki karşılık
    gelen madde de HEMEN yeni duruma göre güncellenir (gerçek bir
    teknisyen tamamlaması varsa dokunulmaz)."""
    node = db.get(models.TreeNode, node_id)
    if not node:
        raise HTTPException(404, "Düğüm bulunamadı")
    vehicle = db.get(models.Vehicle, body.vehicle_id)
    if not vehicle:
        raise HTTPException(404, "Araç bulunamadı")
    exc = (
        db.query(models.TreeNodeVehicleException)
        .filter_by(node_id=node_id, vehicle_id=body.vehicle_id)
        .first()
    )
    if exc:
        exc.is_mandatory = body.is_mandatory
    else:
        exc = models.TreeNodeVehicleException(
            node_id=node_id, vehicle_id=body.vehicle_id, is_mandatory=body.is_mandatory,
        )
        db.add(exc)
    db.commit()
    db.refresh(exc)
    changed = services.resync_open_items_mandatory_state(db, node_id, vehicle_id=body.vehicle_id)
    services.log_action(
        db, actor, "update", "tree_node", node_id,
        f"'{node.name}' maddesi {vehicle.code} için "
        f"{'zorunlu' if body.is_mandatory else 'zorunlu değil'} olarak işaretlendi (araç istisnası)"
        + (f" ({changed} açık iş emrinde madde güncellendi)" if changed else ""),
    )
    return exc


@app.delete("/api/tree/vehicle-exceptions/{exception_id}")
def delete_node_vehicle_exception(
    exception_id: int, db: Session = Depends(get_db), actor=Depends(require_staff),
):
    """Bir araç istisnasını kaldırır — o araç tekrar maddenin GENEL
    varsayılanına (TreeNode.is_mandatory) döner. Bu aracın ZATEN AÇIK
    (tamamlanmamış) iş emrindeki karşılık gelen madde de HEMEN genel
    varsayılana göre güncellenir (gerçek bir teknisyen tamamlaması varsa
    dokunulmaz)."""
    exc = db.get(models.TreeNodeVehicleException, exception_id)
    if not exc:
        raise HTTPException(404, "İstisna bulunamadı")
    node = db.get(models.TreeNode, exc.node_id)
    vehicle = db.get(models.Vehicle, exc.vehicle_id)
    node_id, vehicle_id = exc.node_id, exc.vehicle_id
    db.delete(exc)
    db.commit()
    changed = services.resync_open_items_mandatory_state(db, node_id, vehicle_id=vehicle_id)
    services.log_action(
        db, actor, "delete", "tree_node", node_id,
        f"'{node.name if node else '?'}' maddesindeki {vehicle.code if vehicle else '?'} istisnası kaldırıldı"
        + (f" ({changed} açık iş emrinde madde güncellendi)" if changed else ""),
    )
    return {"ok": True}


@app.delete("/api/tree/{node_id}")
def delete_node(node_id: int, db: Session = Depends(get_db), actor=Depends(require_admin)):
    n = db.get(models.TreeNode, node_id)
    if not n:
        raise HTTPException(404, "Düğüm bulunamadı")
    name = n.name
    db.delete(n)
    db.commit()
    services.sync_open_maintenance_trees(db)
    services.log_action(db, actor, "delete", "tree_node", node_id, f"Ağaç maddesi silindi: {name}")
    return {"ok": True}


@app.delete("/api/tree/plan/{plan_id}")
def delete_plan_tree(plan_id: int, db: Session = Depends(get_db), actor=Depends(require_admin)):
    """Bir bakım planının AĞACINI (tüm kök düğümler + altındaki her şey —
    alt maddeler, ekler, parça eşleşmeleri) tamamen siler; planın kendisi
    (Bakım Planları'ndaki kaydı, tamamlanma yüzdeleri vb.) ve o plana ait
    geçmiş iş emirleri DOKUNULMADAN kalır — yalnızca checklist içeriği
    temizlenir. "Ana Bölüm Ekle" ile sıfırdan yeniden kurulabilir ya da
    Excel/toplu katalog ile yeniden içe aktarılabilir. Tek tek her kök
    bölümü elle silmek yerine, o plana ait ağacı bir kerede tamamen
    boşaltmak için kullanılır."""
    p = db.get(models.MaintenancePlan, plan_id)
    if not p:
        raise HTTPException(404, "Plan bulunamadı")
    roots = db.query(models.TreeNode).filter_by(parent_id=None, plan_id=plan_id).all()
    deleted = len(roots)
    for r in roots:
        db.delete(r)
    db.commit()
    services.sync_open_maintenance_trees(db)
    services.log_action(
        db, actor, "delete", "tree_node", plan_id,
        f"'{p.name}' planının ağacı tamamen silindi ({deleted} ana bölüm).",
    )
    return {"ok": True, "deleted_roots": deleted}


@app.delete("/api/tree")
def delete_all_trees(db: Session = Depends(get_db), actor=Depends(require_admin)):
    """TEHLİKELİ — Bakım Ağacı sayfasındaki "Tüm Araçlardan Tüm Ağaçları Sil"
    butonu için: veritabanındaki HER plana (ve genel/ortak plan_id=None
    şablona) ait TÜM ağaç kök düğümlerini (dolayısıyla tüm alt maddeler,
    ekler, madde-parça eşleşmeleri, 3D parça eşleşmeleri) siler. Planların
    kendisi (Bakım Planları listesi, periyot/tolerans ayarları) ve geçmiş iş
    emirleri SİLİNMEZ — yalnızca checklist şablonları boşaltılır; genellikle
    hatalı/karışık bir toplu içe aktarmadan sonra sıfırdan temiz başlamak
    için kullanılır."""
    roots = db.query(models.TreeNode).filter_by(parent_id=None).all()
    deleted = len(roots)
    plans_affected = len({r.plan_id for r in roots})
    for r in roots:
        db.delete(r)
    db.commit()
    services.sync_open_maintenance_trees(db)
    services.log_action(
        db, actor, "delete", "tree_node", 0,
        f"TÜM bakım ağaçları silindi ({deleted} ana bölüm, {plans_affected} plan/şablon etkilendi).",
    )
    return {"ok": True, "deleted_roots": deleted, "plans_affected": plans_affected}


@app.post("/api/tree/import-excel", response_model=schemas.ImportTreeResult)
async def import_tree_excel(
    file: UploadFile = File(...),
    vehicle_model: str = Form(""),
    db: Session = Depends(get_db),
    actor=Depends(require_admin),
):
    """Admin, 'Koruyucu Bakım Liste Çalışması' formatındaki bir Excel dosyası
    yükleyerek yeni bakım plan(lar)ı + ağaçlarını doğrudan bu ekrandan
    oluşturabilir/güncelleyebilir. Aynı periyotta (interval_km) VE aynı
    araç modelinde plan zaten varsa o planın ağacı yeniden oluşturulur
    (idempotent). vehicle_model boşsa ("Genel") plan tüm araçlara
    uygulanır; doluysa yalnızca o modeldeki araçlara uygulanır — böylece
    farklı araç modelleri için ayrı Excel yükleyip bağımsız bakım
    ağaçları tanımlanabilir."""
    content = await file.read()
    try:
        result = excel_utils.import_maintenance_tree(db, content, vehicle_model=vehicle_model)
    except Exception as e:
        raise HTTPException(400, f"Excel işlenemedi: {e}")
    services.sync_open_maintenance_trees(db)
    plan_names = ", ".join(p["name"] for p in result["plans"])
    model_label = vehicle_model.strip() or "Genel"
    reset_count = result.get("maintenances_reset", 0)
    reset_note = f", {reset_count} açık iş emri sıfırlandı" if reset_count else ""
    # Ağaç güncellenince o modeldeki araçlar için otomatik bakım adaylarını
    # HEMEN yeniden değerlendir — admin'in aracı elle güncellemesi gerekmez.
    reopened = _recheck_vehicles_after_tree_import(db, actor, [vehicle_model.strip()])
    reopened_note = f", {reopened} aday iş emri hemen yeniden açıldı" if reopened else ""
    services.log_action(
        db, actor, "import", "tree_node", None,
        f"Bakım ağacı Excel'den içe aktarıldı ({model_label}): {len(result['plans'])} plan, "
        f"{result['nodes_created']} düğüm{reset_note}{reopened_note}",
        detail=plan_names,
    )
    return schemas.ImportTreeResult(
        plans=[schemas.PlanOut.model_validate(p) for p in result["plans_obj"]],
        nodes_created=result["nodes_created"],
        maintenances_reset=reset_count,
    )


@app.post("/api/tree/import-bulk-catalog", response_model=schemas.BulkCatalogImportResult)
async def import_bulk_catalog(
    file: UploadFile = File(...),
    vehicle_model: str = Form(""),
    dry_run: bool = Form(False),
    db: Session = Depends(get_db),
    actor=Depends(require_admin),
):
    """Admin, üretici/entegratörden gelen 'ARAÇ | BÖLGE | SİSTEM | EKİPMAN |
    BAKIM | BAKIM KALEMİ | KART KODU' formatındaki TOPLU bakım kataloğu
    Excel'ini (tek dosyada birden fazla araç modeli, hepsi km bazlı periyot —
    "1M"/"3M"/"1Y" gibi kısaltmalı kodlar da km katıdır, bkz.
    excel_utils._parse_bakim_value) yükler. Her satır kendi ARAÇ (model)
    değerine göre otomatik eşleşir (ör. SİRİO satırları yalnızca SİRİO
    modelindeki araçlara, BOZANKAYA satırları yalnızca BOZANKAYA modelindeki
    araçlara uygulanır). Her benzersiz (araç modeli, periyot) için plan+ağaç
    otomatik oluşturulur/güncellenir (idempotent — aynı modeli tekrar
    yüklemek o modelin ağacını günceller, diğer modellere dokunmaz).

    `vehicle_model` doldurulursa (yükleme formunda araç modeli seçilirse):
    dosyadaki ARAÇ sütunu göz ardı edilir, TÜM satırlar bu modele aitmiş
    gibi işlenir — ARAÇ sütunu olmayan, tek modele özel dosyalar için.

    `dry_run=true` verilirse: hiçbir şey kalıcı olarak KAYDEDİLMEZ — yalnızca
    "gerçekten yüklenirse ne olur" raporu (kaç plan/madde, kaç satır
    atlanır/belirsiz kalır) döner. Admin önce önizleyip sonuçtan memnunsa
    aynı dosyayı `dry_run=false` ile tekrar gönderir.

    `/api/tree/import-excel`'den farkı: o uç nokta TEK araç modeli +
    sütun-bloklu ('Koruyucu Bakım Liste Çalışması') formatı beklerken, bu uç
    nokta satır-bazlı ve ÇOK modelli bir kaynak dosyayı işler — bkz.
    excel_utils.import_bulk_catalog."""
    content = await file.read()
    try:
        result = excel_utils.import_bulk_catalog(
            db, content, vehicle_model_override=vehicle_model.strip(), dry_run=dry_run,
        )
    except Exception as e:
        raise HTTPException(400, f"Excel işlenemedi: {e}")

    if dry_run:
        # Önizlemede hiçbir şey kaydedilmediği için yeniden değerlendirme/
        # audit log YAPILMAZ — bunlar yalnızca gerçek bir değişiklik olduğunda
        # anlamlıdır.
        return schemas.BulkCatalogImportResult(**result)

    services.sync_open_maintenance_trees(db)
    models_label = ", ".join(result["vehicle_models"]) or "—"
    # Katalog güncellenince o modellerdeki araçlar için otomatik bakım
    # adaylarını HEMEN yeniden değerlendir — admin'in aracı elle güncellemesi
    # ya da yeniden yüklemesi gerekmez; kimin bakımı gelmiş kiminkinin
    # gelmediği içe aktarma biter bitmez "Başlatılmayı Bekleyenler"de görünür.
    reopened = _recheck_vehicles_after_tree_import(db, actor, result["vehicle_models"])
    reopened_note = f", {reopened} aday iş emri hemen yeniden açıldı" if reopened else ""
    services.log_action(
        db, actor, "import", "tree_node", None,
        f"Toplu bakım kataloğu içe aktarıldı ({models_label}): "
        f"{result['plans_created']} yeni plan, {result['plans_updated']} güncellenen plan, "
        f"{result['nodes_created']} düğüm ({result['leaves_created']} madde){reopened_note}"
        + (f", {len(result['skipped'])} satır atlandı" if result["skipped"] else "")
        + (f", {len(result['unresolved_period'])} satırda periyot belirsiz (? olarak eklendi)" if result["unresolved_period"] else "")
        + (f", {len(result['catalog_only_periods'])} periyot katalog-amaçlı işaretlendi (temel bakımın katı)" if result.get("catalog_only_periods") else ""),
        detail="\n".join(
            [f"Satır {s['row']}: {s['reason']}" for s in (result["skipped"][:30] + result["unresolved_period"][:30])]
            + [c["reason"] for c in result.get("catalog_only_periods", [])[:30]]
        ),
    )
    return schemas.BulkCatalogImportResult(**result)


@app.get("/api/tree/export-bulk-catalog")
def export_bulk_catalog(
    db: Session = Depends(get_db),
    actor=Depends(require_permission("export_excel")),
):
    """Bakım ağacını, yüklendiği KB TOPLU LİSTE formatında (ARAÇ | BÖLGE |
    SİSTEM | EKİPMAN | BAKIM | BAKIM KALEMİ | KART KODU) + programda eklenen
    açıklamaları taşıyan AÇIKLAMALAR sütunuyla Excel'e aktarır. Bu dosya
    yeniden 'Toplu Katalog Yükle' ile içe aktarılabilir (tam yedek/round-trip;
    AÇIKLAMALAR sütunu da geri okunur)."""
    content = excel_utils.build_bulk_catalog_workbook(db)
    services.log_action(
        db, actor, "export", "tree_node", None,
        "Bakım ağacı KB Toplu Liste formatında Excel'e aktarıldı (açıklamalar dahil)",
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=bakim_agaci_kb_toplu_liste.xlsx"},
    )


# ================================================================ Madde Ekleri (Faz 10)
@app.get("/api/tree/{node_id}/attachments", response_model=list[schemas.NodeAttachmentOut])
def list_node_attachments(
    node_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)
):
    """Bir ağaç maddesine bağlı ekler (foto/PDF/talimat/bağlantı). Girişi olan
    her kullanıcı görebilir (usta bakımı yaparken erişir)."""
    if not db.get(models.TreeNode, node_id):
        raise HTTPException(404, "Düğüm bulunamadı")
    return (
        db.query(models.NodeAttachment)
        .filter_by(node_id=node_id)
        .order_by(models.NodeAttachment.id)
        .all()
    )


@app.post("/api/tree/{node_id}/attachments", response_model=schemas.NodeAttachmentOut)
async def upload_node_attachment(
    node_id: int,
    label: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    actor=Depends(require_staff),
):
    """Yetkili rol (admin/mühendis/amir) bir maddeye dosya (fotoğraf / PDF /
    video / teknik talimat) ekler. Faz 27: Bakım Bilgi Bankası içeriği de bu
    uçtan beslenir — usta yalnızca görüntüler."""
    node = db.get(models.TreeNode, node_id)
    if not node:
        raise HTTPException(404, "Düğüm bulunamadı")
    original = file.filename or "dosya"
    ext = os.path.splitext(original)[1].lower()
    content = await file.read()
    content, ext = await run_in_threadpool(bg_remove.convert_to_browser_image, content, ext)
    _reject_dangerous_ext(ext)
    kind = _attachment_kind(ext)
    _validate_upload_content(content, ext, kind)
    content = _sanitize_if_svg(content, ext)
    fname = f"{uuid.uuid4().hex}{ext}"
    (ATTACH_DIR / fname).write_bytes(content)
    att = models.NodeAttachment(
        node_id=node_id,
        kind=kind,
        label=label,
        filename=fname,
        original_name=original,
    )
    db.add(att)
    db.commit()
    db.refresh(att)
    services.log_action(
        db, actor, "create", "attachment", att.id,
        f"Maddeye ek yüklendi: {node.name}",
        detail=f"{att.kind}: {original}",
    )
    return att


@app.post("/api/tree/{node_id}/attachment-link", response_model=schemas.NodeAttachmentOut)
def add_node_attachment_link(
    node_id: int,
    body: schemas.AttachmentLinkBody,
    db: Session = Depends(get_db),
    actor=Depends(require_staff),
):
    """Yetkili rol bir maddeye harici bağlantı (ör. YouTube videosu) ekler.
    YouTube/Vimeo gibi bilinen video adresleri kind='video' işaretlenir ki
    Bakım Bilgi Bankası bunları gömülü oynatıcıda açabilsin."""
    node = db.get(models.TreeNode, node_id)
    if not node:
        raise HTTPException(404, "Düğüm bulunamadı")
    att = models.NodeAttachment(
        node_id=node_id,
        kind="video" if _is_video_url(body.url) else "link",
        label=body.label,
        url=body.url,
    )
    db.add(att)
    db.commit()
    db.refresh(att)
    services.log_action(
        db, actor, "create", "attachment", att.id,
        f"Maddeye bağlantı eklendi: {node.name}", detail=body.url,
    )
    return att


@app.delete("/api/attachments/{attachment_id}")
def delete_node_attachment(
    attachment_id: int, db: Session = Depends(get_db), actor=Depends(require_staff)
):
    att = db.get(models.NodeAttachment, attachment_id)
    if not att:
        raise HTTPException(404, "Ek bulunamadı")
    node_id = att.node_id
    fpath = ATTACH_DIR / att.filename if att.filename else None
    label = att.label or att.original_name or att.url
    db.delete(att)
    db.commit()
    if fpath and fpath.is_file():
        try:
            fpath.unlink()
        except OSError:
            pass
    services.log_action(
        db, actor, "delete", "attachment", attachment_id,
        f"Madde eki silindi: {label}", detail=f"node #{node_id}",
    )
    return {"ok": True}


# ================================================================ Bakım Bilgi Bankası (Faz 27)
# Ustanın bir maddeye girip "bunu nasıl yaparım" sorusunun cevabını (açıklama
# metni + video/PDF/görsel) tek yerden görebilmesi için. İçerik yeni bir tablo
# tutmaz: madde açıklaması TreeNode.note, materyaller NodeAttachment'tır —
# yani Bakım Ağacı'nda zaten girilmiş bilgi burada okunabilir hale gelir.


@app.patch("/api/tree/{node_id}/kb-note", response_model=schemas.TreeNodeOut)
def update_node_kb_note(
    node_id: int,
    body: schemas.NodeKbNoteBody,
    db: Session = Depends(get_db),
    actor=Depends(require_staff),
):
    """Yalnızca maddenin bilgi/talimat metnini günceller. PATCH /api/tree/{id}
    ad/konum gibi yapısal alanları da değiştirdiği için admin'e kapalıdır;
    bu uç yetkili rollerin (mühendis/amir) ağacın YAPISINA dokunmadan bilgi
    bankası içeriği yazabilmesi içindir."""
    node = db.get(models.TreeNode, node_id)
    if not node:
        raise HTTPException(404, "Düğüm bulunamadı")
    node.note = body.note
    db.commit()
    db.refresh(node)
    services.log_action(
        db, actor, "update", "tree_node", node.id,
        f"Bilgi bankası açıklaması güncellendi: {node.name}",
    )
    return node


@app.get("/api/knowledge/search", response_model=list[schemas.KnowledgeHitOut])
def search_knowledge(
    q: str = "",
    limit: int = 60,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    """Bilgi bankası araması — madde adı, kart kodu ve açıklama metni içinde
    geçen aramaya uyan maddeleri, ağaçtaki yolu ve hangi periyoda ait olduğu
    ile birlikte düz liste hâlinde döner. Girişi olan herkes (usta dahil)
    kullanabilir."""
    term = (q or "").strip()
    if len(term) < 2:
        return []
    limit = max(1, min(limit, 200))
    pat = f"%{term}%"
    nodes = (
        db.query(models.TreeNode)
        .filter(
            or_(
                models.TreeNode.name.ilike(pat),
                models.TreeNode.card_code.ilike(pat),
                models.TreeNode.note.ilike(pat),
            )
        )
        .limit(limit)
        .all()
    )
    plan_names = {p.id: p.name for p in db.query(models.MaintenancePlan).all()}
    hits: list[schemas.KnowledgeHitOut] = []
    for n in nodes:
        # Kök düğüme kadar yürüyerek "Gövde > Fren > Balata" yolunu kur.
        path: list[str] = []
        cur, plan_id, guard = n, n.plan_id, 0
        while cur is not None and guard < 50:
            path.append(cur.name)
            if cur.plan_id is not None:
                plan_id = cur.plan_id
            cur = db.get(models.TreeNode, cur.parent_id) if cur.parent_id else None
            guard += 1
        path.reverse()
        hits.append(
            schemas.KnowledgeHitOut(
                id=n.id,
                name=n.name,
                card_code=n.card_code or "",
                note=n.note or "",
                path=path,
                plan_id=plan_id,
                plan_name=plan_names.get(plan_id or -1, ""),
                attachment_count=len(n.attachments),
                has_video=any(a.kind == "video" for a in n.attachments),
            )
        )
    hits.sort(key=lambda h: (0 if h.name.lower().startswith(term.lower()) else 1, h.name.lower()))
    return hits


def _node_part_usage_out(u: "models.TreeNodePartUsage") -> schemas.TreeNodePartUsageOut:
    return schemas.TreeNodePartUsageOut(
        id=u.id, tree_node_id=u.tree_node_id, part_id=u.part_id,
        part_name=u.part.name if u.part else "", qty=u.qty,
    )


@app.get("/api/tree/{node_id}/parts", response_model=list[schemas.TreeNodePartUsageOut])
def list_node_part_usages(
    node_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)
):
    """Bir bakım ağacı maddesinin tamamlanması sırasında hangi parçadan kaç
    adet kullanıldığı — girişi olan her kullanıcı görebilir."""
    if not db.get(models.TreeNode, node_id):
        raise HTTPException(404, "Düğüm bulunamadı")
    usages = (
        db.query(models.TreeNodePartUsage)
        .filter_by(tree_node_id=node_id)
        .order_by(models.TreeNodePartUsage.id)
        .all()
    )
    return [_node_part_usage_out(u) for u in usages]


@app.post("/api/tree/{node_id}/parts", response_model=schemas.TreeNodePartUsageOut)
def add_node_part_usage(
    node_id: int,
    body: schemas.TreeNodePartUsageIn,
    db: Session = Depends(get_db),
    actor=Depends(require_admin),
):
    """Admin bir maddeye "bu madde tamamlanınca şu parçadan şu kadar
    kullanılır" eşleşmesi ekler/günceller (aynı parça zaten eşleşmişse
    adedini günceller — upsert)."""
    node = db.get(models.TreeNode, node_id)
    if not node:
        raise HTTPException(404, "Düğüm bulunamadı")
    part = db.get(models.PartCatalogItem, body.part_id)
    if not part:
        raise HTTPException(404, "Parça bulunamadı")
    usage = (
        db.query(models.TreeNodePartUsage)
        .filter_by(tree_node_id=node_id, part_id=body.part_id)
        .first()
    )
    if usage:
        usage.qty = body.qty
    else:
        usage = models.TreeNodePartUsage(tree_node_id=node_id, part_id=body.part_id, qty=body.qty)
        db.add(usage)
    db.commit()
    db.refresh(usage)
    services.log_action(
        db, actor, "update", "tree_node", node_id,
        f"Madde-parça eşleşmesi: {node.name} ← {part.name} x{_fmt_qty(body.qty)}",
    )
    return _node_part_usage_out(usage)


@app.delete("/api/tree/part-usages/{usage_id}")
def delete_node_part_usage(
    usage_id: int, db: Session = Depends(get_db), actor=Depends(require_admin)
):
    usage = db.get(models.TreeNodePartUsage, usage_id)
    if not usage:
        raise HTTPException(404, "Eşleşme bulunamadı")
    node_id = usage.tree_node_id
    part_name = usage.part.name if usage.part else f"#{usage.part_id}"
    db.delete(usage)
    db.commit()
    services.log_action(
        db, actor, "delete", "tree_node", node_id,
        f"Madde-parça eşleşmesi kaldırıldı: {part_name}",
    )
    return {"ok": True}


# ================================================================ Parça Bilgi Bankası
# Arıza/bakım anında hızlı referans için: buji, kuplör vb. sık kullanılan
# parçaların görselli kataloğu. Her parçanın dokümanı, kablo/devre şeması,
# eğitim videosu gibi ekleri olabilir. Görüntüleme herkese (admin/mühendis/
# usta) açık; içerik yönetimi (ekleme/silme/yükleme) admin+mühendise özel.
@app.get("/api/parts", response_model=list[schemas.PartCatalogItemOut])
def list_parts(db: Session = Depends(get_db), _=Depends(get_current_user)):
    """Yalnızca EN ÜST seviye parçaları döner (parent_id boş olanlar) — alt
    parçalar (varsa) her birinin `children` alanında iç içe gelir (sıralı)."""
    return (
        db.query(models.PartCatalogItem)
        .filter(models.PartCatalogItem.parent_id.is_(None))
        .order_by(models.PartCatalogItem.order_index, models.PartCatalogItem.name)
        .all()
    )


@app.get("/api/parts/{part_id}", response_model=schemas.PartCatalogItemOut)
def get_part(part_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)):
    part = db.get(models.PartCatalogItem, part_id)
    if not part:
        raise HTTPException(404, "Parça bulunamadı")
    return part


@app.get("/api/parts/{part_id}/qrcode")
def part_qrcode(
    part_id: int, request: Request, download: bool = False, db: Session = Depends(get_db)
):
    """Parçanın QR kodu (Faz 22) — telefonla okutulunca uygulama doğrudan bu
    parçanın bilgi/doküman sayfasını (Parça Bilgi Bankası) açar."""
    part = db.get(models.PartCatalogItem, part_id)
    if not part:
        raise HTTPException(404, "Parça bulunamadı")
    url = f"{str(request.base_url).rstrip('/')}/?part_id={part.id}"
    png = qr_utils.build_qr_png(url)
    headers = {"Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = f"attachment; filename=qr_parca_{part.id}.png"
    return Response(content=png, media_type="image/png", headers=headers)


@app.post("/api/parts", response_model=schemas.PartCatalogItemOut)
def create_part(
    body: schemas.PartCatalogItemCreate,
    db: Session = Depends(get_db),
    actor=Depends(require_staff),
):
    parent = None
    if body.parent_id is not None:
        parent = db.get(models.PartCatalogItem, body.parent_id)
        if not parent:
            raise HTTPException(404, "Üst parça bulunamadı")
    part = models.PartCatalogItem(
        name=body.name,
        description=body.description,
        parent_id=body.parent_id,
        stock_qty=body.stock_qty,
        min_stock=body.min_stock,
        unit=body.unit or "adet",
    )
    db.add(part)
    db.commit()
    db.refresh(part)
    label = f"{parent.name} > {part.name}" if parent else part.name
    services.log_action(db, actor, "create", "part", part.id, f"Parça eklendi: {label}")
    return part


def _collect_part_descendants(part: "models.PartCatalogItem") -> list["models.PartCatalogItem"]:
    """Bir parçanın tüm alt ağacını (torunlar dahil) düz bir listeye toplar
    — silme sırasında diskteki görsel/ek dosyalarını temizlemek ve taşıma
    sırasında döngü oluşmasını engellemek için kullanılır."""
    out: list[models.PartCatalogItem] = []
    for child in part.children:
        out.append(child)
        out.extend(_collect_part_descendants(child))
    return out


# NOT: Bu route, /api/parts/{part_id} tanımından ÖNCE gelmeli — aksi halde
# FastAPI "reorder"ı {part_id} (int) olarak ayrıştırmaya çalışıp 422 döner
# (bir sonraki eşleşen route'a düşmez, ilk yapısal eşleşmede durur).
@app.patch("/api/parts/reorder", response_model=list[schemas.PartCatalogItemOut])
def reorder_parts(
    body: schemas.PartReorderBody,
    db: Session = Depends(get_db),
    actor=Depends(require_staff),
):
    """Sürükle-bırak ile sıralama: aynı üst parçaya (veya en üst seviyeye)
    ait parçaların order_index'lerini verilen sıraya göre yeniden atar."""
    parts = (
        db.query(models.PartCatalogItem)
        .filter(models.PartCatalogItem.id.in_(body.ordered_ids))
        .all()
    )
    by_id = {p.id: p for p in parts}
    for pid in body.ordered_ids:
        p = by_id.get(pid)
        if not p or p.parent_id != body.parent_id:
            raise HTTPException(400, "Sıralanan parçalardan biri bulunamadı veya farklı bir üst parçaya ait")
    for idx, pid in enumerate(body.ordered_ids):
        by_id[pid].order_index = idx
    db.commit()
    services.log_action(
        db, actor, "update", "part", None,
        "Parça sırası güncellendi",
        f"parent_id={body.parent_id}, sıra={body.ordered_ids}",
    )
    return [by_id[pid] for pid in body.ordered_ids]


@app.patch("/api/parts/{part_id}", response_model=schemas.PartCatalogItemOut)
def update_part(
    part_id: int,
    body: schemas.PartCatalogItemUpdate,
    db: Session = Depends(get_db),
    actor=Depends(require_staff),
):
    part = db.get(models.PartCatalogItem, part_id)
    if not part:
        raise HTTPException(404, "Parça bulunamadı")
    if body.name is not None:
        part.name = body.name
    if body.description is not None:
        part.description = body.description
    if body.order_index is not None:
        part.order_index = body.order_index
    if body.min_stock is not None:
        part.min_stock = body.min_stock
    if body.unit is not None:
        part.unit = body.unit
    if "parent_id" in body.model_fields_set and body.parent_id != part.parent_id:
        if body.parent_id == part.id:
            raise HTTPException(400, "Bir parça kendi alt parçası olamaz")
        if body.parent_id is not None:
            new_parent = db.get(models.PartCatalogItem, body.parent_id)
            if not new_parent:
                raise HTTPException(404, "Üst parça bulunamadı")
            # Döngü oluşmasın diye: yeni üst parça, bu parçanın (mevcut)
            # alt ağacında olamaz.
            descendant_ids = {p.id for p in _collect_part_descendants(part)}
            if body.parent_id in descendant_ids:
                raise HTTPException(400, "Bir parça kendi alt parçasının içine taşınamaz")
        part.parent_id = body.parent_id
    db.commit()
    db.refresh(part)
    services.log_action(db, actor, "update", "part", part.id, f"Parça güncellendi: {part.name}")
    return part


@app.post("/api/parts/{part_id}/stock-adjust", response_model=schemas.PartCatalogItemOut)
def adjust_part_stock(
    part_id: int,
    body: schemas.PartStockAdjustIn,
    db: Session = Depends(get_db),
    actor=Depends(require_staff),
):
    """Manuel stok hareketi — yeni parça geldiğinde (delta > 0) ya da sayım/
    fire düzeltmesinde (delta < 0) kullanılır. Bir bakım maddesi tamamlanınca
    yapılan OTOMATİK düşüm bu uç noktayı kullanmaz (bkz.
    services._adjust_stock_for_node) — bu yalnızca elle giriş içindir."""
    part = db.get(models.PartCatalogItem, part_id)
    if not part:
        raise HTTPException(404, "Parça bulunamadı")
    if body.delta == 0:
        raise HTTPException(400, "delta 0 olamaz")
    part.stock_qty += body.delta
    db.commit()
    db.refresh(part)
    yon = "girişi" if body.delta > 0 else "düşümü"
    detail = f"Yeni stok: {_fmt_qty(part.stock_qty)} {part.unit}" + (f" — {body.note}" if body.note else "")
    delta_str = f"+{_fmt_qty(body.delta)}" if body.delta > 0 else _fmt_qty(body.delta)
    services.log_action(
        db, actor, "update", "part", part.id,
        f"Parça stok {yon}: {part.name} ({delta_str})",
        detail=detail,
    )
    return part


def _repair_material_out(r: "models.PartRepairMaterial") -> schemas.PartRepairMaterialOut:
    return schemas.PartRepairMaterialOut(
        id=r.id, part_id=r.part_id, material_part_id=r.material_part_id,
        material_name=r.material.name if r.material else "",
        material_stock_qty=r.material.stock_qty if r.material else None,
        material_unit=r.material.unit if r.material else "",
        free_text=r.free_text, qty=r.qty,
    )


@app.get("/api/parts/{part_id}/repair-materials", response_model=list[schemas.PartRepairMaterialOut])
def list_repair_materials(
    part_id: int, db: Session = Depends(get_db), _=Depends(get_current_user)
):
    """Bir parçanın (ör. Körük) tamirinde/bakımında kullanılan malzeme
    listesi — girişi olan her kullanıcı görebilir."""
    if not db.get(models.PartCatalogItem, part_id):
        raise HTTPException(404, "Parça bulunamadı")
    rows = (
        db.query(models.PartRepairMaterial)
        .filter_by(part_id=part_id)
        .order_by(models.PartRepairMaterial.id)
        .all()
    )
    return [_repair_material_out(r) for r in rows]


@app.post("/api/parts/{part_id}/repair-materials", response_model=schemas.PartRepairMaterialOut)
def add_repair_material(
    part_id: int,
    body: schemas.PartRepairMaterialIn,
    db: Session = Depends(get_db),
    actor=Depends(require_staff),
):
    """Bu parçanın tamirinde kullanılan bir malzeme satırı ekler — ya
    katalogdan seçilen (material_part_id, stok bağlantılı) ya da serbest
    metin (free_text, yalnızca bilgi amaçlı) olarak."""
    part = db.get(models.PartCatalogItem, part_id)
    if not part:
        raise HTTPException(404, "Parça bulunamadı")
    material = None
    if body.material_part_id is not None:
        material = db.get(models.PartCatalogItem, body.material_part_id)
        if not material:
            raise HTTPException(404, "Malzeme parçası bulunamadı")
        if material.id == part.id:
            raise HTTPException(400, "Bir parça kendi tamirinde malzeme olarak kullanılamaz")
    elif not body.free_text.strip():
        raise HTTPException(400, "Katalogdan bir malzeme seçin ya da serbest metin girin")
    row = models.PartRepairMaterial(
        part_id=part_id,
        material_part_id=body.material_part_id,
        free_text="" if body.material_part_id is not None else body.free_text.strip(),
        qty=body.qty,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    services.log_action(
        db, actor, "update", "part", part.id,
        f"Tamir malzemesi eklendi: {part.name} ← {material.name if material else body.free_text.strip()} x{_fmt_qty(body.qty)}",
    )
    return _repair_material_out(row)


@app.delete("/api/parts/repair-materials/{row_id}")
def delete_repair_material(
    row_id: int, db: Session = Depends(get_db), actor=Depends(require_staff)
):
    row = db.get(models.PartRepairMaterial, row_id)
    if not row:
        raise HTTPException(404, "Malzeme satırı bulunamadı")
    part_id = row.part_id
    label = row.material.name if row.material else row.free_text
    db.delete(row)
    db.commit()
    services.log_action(
        db, actor, "delete", "part", part_id, f"Tamir malzemesi kaldırıldı: {label}",
    )
    return {"ok": True}


@app.post("/api/parts/{part_id}/repair", response_model=schemas.PartRepairResult)
def repair_part(
    part_id: int,
    body: schemas.PartRepairIn,
    db: Session = Depends(get_db),
    actor=Depends(require_staff),
):
    """"Tamir Edildi" — bu parçanın tamir malzemesi listesindeki, katalogdan
    seçilmiş (material_part_id dolu) her satır için o malzemenin stoğu `qty`
    kadar düşülür (bkz. models.PartRepairMaterial). Serbest metin satırlar
    (free_text) stok bağlantısı olmadığı için atlanır — yalnızca bilgi
    amaçlıdır."""
    part = db.get(models.PartCatalogItem, part_id)
    if not part:
        raise HTTPException(404, "Parça bulunamadı")
    rows = db.query(models.PartRepairMaterial).filter_by(part_id=part_id).all()
    deducted: list[str] = []
    skipped: list[str] = []
    for r in rows:
        if r.material_part_id is not None:
            material = db.get(models.PartCatalogItem, r.material_part_id)
            if material:
                material.stock_qty -= r.qty
                deducted.append(f"{material.name} x{_fmt_qty(r.qty)}")
        else:
            skipped.append(f"{r.free_text} x{_fmt_qty(r.qty)}" if r.free_text else f"(malzeme) x{_fmt_qty(r.qty)}")
    db.commit()
    detail = ("Düşülen: " + ", ".join(deducted) if deducted else "Düşülen malzeme yok")
    if skipped:
        detail += " — Bilgi amaçlı (stok bağlantısız): " + ", ".join(skipped)
    if body.note:
        detail += f" — Not: {body.note}"
    services.log_action(
        db, actor, "update", "part", part.id, f"Tamir edildi: {part.name}", detail=detail,
    )
    return schemas.PartRepairResult(ok=True, materials_deducted=deducted, materials_skipped=skipped)


@app.post("/api/parts/{part_id}/image", response_model=schemas.PartCatalogItemOut)
async def upload_part_image(
    part_id: int,
    file: UploadFile = File(...),
    remove_bg: bool = Form(True),
    db: Session = Depends(get_db),
    actor=Depends(require_staff),
):
    """Parçanın kapak görselini yükler/değiştirir. remove_bg=true ise (varsayılan)
    arka plan otomatik olarak (yapay zeka ile) kaldırılır ve şeffaf PNG olarak
    saklanır; remove_bg=false ise görsel olduğu gibi kaydedilir."""
    part = db.get(models.PartCatalogItem, part_id)
    if not part:
        raise HTTPException(404, "Parça bulunamadı")
    old_fname = part.image_filename
    original = file.filename or "gorsel"
    ext = os.path.splitext(original)[1].lower()
    content = await file.read()
    content, ext = await run_in_threadpool(bg_remove.convert_to_browser_image, content, ext)
    if ext not in _IMAGE_EXTS:
        raise HTTPException(400, "Yalnızca resim dosyası yüklenebilir")
    _validate_upload_content(content, ext, "image")
    if remove_bg and ext != ".svg":
        content = await run_in_threadpool(bg_remove.remove_background, content)
        ext = ".png"
    content = _sanitize_if_svg(content, ext)
    fname = f"{uuid.uuid4().hex}{ext}"
    (PARTS_DIR / fname).write_bytes(content)
    part.image_filename = fname
    db.commit()
    db.refresh(part)
    if old_fname:
        old_path = PARTS_DIR / old_fname
        if old_path.is_file():
            try:
                old_path.unlink()
            except OSError:
                pass
    services.log_action(db, actor, "update", "part", part.id, f"Parça görseli güncellendi: {part.name}")
    return part


@app.delete("/api/parts/{part_id}")
def delete_part(part_id: int, db: Session = Depends(get_db), actor=Depends(require_staff)):
    part = db.get(models.PartCatalogItem, part_id)
    if not part:
        raise HTTPException(404, "Parça bulunamadı")
    name = part.name
    # Alt parçalar (varsa, torunlar dahil) DB'den cascade ile silinir; onların
    # görsel/ek dosyalarını da diskten temizlemek için önceden toplanır.
    all_parts = [part] + _collect_part_descendants(part)
    image_fnames = [p.image_filename for p in all_parts if p.image_filename]
    att_filenames = [a.filename for p in all_parts for a in p.attachments if a.filename]
    child_count = len(all_parts) - 1
    db.delete(part)
    db.commit()
    for fname in image_fnames + att_filenames:
        fpath = PARTS_DIR / fname
        if fpath.is_file():
            try:
                fpath.unlink()
            except OSError:
                pass
    detail = f"{child_count} alt parça ile birlikte silindi" if child_count else ""
    services.log_action(db, actor, "delete", "part", part_id, f"Parça silindi: {name}", detail)
    return {"ok": True}


@app.post("/api/parts/{part_id}/attachments", response_model=schemas.PartCatalogAttachmentOut)
async def upload_part_attachment(
    part_id: int,
    label: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    actor=Depends(require_staff),
):
    """Admin/mühendis bir parçaya dosya ekler (doküman/kablo şeması/devre
    şeması/eğitim videosu/diğer — türü uzantıdan otomatik belirlenir)."""
    part = db.get(models.PartCatalogItem, part_id)
    if not part:
        raise HTTPException(404, "Parça bulunamadı")
    original = file.filename or "dosya"
    ext = os.path.splitext(original)[1].lower()
    content = await file.read()
    content, ext = await run_in_threadpool(bg_remove.convert_to_browser_image, content, ext)
    _reject_dangerous_ext(ext)
    kind = _part_attachment_kind(ext)
    _validate_upload_content(content, ext, kind)
    content = _sanitize_if_svg(content, ext)
    fname = f"{uuid.uuid4().hex}{ext}"
    (PARTS_DIR / fname).write_bytes(content)
    att = models.PartCatalogAttachment(
        part_id=part_id,
        kind=kind,
        label=label,
        filename=fname,
        original_name=original,
    )
    db.add(att)
    db.commit()
    db.refresh(att)
    services.log_action(
        db, actor, "create", "part_attachment", att.id,
        f"Parçaya ek yüklendi: {part.name}", detail=f"{att.kind}: {original}",
    )
    return att


@app.post("/api/parts/{part_id}/attachment-link", response_model=schemas.PartCatalogAttachmentOut)
def add_part_attachment_link(
    part_id: int,
    body: schemas.AttachmentLinkBody,
    db: Session = Depends(get_db),
    actor=Depends(require_staff),
):
    """Admin/mühendis bir parçaya harici bağlantı ekler (ör. YouTube eğitim
    videosu ya da bulut üzerindeki bir doküman)."""
    part = db.get(models.PartCatalogItem, part_id)
    if not part:
        raise HTTPException(404, "Parça bulunamadı")
    att = models.PartCatalogAttachment(
        part_id=part_id, kind="link", label=body.label, url=body.url
    )
    db.add(att)
    db.commit()
    db.refresh(att)
    services.log_action(
        db, actor, "create", "part_attachment", att.id,
        f"Parçaya bağlantı eklendi: {part.name}", detail=body.url,
    )
    return att


@app.delete("/api/part-attachments/{attachment_id}")
def delete_part_attachment(
    attachment_id: int, db: Session = Depends(get_db), actor=Depends(require_staff)
):
    att = db.get(models.PartCatalogAttachment, attachment_id)
    if not att:
        raise HTTPException(404, "Ek bulunamadı")
    part_id = att.part_id
    fpath = PARTS_DIR / att.filename if att.filename else None
    label = att.label or att.original_name or att.url
    db.delete(att)
    db.commit()
    if fpath and fpath.is_file():
        try:
            fpath.unlink()
        except OSError:
            pass
    services.log_action(
        db, actor, "delete", "part_attachment", attachment_id,
        f"Parça eki silindi: {label}", detail=f"part #{part_id}",
    )
    return {"ok": True}


# ================================================================ 3D Model + Parça Eşleşmesi
@app.post("/api/models3d", response_model=schemas.Model3DOut)
async def create_model3d(
    label: str = Form(""),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    actor=Depends(require_admin),
):
    """Admin bir .glb 3D model dosyası yükler (plandan bağımsız, ortak model)."""
    ext = os.path.splitext(file.filename or "")[1].lower() or ".glb"
    if ext not in (".glb", ".gltf"):
        raise HTTPException(400, "Desteklenmeyen dosya türü (yalnızca .glb/.gltf)")
    fname = f"{uuid.uuid4().hex}{ext}"
    content = await file.read()
    (MODELS_DIR / fname).write_bytes(content)

    order_index = db.query(models.Model3D).count()
    m = models.Model3D(label=label, filename=fname, order_index=order_index)
    db.add(m)
    db.commit()
    db.refresh(m)
    services.log_action(db, actor, "create", "model3d", m.id, f"3D model yüklendi: {m.label or fname}")
    return m


@app.get("/api/models3d", response_model=list[schemas.Model3DOut])
def list_models3d(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return db.query(models.Model3D).order_by(models.Model3D.order_index).all()


@app.delete("/api/models3d/{model_id}")
def delete_model3d(model_id: int, db: Session = Depends(get_db), actor=Depends(require_admin)):
    m = db.get(models.Model3D, model_id)
    if not m:
        raise HTTPException(404, "Model bulunamadı")
    label = m.label or m.filename
    fpath = MODELS_DIR / m.filename
    db.delete(m)
    db.commit()
    if fpath.is_file():
        try:
            fpath.unlink()
        except OSError:
            pass
    services.log_action(db, actor, "delete", "model3d", model_id, f"3D model silindi: {label}")
    return {"ok": True}


@app.get("/api/models3d/{model_id}/hotspots", response_model=list[schemas.PartHotspotOut])
def list_part_hotspots(
    model_id: int,
    plan_id: int,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    """Admin editöründe: bu model + bu plan için tanımlı parça eşleşmeleri."""
    return (
        db.query(models.PartHotspot)
        .filter_by(model_id=model_id, plan_id=plan_id)
        .order_by(models.PartHotspot.id)
        .all()
    )


@app.post("/api/models3d/{model_id}/hotspots", response_model=schemas.PartHotspotOut)
def create_part_hotspot(
    model_id: int,
    body: schemas.PartHotspotCreate,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    m = db.get(models.Model3D, model_id)
    if not m:
        raise HTTPException(404, "Model bulunamadı")
    if not db.get(models.TreeNode, body.tree_node_id):
        raise HTTPException(404, "Ağaç düğümü bulunamadı")
    if not db.get(models.MaintenancePlan, body.plan_id):
        raise HTTPException(404, "Plan bulunamadı")
    node = db.get(models.TreeNode, body.tree_node_id)
    ensure_node_belongs_to_plan(node, body.plan_id, db)
    h = models.PartHotspot(model_id=model_id, **body.model_dump())
    db.add(h)
    db.commit()
    db.refresh(h)
    return h


@app.patch("/api/model-hotspots/{hotspot_id}", response_model=schemas.PartHotspotOut)
def update_part_hotspot(
    hotspot_id: int,
    body: schemas.PartHotspotUpdate,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    h = db.get(models.PartHotspot, hotspot_id)
    if not h:
        raise HTTPException(404, "Parça eşleşmesi bulunamadı")
    if body.tree_node_id is not None:
        node = db.get(models.TreeNode, body.tree_node_id)
        if not node:
            raise HTTPException(404, "Tree node not found")
        ensure_node_belongs_to_plan(node, h.plan_id, db)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(h, k, v)
    db.commit()
    db.refresh(h)
    return h


@app.delete("/api/model-hotspots/{hotspot_id}")
def delete_part_hotspot(hotspot_id: int, db: Session = Depends(get_db), _=Depends(require_admin)):
    h = db.get(models.PartHotspot, hotspot_id)
    if not h:
        raise HTTPException(404, "Parça eşleşmesi bulunamadı")
    db.delete(h)
    db.commit()
    return {"ok": True}


# ================================================================ Maintenances
def _parse_date(s: str | None, end: bool = False) -> datetime | None:
    """'YYYY-MM-DD' veya ISO tarih dizesini datetime'a çevirir. end=True ise
    günün sonuna (23:59:59) çeker, böylece 'bitiş' filtresi o günü de kapsar."""
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    if end and d.hour == 0 and d.minute == 0 and d.second == 0:
        d = d.replace(hour=23, minute=59, second=59)
    return d


def _filtered_maintenance_query(
    db: Session,
    user: models.User,
    status: str | None,
    vehicle_id: int | None,
    plan_id: int | None,
    worked_by_id: int | None,
    date_field: str,
    date_from: str | None,
    date_to: str | None,
    km_min: int | None,
    km_max: int | None,
    vehicle_model: str | None = None,
):
    """Bakımlar listesi ve dışa aktarma tarafından ortak kullanılan filtreli
    sorgu (Faz 13-14). date_field: 'created' | 'completed' | 'started'."""
    q = db.query(models.Maintenance)
    # Faz 26: Bakımlar listesi (ve dışa aktarma) HERKES için yalnızca
    # "Başlat" denmiş iş emirlerini gösterir — henüz onaylanmamış otomatik
    # adaylar yalnızca GET /api/maintenances/pending-start ile (başlatma
    # yetkisi olanlara) listelenir.
    q = q.filter(models.Maintenance.is_activated == True)  # noqa: E712
    if user.role == "technician":
        q = q.filter(
            (models.Maintenance.assigned_to_id == user.id)
            | (models.Maintenance.assigned_to_id.is_(None))
        )
    if status:
        q = q.filter(models.Maintenance.status == status)
    if vehicle_id:
        q = q.filter(models.Maintenance.vehicle_id == vehicle_id)
    if plan_id:
        q = q.filter(models.Maintenance.plan_id == plan_id)
    if vehicle_model:
        # Bakım Geçmişi'nde "Sirio / Bozankaya Tip-1 / Tip-2" gibi araç
        # tipine göre filtreleme — Maintenance'ın kendi vehicle_model alanı
        # yok, bağlı Vehicle üzerinden filtrelenir.
        q = q.join(models.Vehicle, models.Maintenance.vehicle_id == models.Vehicle.id).filter(
            models.Vehicle.model == vehicle_model
        )
    if km_min is not None:
        q = q.filter(models.Maintenance.target_km >= km_min)
    if km_max is not None:
        q = q.filter(models.Maintenance.target_km <= km_max)
    if worked_by_id:
        # Bu bakımda en az bir maddeyi tamamlayan (ya da atanan) personel
        sub = (
            db.query(models.MaintenanceItem.maintenance_id)
            .filter(models.MaintenanceItem.completed_by_id == worked_by_id)
            .subquery()
        )
        q = q.filter(
            (models.Maintenance.assigned_to_id == worked_by_id)
            | (models.Maintenance.id.in_(sub))
        )
    col = {
        "completed": models.Maintenance.completed_at,
        "started": models.Maintenance.started_at,
    }.get(date_field, models.Maintenance.created_at)
    df = _parse_date(date_from)
    dt_ = _parse_date(date_to, end=True)
    if df is not None:
        q = q.filter(col >= df)
    if dt_ is not None:
        q = q.filter(col <= dt_)
    return q.order_by(col.desc(), models.Maintenance.created_at.desc())


@app.get("/api/maintenances", response_model=list[schemas.MaintenanceOut])
def list_maintenances(
    status: str | None = None,
    vehicle_id: int | None = None,
    plan_id: int | None = None,
    worked_by_id: int | None = None,
    date_field: str = "created",
    date_from: str | None = None,
    date_to: str | None = None,
    km_min: int | None = None,
    km_max: int | None = None,
    vehicle_model: str | None = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Bakım iş emirleri, zengin filtrelerle (Faz 13-14): durum, araç, plan,
    personel, tarih aralığı (oluşturma/başlangıç/bitiş), hedef km aralığı ve
    araç tipi (Sirio / Bozankaya Tip-1 / Tip-2 vb. — bkz. /vehicle-models)."""
    if not services.has_permission(db, user, "view_maintenance"):
        raise HTTPException(403, "Bakımları görüntüleme yetkiniz yok")
    q = _filtered_maintenance_query(
        db, user, status, vehicle_id, plan_id, worked_by_id,
        date_field, date_from, date_to, km_min, km_max, vehicle_model,
    )
    rows = q.all()
    for m in rows:
        m.covered_tiers = services.compute_covered_tiers(m)
    return rows


@app.get("/api/maintenances/pending-start", response_model=list[schemas.MaintenanceOut])
def list_pending_start_maintenances(
    db: Session = Depends(get_db),
    actor=Depends(require_permission("start_maintenance")),
):
    """Faz 26 — Bakım başlatma onayı: otomatik-açma tarafından oluşturulmuş
    ama admin/mühendis henüz "Başlat" demediği için Bakımlar listesinde,
    Takvim'de ve teknisyen ekranında GÖRÜNMEYEN aday iş emirleri. Yalnızca
    start_maintenance yetkisi olan roller (admin her zaman, varsayılan
    olarak mühendis/amir) görebilir."""
    rows = (
        db.query(models.Maintenance)
        .filter(models.Maintenance.is_activated == False)  # noqa: E712
        .order_by(models.Maintenance.created_at.desc())
        .all()
    )
    for m in rows:
        m.covered_tiers = services.compute_covered_tiers(m)
    return rows


@app.post("/api/maintenances/plan-upcoming", response_model=schemas.MaintenanceOut)
def plan_upcoming_maintenance(
    body: schemas.MaintenancePlanUpcomingIn,
    db: Session = Depends(get_db),
    actor=Depends(require_permission("start_maintenance")),
):
    """Yaklaşan/gecikmiş bir bakımı planlama kuyruğuna alır.

    Araçlar ve Bakımlar ekranındaki akış kartları aynı endpoint'i kullanır;
    kayıt oluşturulduktan sonra normal Başlat/Zamanla akışına düşer.
    """
    vehicle = db.get(models.Vehicle, body.vehicle_id)
    if not vehicle:
        raise HTTPException(404, "Araç bulunamadı")
    try:
        m = services.queue_upcoming_maintenance(db, vehicle, body.target_km)
    except ValueError as e:
        raise HTTPException(400, str(e))
    services.log_action(
        db, actor, "plan", "maintenance", m.id,
        f"Bakım planlama kuyruğuna alındı: {m.vehicle.code} — {m.plan.name}",
        detail=f"Hedef {fmt_km(m.target_km)} km",
    )
    db.refresh(m)
    m.covered_tiers = services.compute_covered_tiers(m)
    return m


@app.post("/api/maintenances/{mid}/start", response_model=schemas.MaintenanceOut)
def start_maintenance(
    mid: int,
    body: schemas.MaintenanceStartIn = schemas.MaintenanceStartIn(),
    db: Session = Depends(get_db),
    actor=Depends(require_permission("start_maintenance")),
):
    """Faz 26: otomatik açılan bir aday iş emrini onaylayıp devreye alır —
    bundan sonra Bakımlar listesinde, Takvim'de, Dashboard'da, Alarm
    Merkezi'nde ve teknisyen ekranında normal şekilde görünür.

    body.scheduled_start_at bugünden ileri bir tarihse HEMEN başlatmaz —
    yalnızca o tarihi kaydeder, gerçek başlatma arka plan zamanlayıcısı
    tarafından o tarih geldiğinde otomatik yapılır (bkz.
    services.run_scheduled_maintenance_starts). Tarih boşsa veya bugüne
    eşit/öncesiyse eski davranış (anında başlatma) uygulanır."""
    m = db.get(models.Maintenance, mid)
    if not m:
        raise HTTPException(404, "Bakım bulunamadı")
    if m.is_activated:
        raise HTTPException(400, "Bu bakım zaten başlatılmış")
    hedef_txt = (
        f"Hedef tarih {m.target_date.strftime('%d.%m.%Y')}" if m.target_date
        else f"Hedef {fmt_km(m.target_km)} km"
    )
    if body.scheduled_start_at and body.scheduled_start_at > date.today():
        services.schedule_maintenance_start(db, m, body.scheduled_start_at)
        services.log_action(
            db, actor, "schedule_start", "maintenance", m.id,
            f"Bakım başlatma zamanlandı: {m.vehicle.code} — {m.plan.name} "
            f"({body.scheduled_start_at.strftime('%d.%m.%Y')})",
            detail=hedef_txt,
        )
        db.refresh(m)
        m.covered_tiers = services.compute_covered_tiers(m)
        return m
    services.activate_maintenance(db, m, actor)
    services.log_action(
        db, actor, "start", "maintenance", m.id,
        f"Otomatik bakım başlatıldı: {m.vehicle.code} — {m.plan.name}",
        detail=hedef_txt,
    )
    db.refresh(m)
    m.covered_tiers = services.compute_covered_tiers(m)
    return m


@app.patch("/api/maintenances/{mid}/planned-date", response_model=schemas.MaintenanceOut)
def set_maintenance_planned_date(
    mid: int,
    body: schemas.MaintenancePlannedDateIn,
    db: Session = Depends(get_db),
    actor=Depends(require_permission("start_maintenance")),
):
    """Takvim'de bir iş emri elle başka bir güne sürüklendiğinde o günü kaydeder.
    body.planned_date null gönderilirse elle planlama kaldırılır ve iş emri
    yeniden aracın hedef km'ye ulaşacağı tahmini güne (frontend hesabı) döner."""
    m = db.get(models.Maintenance, mid)
    if not m:
        raise HTTPException(404, "Bakım bulunamadı")
    old = m.planned_date
    m.planned_date = body.planned_date
    db.commit()
    services.log_action(
        db, actor, "planned_date", "maintenance", m.id,
        f"Takvim planı güncellendi: {m.vehicle.code} — {m.plan.name}",
        old_value=old.strftime("%d.%m.%Y") if old else "",
        new_value=body.planned_date.strftime("%d.%m.%Y") if body.planned_date else "(kaldırıldı)",
    )
    db.refresh(m)
    m.covered_tiers = services.compute_covered_tiers(m)
    return m


@app.post("/api/maintenances/{mid}/cancel-schedule", response_model=schemas.MaintenanceOut)
def cancel_maintenance_schedule(
    mid: int,
    db: Session = Depends(get_db),
    actor=Depends(require_permission("start_maintenance")),
):
    """Daha önce "Başlat" ile girilmiş bir zamanlanmış başlangıç tarihini
    iptal eder — iş emri "Başlatılmayı Bekleyenler" listesinde zamansız
    aday olarak kalmaya devam eder, tekrar tarih seçilebilir veya hemen
    başlatılabilir."""
    m = db.get(models.Maintenance, mid)
    if not m:
        raise HTTPException(404, "Bakım bulunamadı")
    if m.is_activated:
        raise HTTPException(400, "Bu bakım zaten başlatılmış")
    if not m.scheduled_start_at:
        raise HTTPException(400, "Bu bakım için bir zamanlama yok")
    services.log_action(
        db, actor, "cancel_scheduled_start", "maintenance", m.id,
        f"Bakım başlatma zamanlaması iptal edildi: {m.vehicle.code} — {m.plan.name}",
    )
    services.cancel_scheduled_maintenance_start(db, m)
    db.refresh(m)
    m.covered_tiers = services.compute_covered_tiers(m)
    return m


@app.post("/api/maintenances/manual", response_model=schemas.MaintenanceOut)
def create_manual_maintenance(
    body: schemas.MaintenanceManualCreate,
    db: Session = Depends(get_db),
    actor=Depends(require_permission("create_maintenance")),
):
    """Faz 28: otomatik tespiti beklemeden araç + periyot + hedef (km ya da
    tarih, plan türüne göre) seçerek elle bir bakım iş emri açar. Otomatik
    açılan adayların aksine doğrudan aktif olarak oluşur — "Başlat" onayı
    gerekmez."""
    vehicle = db.get(models.Vehicle, body.vehicle_id)
    if not vehicle:
        raise HTTPException(404, "Araç bulunamadı")
    plan = db.get(models.MaintenancePlan, body.plan_id)
    if not plan:
        raise HTTPException(404, "Bakım planı bulunamadı")
    try:
        if plan.plan_type == "time":
            if not body.target_date:
                raise HTTPException(400, "Bu plan süre bazlı — target_date belirtilmeli")
            m = services.create_manual_time_maintenance(db, vehicle, plan, body.target_date, actor)
            hedef_txt = f"Hedef tarih {body.target_date.strftime('%d.%m.%Y')}"
        else:
            if not body.target_km:
                raise HTTPException(400, "Bu plan km bazlı — target_km belirtilmeli")
            m = services.create_manual_maintenance(db, vehicle, plan, body.target_km, actor)
            hedef_txt = f"Hedef {fmt_km(body.target_km)} km"
    except ValueError as e:
        raise HTTPException(400, str(e))
    services.log_action(
        db, actor, "create_manual", "maintenance", m.id,
        f"Manuel bakım iş emri oluşturuldu: {vehicle.code} — {plan.name}",
        detail=hedef_txt,
    )
    db.refresh(m)
    # covered_tiers gerçek bir DB sütunu değil, MaintenanceOut'ta hesaplanan
    # bir alan — get_maintenance/list_maintenances'ta olduğu gibi burada da
    # döndürmeden önce elle hesaplanıp iliştirilmeli, yoksa manuel oluşturma
    # yanıtında (sayfa yeniden yüklenene kadar) boş [] görünür.
    m.covered_tiers = services.compute_covered_tiers(m)
    return m


_EXPORT_FORMATS = {"xlsx", "csv", "pdf", "docx"}
_EXPORT_FORMAT_TR = {"xlsx": "Excel", "csv": "CSV", "pdf": "PDF", "docx": "Word"}


@app.get("/api/maintenances/export")
def export_maintenances(
    format: str = "xlsx",
    status: str | None = None,
    vehicle_id: int | None = None,
    plan_id: int | None = None,
    worked_by_id: int | None = None,
    date_field: str = "created",
    date_from: str | None = None,
    date_to: str | None = None,
    km_min: int | None = None,
    km_max: int | None = None,
    vehicle_model: str | None = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Filtrelenmiş bakım listesini Excel/PDF/CSV/Word olarak indirir (Faz 16,
    Faz 22 çoklu format desteği). Bakımlar ve Bakım Geçmişi sayfalarındaki
    aktif filtreler (araç tipi dahil) aynen uygulanır."""
    if not services.has_permission(db, user, "export_excel"):
        raise HTTPException(403, "Dışa aktarma yetkiniz yok")
    fmt = (format or "xlsx").lower()
    if fmt not in _EXPORT_FORMATS:
        raise HTTPException(400, "Geçersiz format (xlsx, csv, pdf, docx desteklenir)")
    q = _filtered_maintenance_query(
        db, user, status, vehicle_id, plan_id, worked_by_id,
        date_field, date_from, date_to, km_min, km_max, vehicle_model,
    )
    rows = q.all()
    title = "Bakım Geçmişi" if status == "completed" else "Bakımlar"
    content = export_utils.build_maintenances_export(rows, fmt, title)
    services.log_action(
        db, user, "export", "maintenance", None,
        f"{title} {_EXPORT_FORMAT_TR[fmt]}'e aktarıldı ({len(rows)} kayıt)",
    )
    base = "bakim_gecmisi" if status == "completed" else "bakimlar"
    fname = export_utils.filename_for(base, fmt)
    return Response(
        content=content,
        media_type=export_utils.MEDIA_TYPES[fmt],
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


@app.get("/api/maintenances/projections", response_model=list[schemas.CalendarProjectionItem])
def get_maintenance_projections(
    db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    """Takvim görünümü — her aracın bir sonraki bakım hedefine ait -%5/+%10
    önizleme penceresini, km bazlı planlarda günde ortalama 300 km ilerleme
    varsayımıyla, süre bazlı (aylık/yıllık) planlarda doğrudan takvim
    tarihiyle hesaplar. Tamamlanmış hedefler bir daha döndürülmez.

    DÜZELTME: önceden yalnızca km bazlı planlar hesaplanıyordu — süre bazlı
    bir planı olan araçlar Takvim'de hiç görünmüyordu (bkz.
    services.compute_time_maintenance_calendar_projections docstring'i)."""
    if not services.has_permission(db, user, "view_maintenance"):
        raise HTTPException(403, "Bakımları görüntüleme yetkiniz yok")
    vehicles = db.query(models.Vehicle).all()
    items = services.compute_maintenance_calendar_projections(db, vehicles)
    items += services.compute_time_maintenance_calendar_projections(db, vehicles)
    if user.role == "technician":
        items = [
            it for it in items
            if it["maintenance_id"] is None
            or db.get(models.Maintenance, it["maintenance_id"]).assigned_to_id in (None, user.id)
        ]
    return [schemas.CalendarProjectionItem(**it) for it in items]


@app.get("/api/maintenances/{mid}", response_model=schemas.MaintenanceDetail)
def get_maintenance(
    mid: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    if not services.has_permission(db, user, "view_maintenance"):
        raise HTTPException(403, "Bakımları görüntüleme yetkiniz yok")
    m = db.get(models.Maintenance, mid)
    if not m:
        raise HTTPException(404, "Bakım bulunamadı")
    # Faz 26: henüz "Başlat" denmemiş bir aday, başlatma yetkisi olmayan
    # (ör. teknisyen) kullanıcılar için hiç var olmamış gibi davranır — id
    # tahmin edilerek doğrudan erişilmesini de engeller.
    if not m.is_activated and not services.has_permission(db, user, "start_maintenance"):
        raise HTTPException(404, "Bakım bulunamadı")
    if user.role == "technician" and m.assigned_to_id not in (None, user.id):
        raise HTTPException(403, "Bu bakım size atanmamış")
    services.sync_open_maintenance_trees(db)
    detail = schemas.MaintenanceDetail.model_validate(m)
    detail.items = _build_item_tree(m.items, db, vehicle_model=m.vehicle.model)
    detail.covered_tiers = services.compute_covered_tiers(m)
    return detail


@app.post("/api/maintenances/{mid}/resync-tiers")
def resync_maintenance_tiers(
    mid: int,
    db: Session = Depends(get_db),
    actor=Depends(require_permission("create_maintenance")),
):
    """Bir iş emrinin hedef km'sinin kapsadığı ama (eski bir hatadan dolayı
    veya sonradan yeni bir plan eklendiği için) checklist'te eksik kalmış
    periyotların maddelerini sonradan ekler — bkz. services.resync_maintenance_tiers.
    Tamamlanmış maddelere dokunmaz, yalnızca eksik düğümleri ekler."""
    m = db.get(models.Maintenance, mid)
    if not m:
        raise HTTPException(404, "Bakım bulunamadı")
    try:
        result = services.resync_maintenance_tiers(db, m)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if result["added_items"]:
        services.log_action(
            db, actor, "resync_tiers", "maintenance", m.id,
            f"İş Emri #{m.id}: eksik periyot(lar) eklendi ({', '.join(fmt_km(k) for k in result['merged_periods'])} km), "
            f"{result['added_items']} yeni madde.",
        )
    return result


@app.get("/api/maintenances/{mid}/export-items")
def export_maintenance_items(
    mid: int,
    format: str = "xlsx",
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Tek bir iş emrinin tüm kontrol maddelerini (kim/ne zaman tamamladı,
    varsa hangi notu ekledi) Excel/CSV/PDF/Word olarak indirir — Bakımlar
    sayfasındaki iş emri detayında "Excel'e Aktar" butonu için."""
    if not services.has_permission(db, user, "export_excel"):
        raise HTTPException(403, "Dışa aktarma yetkiniz yok")
    m = db.get(models.Maintenance, mid)
    if not m:
        raise HTTPException(404, "Bakım bulunamadı")
    fmt = (format or "xlsx").lower()
    if fmt not in _EXPORT_FORMATS:
        raise HTTPException(400, "Geçersiz format (xlsx, csv, pdf, docx desteklenir)")
    items = _build_item_tree(m.items, db, vehicle_model=m.vehicle.model)
    title = f"İş Emri #{m.id} — {m.vehicle.code} — {m.plan.name}"
    content = export_utils.build_item_completion_export(
        m.id, m.vehicle.code, m.plan.name, items, fmt, title,
    )
    services.log_action(
        db, user, "export", "maintenance", m.id,
        f"İş Emri #{m.id} madde tamamlama {_EXPORT_FORMAT_TR[fmt]}'e aktarıldı",
    )
    fname = export_utils.filename_for(f"is_emri_{m.id}_maddeler", fmt)
    return Response(
        content=content,
        media_type=export_utils.MEDIA_TYPES[fmt],
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


@app.delete("/api/maintenances/all")
def delete_all_maintenances(
    db: Session = Depends(get_db),
    actor=Depends(require_permission("delete_all_maintenance")),
):
    """Faz 27 — Tehlikeli Bölge: sistemdeki TÜM bakım iş emirlerini (Tamamlanmış
    geçmiş kayıtlar DAHİL) kalıcı olarak siler. Tek iş emri silmenin aksine
    (delete_maintenance) burada "tamamlanmış olduğu için silinemez" kısıtı
    YOKTUR — kullanıcı bilerek tüm geçmişi temizlemeyi istemiştir. Geri
    alınamaz. SQLite'ta FK cascade etkin olmadığından alt tablolar (madde/
    not) önce, ana tablo (maintenances) sonra ayrı ayrı toplu silinir."""
    total = db.query(models.Maintenance).count()
    db.query(models.MaintenanceItem).delete(synchronize_session=False)
    db.query(models.MaintenanceNote).delete(synchronize_session=False)
    db.query(models.Maintenance).delete(synchronize_session=False)
    db.commit()
    services.log_action(
        db, actor, "delete_all", "maintenance", None,
        f"TÜM bakım iş emirleri silindi ({total} kayıt, tamamlananlar dahil)",
    )
    return {"ok": True, "deleted": total}


@app.delete("/api/maintenances/{mid}")
def delete_maintenance(
    mid: int,
    db: Session = Depends(get_db),
    actor=Depends(require_permission("delete_maintenance")),
):
    """Yetki Yönetimi — 'Silebilir' izni. Bir iş emrini ve tüm kontrol
    maddelerini kalıcı olarak siler. Geri alınamaz; tamamlanmış (Tamamlandı)
    iş emirleri geçmiş kaydı olduğu için silinemez — önce Admin'in planı/
    araç geçmişini bozmaması için durum kontrolü yapılır."""
    m = db.get(models.Maintenance, mid)
    if not m:
        raise HTTPException(404, "Bakım bulunamadı")
    if m.status == "completed":
        raise HTTPException(400, "Tamamlanmış bir iş emri silinemez")
    summary = f"İş emri silindi: {m.vehicle.code} — {m.plan.name} (#{m.id})"
    services.log_action(db, actor, "delete", "maintenance", m.id, summary, commit=False)
    db.delete(m)
    db.commit()
    return {"ok": True}


def _parse_tier_kms(raw: str | None) -> list[int]:
    if not raw:
        return []
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return sorted(out)


def _build_item_tree(
    items,
    db: Session | None = None,
    vehicle_model: str | None = None,
) -> list[schemas.ItemOut]:
    # Faz 10: her maddenin bağlı olduğu şablon düğümünün (source_node_id)
    # not ve eklerini tek seferde topla.
    notes: dict[int, str] = {}
    card_codes: dict[int, str] = {}
    attach: dict[int, list[schemas.NodeAttachmentOut]] = {}
    # Faz 21: bakım SIRASINDA eklenen fotoğraflar (item_id — şablon değil,
    # doğrudan bu iş emrinin maddesi) — aynı toplu-sorgu deseni.
    exec_attach: dict[int, list[schemas.ItemExecAttachmentOut]] = {}
    if db is not None:
        source_ids = [i.source_node_id for i in items if i.source_node_id is not None]
        if source_ids:
            nodes = (
                db.query(models.TreeNode)
                .filter(models.TreeNode.id.in_(set(source_ids)))
                .all()
            )
            for n in nodes:
                if n.note:
                    notes[n.id] = n.note
                if n.card_code:
                    card_codes[n.id] = n.card_code
            attachment_node_ids = set(source_ids)
            source_paths: dict[int, tuple[str, ...]] = {}
            if vehicle_model is not None:
                # A component can be represented by a separate TreeNode in
                # every maintenance-period plan. Share its reference files
                # across those plans only when both vehicle model and full
                # component path match.
                all_nodes = db.query(models.TreeNode).all()
                nodes_by_id = {node.id: node for node in all_nodes}
                candidate_plan_ids = {
                    p.id
                    for p in db.query(models.MaintenancePlan).all()
                    if services._model_matches(p.vehicle_model, vehicle_model)
                }
                root_plan_ids: dict[int, int | None] = {}

                def get_root_plan_id(node: models.TreeNode) -> int | None:
                    trail: list[models.TreeNode] = []
                    current = node
                    while current.id not in root_plan_ids:
                        trail.append(current)
                        if current.parent_id is None:
                            root_plan_ids[current.id] = current.plan_id
                            break
                        parent = nodes_by_id.get(current.parent_id)
                        if parent is None:
                            root_plan_ids[current.id] = None
                            break
                        current = parent
                    root_plan_id = root_plan_ids.get(current.id)
                    for ancestor in trail:
                        root_plan_ids[ancestor.id] = root_plan_id
                    return root_plan_id

                def get_node_path(node: models.TreeNode) -> tuple[str, ...]:
                    path: list[str] = []
                    current: models.TreeNode | None = node
                    while current is not None:
                        path.append(current.name)
                        current = nodes_by_id.get(current.parent_id) if current.parent_id is not None else None
                    return tuple(reversed(path))

                candidate_ids_by_path: dict[tuple[str, ...], set[int]] = {}
                for node in all_nodes:
                    root_plan_id = get_root_plan_id(node)
                    if root_plan_id is None or root_plan_id in candidate_plan_ids:
                        candidate_ids_by_path.setdefault(get_node_path(node), set()).add(node.id)
                for node_id in source_ids:
                    source_node = nodes_by_id.get(node_id)
                    if source_node is not None:
                        path = get_node_path(source_node)
                        source_paths[node_id] = path
                        attachment_node_ids.update(candidate_ids_by_path.get(path, ()))

            atts = (
                db.query(models.NodeAttachment)
                .filter(models.NodeAttachment.node_id.in_(attachment_node_ids))
                .order_by(models.NodeAttachment.id)
                .all()
            )
            attachments_by_node: dict[int, list[schemas.NodeAttachmentOut]] = {}
            for a in atts:
                attachments_by_node.setdefault(a.node_id, []).append(
                    schemas.NodeAttachmentOut.model_validate(a)
                )
            if vehicle_model is None:
                attach.update(attachments_by_node)
            else:
                for node_id in source_ids:
                    equivalent_ids = candidate_ids_by_path.get(source_paths.get(node_id, ()), {node_id})
                    attach[node_id] = [
                        attachment
                        for equivalent_id in equivalent_ids
                        for attachment in attachments_by_node.get(equivalent_id, [])
                    ]
        item_ids = [i.id for i in items]
        if item_ids:
            exec_atts = (
                db.query(models.ItemExecAttachment)
                .filter(models.ItemExecAttachment.item_id.in_(item_ids))
                .order_by(models.ItemExecAttachment.id)
                .all()
            )
            for a in exec_atts:
                exec_attach.setdefault(a.item_id, []).append(
                    schemas.ItemExecAttachmentOut.model_validate(a)
                )

    by_id = {i.id: schemas.ItemOut.model_validate(i) for i in items}
    for out in by_id.values():
        out.children = []
    for i in items:
        out = by_id[i.id]
        out.tier_kms = _parse_tier_kms(getattr(i, "tiers", ""))
        out.exec_attachments = exec_attach.get(i.id, [])
        if i.source_node_id is not None:
            out.note = notes.get(i.source_node_id, "")
            out.card_code = card_codes.get(i.source_node_id, "")
            out.attachments = attach.get(i.source_node_id, [])
    roots: list[schemas.ItemOut] = []
    for i in sorted(items, key=lambda x: x.order_index):
        out = by_id[i.id]
        if i.parent_id and i.parent_id in by_id:
            by_id[i.parent_id].children.append(out)
        else:
            roots.append(out)
    return roots


@app.get("/api/users/assignable", response_model=list[schemas.UserOut])
def list_assignable_users(
    shift_id: int | None = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Bakım atarken / paylaşılan tablette 'bu maddeyi kim tikledi' veya
    'notu kim ekledi' seçiminde kullanılan basitleştirilmiş kullanıcı
    listesi. Bilerek 'manage_users' izninden BAĞIMSIZDIR — yalnızca oturum
    açmış olmak yeterlidir (önceden admin/mühendis/amir dışındaki roller
    bu uca erişemiyordu; usta girişinde isim listesi hiç gelmiyordu).

    Görünürlük kuralı: usta girişindeyse yalnızca ustalar arasından seçim
    yapılabilir (bir usta başka bir rolü kendi adına işaretleyemez);
    mühendis/amir/admin girişinde ise (admin hesapları hariç) tüm aktif
    personel listelenir. `shift_id` verilirse (vardiyalı çalışma modunda,
    o an seçili vardiya) yalnızca o vardiyadaki kişilerle daraltılır."""
    q = db.query(models.User).filter(models.User.is_active == True)  # noqa: E712
    if user.role == "technician":
        q = q.filter(models.User.role == "technician")
    else:
        q = q.filter(models.User.role != "admin")
    if shift_id is not None:
        q = q.filter(models.User.shift_id == shift_id)
    return q.order_by(models.User.full_name).all()


# ================================================================ Vardiyalar
@app.get("/api/shifts", response_model=list[schemas.ShiftOut])
def list_shifts(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return db.query(models.Shift).order_by(models.Shift.order_index).all()


@app.post("/api/shifts", response_model=schemas.ShiftOut)
def create_shift(
    body: schemas.ShiftCreate,
    db: Session = Depends(get_db),
    actor=Depends(require_permission("manage_users")),
):
    """Yeni bir vardiya açar (ör. "Vardiya 4", "Gece Vardiyası") — üye
    ataması PATCH /users/{id} ile aynı manage_users yetkisiyle yapılır,
    o yüzden vardiya oluşturma/yeniden adlandırma/silme de aynı yetkiye
    bağlıdır."""
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Vardiya adı boş olamaz")
    order_index = db.query(models.Shift).count()
    shift = models.Shift(name=name, order_index=order_index)
    db.add(shift)
    db.commit()
    db.refresh(shift)
    services.log_action(db, actor, "create", "shift", shift.id, f"Vardiya oluşturuldu: {shift.name}")
    return shift


@app.patch("/api/shifts/{shift_id}", response_model=schemas.ShiftOut)
def rename_shift(
    shift_id: int,
    body: schemas.ShiftUpdate,
    db: Session = Depends(get_db),
    actor=Depends(require_permission("manage_users")),
):
    """Vardiya adını ve/veya vardiya sorumlusunu günceller.

    Yalnızca gövdede GÖNDERİLEN alanlar değişir — adı değiştirmeden sorumlu
    atamak (ya da tersi) mümkündür. Vardiya sorumlusu, o vardiyanın kendi
    üyesi olmak zorundadır: yalnızca bu kişi başkaları adına madde
    tikleyebilir (bkz. models.Shift.supervisor_id)."""
    shift = db.get(models.Shift, shift_id)
    if not shift:
        raise HTTPException(404, "Vardiya bulunamadı")

    if "name" in body.model_fields_set and body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "Vardiya adı boş olamaz")
        old_name = shift.name
        if name != old_name:
            shift.name = name
            db.commit()
            services.log_action(
                db, actor, "update", "shift", shift.id,
                f"Vardiya adı değiştirildi: {old_name} → {shift.name}",
            )

    if "supervisor_id" in body.model_fields_set:
        old_sup = shift.supervisor.full_name if shift.supervisor else "(yok)"
        if body.supervisor_id is None:
            shift.supervisor_id = None
            new_sup = "(yok)"
        else:
            chosen = db.get(models.User, body.supervisor_id)
            if not chosen or not chosen.is_active:
                raise HTTPException(400, "Geçersiz kullanıcı seçimi")
            if chosen.shift_id != shift.id:
                raise HTTPException(
                    400,
                    f"{chosen.full_name} bu vardiyanın üyesi değil — önce vardiyaya ekleyin.",
                )
            shift.supervisor_id = chosen.id
            new_sup = chosen.full_name
        db.commit()
        services.log_action(
            db, actor, "update", "shift", shift.id,
            f"Vardiya sorumlusu değiştirildi ({shift.name}): {old_sup} → {new_sup}",
        )

    db.refresh(shift)
    return shift


@app.delete("/api/shifts/{shift_id}")
def delete_shift(
    shift_id: int,
    db: Session = Depends(get_db),
    actor=Depends(require_permission("manage_users")),
):
    """Vardiyayı kalıcı olarak siler. Bu vardiyaya atanmış kullanıcıların
    yalnızca "hangi vardiyada" bilgisi sıfırlanır (kendileri silinmez).
    Son kalan vardiya silinemez — aksi halde vardiyalı çalışma modu
    açıkken seçilecek hiçbir vardiya kalmaz."""
    shift = db.get(models.Shift, shift_id)
    if not shift:
        raise HTTPException(404, "Vardiya bulunamadı")
    if db.query(models.Shift).count() <= 1:
        raise HTTPException(400, "Son vardiya silinemez")
    db.query(models.User).filter(models.User.shift_id == shift_id).update({models.User.shift_id: None})
    db.query(models.Maintenance).filter(models.Maintenance.assigned_shift_id == shift_id).update(
        {models.Maintenance.assigned_shift_id: None}
    )
    name = shift.name
    db.delete(shift)
    db.commit()
    services.log_action(db, actor, "delete", "shift", shift_id, f"Vardiya silindi: {name}")
    return {"ok": True}


@app.get("/api/shift-mode", response_model=schemas.ShiftModeConfig)
def get_shift_mode(db: Session = Depends(get_db), _=Depends(get_current_user)):
    return schemas.ShiftModeConfig(enabled=services.get_shift_mode(db))


@app.patch("/api/shift-mode", response_model=schemas.ShiftModeConfig)
def update_shift_mode(
    body: schemas.ShiftModeUpdate,
    db: Session = Depends(get_db),
    actor: models.User = Depends(require_staff),
):
    """Vardiyalı çalışma modunu açar/kapatır — admin/mühendis/amir yetkisi
    gerektirir (paylaşılan tabletteki genel çalışma biçimini belirlediği
    için ustaya bırakılmaz)."""
    services.set_shift_mode(db, body.enabled)
    services.log_action(
        db, actor, "update", "setting", None,
        f"Vardiyalı çalışma modu {'açıldı' if body.enabled else 'kapatıldı'}",
    )
    return schemas.ShiftModeConfig(enabled=body.enabled)


# ================================================================ Öneriler (Faz 30 — hiyerarşik onay)
_SUGGESTION_STATUS_TR = {
    "pending": "Beklemede",
    # Yalnızca Faz 30'daki iki aşamalı zincirden kalan ESKİ kayıtlarda
    # görülebilir; yeni önerilerde üretilmez (bkz. migrate.py) ve arayüzde
    # normal "Beklemede" gibi ele alınır.
    "pending_engineer": "Beklemede",
    "rejected": "Reddedildi",
    "approved": "Onaylandı",
    # Artık işaretlenemez ("Bakım ağacına ekle" kaldırıldı) — yalnızca eski
    # kayıtlar için etiket olarak duruyor.
    "added": "Ağaca Eklendi",
}


@app.get("/api/suggestions", response_model=list[schemas.SuggestionOut])
def list_suggestions(
    status: str | None = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Şeffaflık için HERKES (rolü ne olursa olsun) TÜM iyileştirme
    taleplerini görür — yalnızca değerlendirme (onayla/reddet) yetkisi role
    göre kısıtlıdır (bkz. review_suggestion).

    Not: kurumsal adlandırma "İyileştirme Talebi"dir; API yolu, tablo ve
    şema adları geriye dönük uyumluluk için 'suggestion' olarak kalmıştır."""
    q = db.query(models.Suggestion)
    if status:
        q = q.filter(models.Suggestion.status == status)
    return q.order_by(models.Suggestion.created_at.desc()).all()


@app.post("/api/suggestions", response_model=schemas.SuggestionOut)
def create_suggestion(
    body: schemas.SuggestionCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Usta/mühendis/amir (ve admin), sahada fark ettiği bir eksikliği/
    iyileştirmeyi bir ARAÇ MODELİ (Bozankaya/Sirio vb. — belirli bir araç
    değil, o modeldeki TÜM araçları ilgilendirir) ve (varsa) bir bakım
    periyoduyla ilişkilendirip bildirir. Faz 31: onay artık TEK aşamalı —
    öneri 'pending' olarak girer ve yetkili rollerden biri (mühendis/amir/
    admin) doğrudan onaylar ya da reddeder."""
    vehicle_model = body.vehicle_model.strip()
    if not vehicle_model:
        raise HTTPException(400, "Araç modeli seçilmelidir")
    if body.plan_id is not None and not db.get(models.MaintenancePlan, body.plan_id):
        raise HTTPException(404, "Bakım periyodu bulunamadı")
    sug = models.Suggestion(
        vehicle_model=vehicle_model,
        plan_id=body.plan_id,
        text=body.text.strip(),
        status="pending",
        created_by_id=user.id,
    )
    db.add(sug)
    db.commit()
    db.refresh(sug)
    services.log_action(
        db, user, "create", "suggestion", sug.id,
        f"{vehicle_model} için yeni iyileştirme talebi",
        body.text.strip()[:280],
    )
    return sug


@app.patch("/api/suggestions/{suggestion_id}", response_model=schemas.SuggestionOut)
def review_suggestion(
    suggestion_id: int,
    body: schemas.SuggestionReviewBody,
    db: Session = Depends(get_db),
    actor: models.User = Depends(get_current_user),
):
    """Faz 31 — TEK AŞAMALI onay. Faz 30'daki amir -> mühendis zinciri
    kaldırıldı: yetkili rollerden (mühendis / amir / admin) herhangi biri
    öneriyi doğrudan onaylayabilir ya da reddedebilir ve verilen ilk karar
    NİHAİDİR. 'added' ("Bakım ağacına eklendi") kararı tamamen kaldırıldı —
    bir öneriyi onaylamak, ağacın kendiliğinden değişmesi anlamına gelmez;
    ağaç düzenlemesi ayrı ve bilinçli bir işlemdir."""
    sug = db.get(models.Suggestion, suggestion_id)
    if not sug:
        raise HTTPException(404, "Talep bulunamadı")
    if not services.can_review_suggestion(actor):
        raise HTTPException(403, "Talepleri yalnızca mühendis, amir veya admin değerlendirebilir")
    if not services.suggestion_is_open(sug):
        raise HTTPException(400, "Bu talep zaten sonuçlandırılmış, yeniden değerlendirilemez")
    services.apply_suggestion_review(db, sug, actor, body.status, body.admin_note)
    db.refresh(sug)
    services.log_action(
        db, actor, "update", "suggestion", sug.id,
        f"Talep #{sug.id}: {_SUGGESTION_STATUS_TR.get(body.status, body.status)}",
        body.admin_note.strip()[:280],
    )
    return sug


@app.delete("/api/suggestions/{suggestion_id}")
def delete_suggestion(
    suggestion_id: int,
    db: Session = Depends(get_db),
    actor: models.User = Depends(get_current_user),
):
    """Talebi kalıcı olarak siler — ya değerlendirme yetkisi olan bir rol
    (admin/amir/mühendis) ya da talebi gönderen kişinin kendisi silebilir."""
    sug = db.get(models.Suggestion, suggestion_id)
    if not sug:
        raise HTTPException(404, "Talep bulunamadı")
    if not (services.can_review_suggestion(actor) or sug.created_by_id == actor.id):
        raise HTTPException(403, "Bu işlem için yetkiniz yok")
    db.delete(sug)
    db.commit()
    services.log_action(db, actor, "delete", "suggestion", suggestion_id, f"Talep #{suggestion_id} silindi")
    return {"ok": True}


@app.patch("/api/maintenances/{mid}/assign", response_model=schemas.MaintenanceOut)
def assign_maintenance(
    mid: int,
    body: schemas.AssignBody,
    db: Session = Depends(get_db),
    actor: models.User = Depends(get_current_user),
):
    m = db.get(models.Maintenance, mid)
    if not m:
        raise HTTPException(404, "Bakım bulunamadı")
    if actor.role == "technician":
        # Usta serbestçe atama yapamaz; yalnızca ATANMAMIŞ bir işi kendi
        # üzerine alabilir ("Bana Ata") veya kendi üzerindeki atamayı
        # bırakabilir ("Bırak"). Başkasına atama/başkasının atamasını
        # değiştirme admin/mühendise özeldir.
        claiming_self = body.assigned_to_id == actor.id and m.assigned_to_id is None
        releasing_self = body.assigned_to_id is None and m.assigned_to_id == actor.id
        if not (claiming_self or releasing_self):
            raise HTTPException(403, "Bu işlem için yetkiniz yok")
    elif actor.role not in ("admin", "engineer", "amir"):
        raise HTTPException(403, "Bu işlem için yetkiniz yok")
    old_target = m.assigned_to.full_name if m.assigned_to else "(atanmamış)"
    m.assigned_to_id = body.assigned_to_id
    db.commit()
    db.refresh(m)
    target = m.assigned_to.full_name if m.assigned_to else "(atama kaldırıldı)"
    services.log_action(
        db, actor, "update", "maintenance", m.id,
        f"Bakım atandı: {m.vehicle.code} — {m.plan.name} → {target}",
        old_value=old_target, new_value=target,
    )
    m.covered_tiers = services.compute_covered_tiers(m)
    return m


@app.patch("/api/maintenances/{mid}/assign-shift", response_model=schemas.MaintenanceOut)
def assign_maintenance_shift(
    mid: int,
    body: schemas.AssignShiftBody,
    db: Session = Depends(get_db),
    actor: models.User = Depends(get_current_user),
):
    """İş emrini bir vardiyaya atar/vardiyadan çıkarır — "Atanan" (kişi,
    /assign) alanından bağımsızdır (bkz. models.Maintenance.assigned_shift_id
    yorumu). Bakımlar sayfasındaki vardiya barında seçili vardiyaya hızlıca
    iş emri atamak için kullanılır. Kişi ataması gibi ustanın kendi kendine
    alma/bırakma senaryosu yoktur — dispatch/planlama işlevi olduğu için
    yalnızca admin/mühendis/amir kullanabilir."""
    m = db.get(models.Maintenance, mid)
    if not m:
        raise HTTPException(404, "Bakım bulunamadı")
    if actor.role not in ("admin", "engineer", "amir"):
        raise HTTPException(403, "Bu işlem için yetkiniz yok")
    if body.assigned_shift_id is not None:
        shift = db.get(models.Shift, body.assigned_shift_id)
        if not shift:
            raise HTTPException(404, "Vardiya bulunamadı")
    old_target = m.assigned_shift.name if m.assigned_shift else "(atanmamış)"
    m.assigned_shift_id = body.assigned_shift_id
    db.commit()
    db.refresh(m)
    target = m.assigned_shift.name if m.assigned_shift else "(atama kaldırıldı)"
    services.log_action(
        db, actor, "update", "maintenance", m.id,
        f"Bakım vardiyaya atandı: {m.vehicle.code} — {m.plan.name} → {target}",
        old_value=old_target, new_value=target,
    )
    m.covered_tiers = services.compute_covered_tiers(m)
    return m


@app.patch("/api/maintenances/{mid}/completed-km", response_model=schemas.MaintenanceOut)
def set_maintenance_completed_km(
    mid: int,
    body: schemas.MaintenanceCompletedKmIn,
    db: Session = Depends(get_db),
    actor: models.User = Depends(get_current_user),
):
    """Kullanıcı isteği: "bakım yapılırken bakım yapan usta veya kişi
    tarafından o anki güncel km girme olsun." Usta/teknisyen (kendine
    atanmışsa ya da atanmamışsa) bakım sırasında aracın o anki gerçek km'sini
    girer. Bu değer hem hedef km'den (m.target_km) planın periyoduna oranlı
    sapma yüzdesini hesaplamak için kaydedilir (bkz.
    services.compute_km_deviation_pct — Bakım Geçmişi sayfası ve dışa
    aktarımlarda "Sapma %" olarak gösterilir) hem de — girilen değer aracın
    şu anki takip edilen km'sinden büyükse — normal km güncellemesiyle
    (PUT /vehicles/{id}/km) BİREBİR aynı yan etkilerle (yeni adayların
    otomatik açılması dahil) aracın current_km'sini ileri taşır."""
    m = db.get(models.Maintenance, mid)
    if not m:
        raise HTTPException(404, "Bakım bulunamadı")
    if actor.role == "technician" and m.assigned_to_id not in (None, actor.id):
        raise HTTPException(403, "Bu işlem için yetkiniz yok")
    if m.plan.plan_type != "km":
        raise HTTPException(400, "Bu alan yalnızca km bazlı bakımlarda kullanılabilir")
    v = m.vehicle
    if body.completed_km < v.current_km:
        raise HTTPException(
            400,
            f"Girilen km ({fmt_km(body.completed_km)}) aracın şu anki km'sinden "
            f"({fmt_km(v.current_km)}) küçük olamaz — kilometre geriye alınamaz.",
        )
    old_completed_km = m.completed_km
    m.completed_km = body.completed_km
    old_vehicle_km = v.current_km
    opened: list[models.Maintenance] = []
    if body.completed_km > v.current_km:
        v.current_km = body.completed_km
        v.last_km_update_at = datetime.now()
    db.commit()
    services.log_action(
        db, actor, "update", "maintenance", m.id,
        f"Bakım yapılırken güncel km girildi: {v.code} — {m.plan.name}",
        detail=f"Hedef {fmt_km(m.target_km)} km",
        old_value=str(old_completed_km) if old_completed_km is not None else "",
        new_value=str(body.completed_km),
    )
    if v.current_km != old_vehicle_km:
        services.log_action(
            db, actor, "update", "vehicle", v.id,
            f"KM güncellendi: {v.code}",
            detail=f"{fmt_km(old_vehicle_km)} → {fmt_km(v.current_km)} km (bakım tamamlama girişi)",
            old_value=str(old_vehicle_km), new_value=str(v.current_km),
        )
        opened = services.check_and_open_maintenances(db, v)
        _log_opened_maintenances(db, actor, v, opened)
    db.refresh(m)
    m.covered_tiers = services.compute_covered_tiers(m)
    return m


@app.get("/api/maintenances/{mid}/model3d", response_model=list[schemas.Model3DWithStatus])
def get_maintenance_model3d(
    mid: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Bu bakıma ait 3D modelleri, her parçanın (mesh) bağlı olduğu maddenin
    tamamlanma durumuyla birlikte döner (usta ekranındaki tıklanabilir 3D
    tramvay için)."""
    m = db.get(models.Maintenance, mid)
    if not m:
        raise HTTPException(404, "Bakım bulunamadı")
    if user.role == "technician" and m.assigned_to_id not in (None, user.id):
        raise HTTPException(403, "Bu bakım size atanmamış")

    models3d = db.query(models.Model3D).order_by(models.Model3D.order_index).all()
    items_by_source = {
        i.source_node_id: i for i in m.items if i.source_node_id is not None
    }

    result: list[schemas.Model3DWithStatus] = []
    for mo in models3d:
        hotspots = [h for h in mo.hotspots if h.plan_id == m.plan_id]
        statuses: list[schemas.PartHotspotStatus] = []
        for h in hotspots:
            item = items_by_source.get(h.tree_node_id)
            statuses.append(
                schemas.PartHotspotStatus(
                    hotspot=schemas.PartHotspotOut.model_validate(h),
                    is_completed=bool(item and item.is_completed),
                    item_id=item.id if item else None,
                )
            )
        result.append(
            schemas.Model3DWithStatus(
                id=mo.id,
                label=mo.label,
                filename=mo.filename,
                hotspots=statuses,
            )
        )
    return result


@app.post("/api/items/{item_id}/complete")
def complete_item(
    item_id: int,
    body: schemas.CompleteItemBody | None = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    if not services.has_permission(db, user, "edit_maintenance"):
        raise HTTPException(403, "Bakım maddelerini işaretleme yetkiniz yok")
    item = db.get(models.MaintenanceItem, item_id)
    if not item:
        raise HTTPException(404, "Madde bulunamadı")
    m = item.maintenance
    if user.role == "technician" and m.assigned_to_id not in (None, user.id):
        raise HTTPException(403, "Bu bakım size atanmamış")

    # Vardiya koruması (kullanıcı isteği): bir iş emri BİR VARDİYAYA atanmışsa,
    # o vardiyada olmayan biri iş emrini açıp yanlışlıkla madde tikleyemesin.
    # Yönetici/mühendis/amir muaftır — hatalı tikleri düzeltmek ya da vardiya
    # dışı müdahale etmek zorunda kalabilirler. Kural yalnızca vardiyalı
    # çalışma AÇIKKEN uygulanır; kapalıyken eski davranış korunur (eskiden
    # atanmış ama artık kullanılmayan vardiyalar kimseyi kilitlemesin).
    shift_mode_on = services.get_shift_mode(db)
    is_staff = user.role in ("admin", "engineer", "amir")
    shift_guard = shift_mode_on and m.assigned_shift_id is not None and not is_staff
    if shift_guard and user.shift_id != m.assigned_shift_id:
        shift_name = m.assigned_shift.name if m.assigned_shift else "başka bir"
        raise HTTPException(
            403,
            f"Bu iş emri {shift_name} vardiyasına atanmış — kendi vardiyanız "
            "olmadığı için madde işaretleyemezsiniz.",
        )

    # Paylaşılan tablet senaryosu: tikleyen kişi oturum sahibinden farklı
    # olabilir (tek cihaz, giriş/çıkış yapılmıyor). Belirtilirse geçerli bir
    # aktif kullanıcı olmalı; tamamlama bu kişiye atfedilir.
    completed_by = None
    if body and body.completed_by_id is not None:
        completed_by = db.get(models.User, body.completed_by_id)
        if not completed_by or not completed_by.is_active:
            raise HTTPException(400, "Geçersiz kullanıcı seçimi")

    # Maddede fiilen çalışan kişiler (çoklu seçim). Aynı kişi iki kez
    # gönderilirse tekilleştirilir; sıra kullanıcının seçtiği sırayla korunur
    # (ilk seçilen, completed_by verilmediğinde "tikleyen" olur).
    workers: list[models.User] = []
    if body and body.worker_ids:
        seen: set[int] = set()
        for uid in body.worker_ids:
            if uid in seen:
                continue
            seen.add(uid)
            u = db.get(models.User, uid)
            if not u or not u.is_active:
                raise HTTPException(400, "Geçersiz kullanıcı seçimi")
            # İş emri bir vardiyaya atanmışsa yalnızca O VARDİYADAKİ kişiler
            # "yapan" olarak seçilebilir — başka vardiyadan biri yanlışlıkla
            # (veya bilerek) bu bakıma yazılamasın.
            if (
                shift_mode_on
                and m.assigned_shift_id is not None
                and u.shift_id != m.assigned_shift_id
            ):
                shift_name = m.assigned_shift.name if m.assigned_shift else "atanan"
                raise HTTPException(
                    400,
                    f"{u.full_name} bu iş emrinin vardiyasında ({shift_name}) değil.",
                )
            workers.append(u)

    # Vardiya sorumlusu kuralı (kullanıcı isteği): "seçtiğimiz vardiya
    # sorumlusu sadece diğerleri adına tikleme yapabilsin." Yani vardiyanın
    # SIRADAN bir üyesi yalnızca KENDİ adına işaretleyebilir; başka birini
    # (ya da kendisiyle birlikte başkalarını) yapan olarak yazmak vardiya
    # sorumlusuna özeldir. Yönetici/mühendis/amir bu kısıttan muaftır.
    if shift_mode_on and not is_staff and workers:
        shift = m.assigned_shift or (db.get(models.Shift, user.shift_id) if user.shift_id else None)
        is_supervisor = shift is not None and shift.supervisor_id == user.id
        others = [w for w in workers if w.id != user.id]
        if others and not is_supervisor:
            raise HTTPException(
                403,
                "Başkası adına işaretleme yetkiniz yok — bunu yalnızca vardiya "
                "sorumlusu yapabilir. Kendi adınıza işaretleyebilirsiniz.",
            )

    item_name = item.name
    try:
        services.complete_item(db, item, user, completed_by=completed_by, workers=workers)
    except ValueError as e:
        raise HTTPException(400, str(e))
    attributed_to = completed_by or (workers[0] if workers else None) or user
    who = ", ".join(w.full_name for w in workers) if workers else attributed_to.full_name
    services.log_action(
        db, user, "complete", "item", item_id,
        f"Madde tamamlandı: {item_name} — {who}",
        detail=f"İş Emri #{m.id} ({m.vehicle.code} — {m.plan.name})",
    )
    if m.status == "completed":
        services.log_action(
            db, user, "complete", "maintenance", m.id,
            f"Bakım tamamlandı: {m.vehicle.code} — {m.plan.name}",
        )
    return {"ok": True, "maintenance_status": m.status}


@app.patch("/api/items/{item_id}/note", response_model=schemas.ItemOut)
def set_item_note(
    item_id: int,
    body: schemas.ItemNoteBody,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Usta/mühendis, bir bakım maddesini tikleyip incelerken o maddeye özel
    bir not düşebilir (şablon notundan bağımsız). Boş metin gönderilirse
    not kaldırılır. Paylaşılan tablet senaryosunda notu giren kişi oturum
    sahibinden farklı seçilebilir (author_id)."""
    if not services.has_permission(db, user, "edit_maintenance"):
        raise HTTPException(403, "Bakım maddelerine not ekleme yetkiniz yok")
    item = db.get(models.MaintenanceItem, item_id)
    if not item:
        raise HTTPException(404, "Madde bulunamadı")
    m = item.maintenance
    if user.role == "technician" and m.assigned_to_id not in (None, user.id):
        raise HTTPException(403, "Bu bakım size atanmamış")

    author = user
    if body.author_id is not None:
        chosen = db.get(models.User, body.author_id)
        if not chosen or not chosen.is_active:
            raise HTTPException(400, "Geçersiz kullanıcı seçimi")
        author = chosen

    text = (body.text or "").strip()
    item.exec_note = text
    if text:
        item.exec_note_by_id = author.id
        item.exec_note_at = datetime.now()
    else:
        item.exec_note_by_id = None
        item.exec_note_at = None
    db.commit()
    db.refresh(item)
    services.log_action(
        db, user, "update", "item", item_id,
        f"Madde notu {'güncellendi' if text else 'kaldırıldı'}: {item.name} — {author.full_name}",
        detail=f"İş Emri #{m.id} ({m.vehicle.code} — {m.plan.name})",
    )
    out = schemas.ItemOut.model_validate(item)
    if item.source_node_id is not None:
        node = db.get(models.TreeNode, item.source_node_id)
        if node:
            out.note = node.note or ""
            out.card_code = node.card_code or ""
            out.attachments = [
                schemas.NodeAttachmentOut.model_validate(a) for a in node.attachments
            ]
    out.tier_kms = _parse_tier_kms(getattr(item, "tiers", ""))
    out.exec_attachments = [
        schemas.ItemExecAttachmentOut.model_validate(a)
        for a in db.query(models.ItemExecAttachment)
        .filter_by(item_id=item.id)
        .order_by(models.ItemExecAttachment.id)
        .all()
    ]
    return out


# ================================================================ Bakım sırasında görsel ekleme (Faz 21)
@app.post("/api/items/{item_id}/exec-attachments", response_model=schemas.ItemExecAttachmentOut)
async def upload_item_exec_attachment(
    item_id: int,
    file: UploadFile = File(...),
    uploaded_by_id: int | None = Form(None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Usta/mühendis/amir bir bakım maddesini tikleyip incelerken tablet
    kamerasıyla (veya galeriden) fotoğraf ekler — sahadaki o anki durumu
    (arıza, aşınma, tamamlanmış iş kanıtı vb.) belgelemek içindir. Admin'in
    bakım ağacı ŞABLONUNA eklediği referans/talimat görselinden (bkz.
    upload_node_attachment) FARKLIDIR ve yalnızca RESİM kabul eder.
    Paylaşılan tablet senaryosunda fotoğrafı çeken kişi oturum sahibinden
    farklı seçilebilir (completed_by_id/author_id ile aynı desen)."""
    if not services.has_permission(db, user, "edit_maintenance"):
        raise HTTPException(403, "Bakım maddelerine görsel ekleme yetkiniz yok")
    item = db.get(models.MaintenanceItem, item_id)
    if not item:
        raise HTTPException(404, "Madde bulunamadı")
    m = item.maintenance
    if user.role == "technician" and m.assigned_to_id not in (None, user.id):
        raise HTTPException(403, "Bu bakım size atanmamış")

    uploader = user
    if uploaded_by_id is not None:
        chosen = db.get(models.User, uploaded_by_id)
        if not chosen or not chosen.is_active:
            raise HTTPException(400, "Geçersiz kullanıcı seçimi")
        uploader = chosen

    original = file.filename or "gorsel"
    ext = os.path.splitext(original)[1].lower()
    content = await file.read()
    # Tablet kameraları (özellikle iPad) genelde HEIC üretir — tarayıcıda
    # doğrudan görüntülenemeyeceği için önce görüntülenebilir bir formata
    # çevrilir (bkz. upload_node_attachment/upload_part_image'daki aynı adım).
    content, ext = await run_in_threadpool(bg_remove.convert_to_browser_image, content, ext)
    if ext not in _IMAGE_EXTS:
        raise HTTPException(400, "Yalnızca resim dosyası yüklenebilir")
    _reject_dangerous_ext(ext)
    _validate_upload_content(content, ext, "image")
    content = _sanitize_if_svg(content, ext)
    fname = f"{uuid.uuid4().hex}{ext}"
    (ATTACH_DIR / fname).write_bytes(content)
    att = models.ItemExecAttachment(
        item_id=item_id, filename=fname, original_name=original, uploaded_by_id=uploader.id,
    )
    db.add(att)
    db.commit()
    db.refresh(att)
    services.log_action(
        db, user, "create", "item_exec_attachment", att.id,
        f"Bakım maddesine görsel eklendi: {item.name} — {uploader.full_name}",
        detail=f"İş Emri #{m.id} ({m.vehicle.code} — {m.plan.name})",
    )
    return att


@app.delete("/api/item-exec-attachments/{attachment_id}")
def delete_item_exec_attachment(
    attachment_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Görseli ekleyen kişi de dahil olmak üzere, işaretleme yetkisi (edit_
    maintenance) olan herkes silebilir — paylaşılan tablet senaryosunda
    'yanlış fotoğraf' düzeltmesi anında yapılabilsin diye (not/tamamlama
    işaretlerinde de aynı yaklaşım)."""
    if not services.has_permission(db, user, "edit_maintenance"):
        raise HTTPException(403, "Bu görseli silme yetkiniz yok")
    att = db.get(models.ItemExecAttachment, attachment_id)
    if not att:
        raise HTTPException(404, "Ek bulunamadı")
    item = att.item
    m = item.maintenance if item else None
    if m and user.role == "technician" and m.assigned_to_id not in (None, user.id):
        raise HTTPException(403, "Bu bakım size atanmamış")
    fpath = ATTACH_DIR / att.filename if att.filename else None
    label = att.original_name
    item_name = item.name if item else ""
    mid_txt = f"İş Emri #{m.id}" if m else ""
    db.delete(att)
    db.commit()
    if fpath and fpath.is_file():
        try:
            fpath.unlink()
        except OSError:
            pass
    services.log_action(
        db, user, "delete", "item_exec_attachment", attachment_id,
        f"Bakım maddesindeki görsel silindi: {item_name}", detail=f"{label} · {mid_txt}",
    )
    return {"ok": True}


@app.post("/api/items/{item_id}/unlock")
def unlock_item(
    item_id: int, db: Session = Depends(get_db), actor=Depends(require_permission("approve_maintenance"))
):
    """Onaylama yetkisi olanlar: kilidi kaldırır, madde ve alt ağacı yeniden
    düzenlenebilir olur (admin her zaman yetkilidir)."""
    item = db.get(models.MaintenanceItem, item_id)
    if not item:
        raise HTTPException(404, "Madde bulunamadı")
    item_name = item.name
    mid = item.maintenance_id
    services.unlock_item(db, item)
    services.log_action(
        db, actor, "unlock", "item", item_id,
        f"Kilit açıldı: {item_name}",
        detail=f"İş Emri #{mid}",
    )
    return {"ok": True}


@app.post("/api/maintenances/{mid}/force-complete")
def force_complete_maintenance(
    mid: int, db: Session = Depends(get_db), actor=Depends(require_admin)
):
    """Yalnızca admin: tek tek madde işaretlemeden iş emrini doğrudan
    tamamlandı yapar (ör. kağıt üzerinde zaten yapılmış, sisteme toplu
    kapatılacak bir bakım için)."""
    m = db.get(models.Maintenance, mid)
    if not m:
        raise HTTPException(404, "Bakım bulunamadı")
    if m.status == "completed":
        raise HTTPException(400, "Bu iş emri zaten tamamlandı")
    services.force_complete_maintenance(db, m, actor)
    services.log_action(
        db, actor, "complete", "maintenance", m.id,
        f"Bakım doğrudan tamamlandı yapıldı: {m.vehicle.code} — {m.plan.name}",
        detail="Admin kısayolu (tek tek madde işaretlenmeden)",
    )
    return {"ok": True, "maintenance_status": m.status}


@app.post("/api/maintenances/{mid}/reopen")
def reopen_maintenance(
    mid: int, db: Session = Depends(get_db), actor=Depends(require_admin)
):
    """Yalnızca admin: tamamlandı olarak işaretlenmiş bir iş emrini bu
    durumdan çıkarır (ör. yanlışlıkla veya erken tamamlandı denmişse).
    Madde işaretleri/kilitleri değişmez — yalnızca iş emrinin genel durumu
    geri alınır."""
    m = db.get(models.Maintenance, mid)
    if not m:
        raise HTTPException(404, "Bakım bulunamadı")
    if m.status != "completed":
        raise HTTPException(400, "Bu iş emri zaten tamamlandı durumunda değil")
    services.reopen_maintenance(db, m)
    services.log_action(
        db, actor, "reopen", "maintenance", m.id,
        f"Bakım tamamlandıdan çıkarıldı: {m.vehicle.code} — {m.plan.name}",
    )
    return {"ok": True, "maintenance_status": m.status}


@app.post("/api/maintenances/{mid}/notes", response_model=schemas.NoteOut)
def add_maintenance_note(
    mid: int,
    body: schemas.NoteCreateBody,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Usta ekranından, checklist dışında bulunan ek arıza/bakım ihtiyacı
    bildirmek için serbest metin not ekler. Paylaşılan tablet senaryosunda
    notu giren kişi oturum sahibinden farklı seçilebilir (author_id)."""
    if not services.has_permission(db, user, "edit_maintenance"):
        raise HTTPException(403, "Not ekleme yetkiniz yok")
    m = db.get(models.Maintenance, mid)
    if not m:
        raise HTTPException(404, "Bakım bulunamadı")
    if user.role == "technician" and m.assigned_to_id not in (None, user.id):
        raise HTTPException(403, "Bu bakım size atanmamış")

    author = user
    if body.author_id is not None:
        picked = db.get(models.User, body.author_id)
        if not picked or not picked.is_active:
            raise HTTPException(400, "Geçersiz kullanıcı seçimi")
        author = picked

    # Metin boş olabilir: yalnızca fotoğraflı not desteklenir (bkz.
    # schemas.NoteCreateBody). Fotoğraflar not oluşturulduktan SONRA
    # yüklendiği için burada "boş" kontrolü yapılamaz.
    note = services.add_maintenance_note(db, m, body.text.strip(), author)
    ozet = body.text.strip()[:120] or "(yalnızca fotoğraf)"
    services.log_action(
        db, user, "create", "maintenance_note", note.id,
        f"Not eklendi ({author.full_name}): {ozet}",
        detail=f"İş Emri #{m.id} ({m.vehicle.code} — {m.plan.name})",
    )
    return note


@app.post("/api/notes/{note_id}/photos", response_model=schemas.NotePhotoOut)
async def upload_note_photo(
    note_id: int,
    file: UploadFile = File(...),
    uploaded_by_id: int | None = Form(None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """İş emrinin GENELİNE düşülen bir nota fotoğraf ekler — checklist'te
    karşılığı olmayan bir arıza/bulguyu belgelemek için. Madde bazlı
    fotoğraftan (upload_item_exec_attachment) farkı: o belirli bir checklist
    maddesine, bu ise notun kendisine bağlanır. Yalnızca RESİM kabul edilir;
    tablet kameralarının ürettiği HEIC/HEIF dosyaları tarayıcıda
    görüntülenebilir bir formata çevrilir (diğer görsel uçlarıyla aynı adım).
    """
    if not services.has_permission(db, user, "edit_maintenance"):
        raise HTTPException(403, "Nota fotoğraf ekleme yetkiniz yok")
    note = db.get(models.MaintenanceNote, note_id)
    if not note:
        raise HTTPException(404, "Not bulunamadı")
    m = note.maintenance
    if user.role == "technician" and m.assigned_to_id not in (None, user.id):
        raise HTTPException(403, "Bu bakım size atanmamış")

    uploader = user
    if uploaded_by_id is not None:
        chosen = db.get(models.User, uploaded_by_id)
        if not chosen or not chosen.is_active:
            raise HTTPException(400, "Geçersiz kullanıcı seçimi")
        uploader = chosen

    original = file.filename or "gorsel"
    ext = os.path.splitext(original)[1].lower()
    content = await file.read()
    content, ext = await run_in_threadpool(bg_remove.convert_to_browser_image, content, ext)
    if ext not in _IMAGE_EXTS:
        raise HTTPException(400, "Yalnızca resim dosyası yüklenebilir")
    _reject_dangerous_ext(ext)
    _validate_upload_content(content, ext, "image")
    content = _sanitize_if_svg(content, ext)
    fname = f"{uuid.uuid4().hex}{ext}"
    (NOTES_DIR / fname).write_bytes(content)
    photo = models.MaintenanceNotePhoto(
        note_id=note.id, filename=fname, original_name=original,
        uploaded_by_id=uploader.id,
    )
    db.add(photo)
    db.commit()
    db.refresh(photo)
    services.log_action(
        db, user, "create", "maintenance_note_photo", photo.id,
        f"Bakım notuna fotoğraf eklendi ({uploader.full_name})",
        detail=f"İş Emri #{m.id} ({m.vehicle.code} — {m.plan.name})",
    )
    return photo


@app.delete("/api/note-photos/{photo_id}")
def delete_note_photo(
    photo_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Not fotoğrafını siler. Madde fotoğraflarındaki yaklaşımın aynısı:
    işaretleme yetkisi (edit_maintenance) olan herkes silebilir — paylaşılan
    tablet senaryosunda 'yanlış fotoğraf' düzeltmesi anında yapılabilsin."""
    if not services.has_permission(db, user, "edit_maintenance"):
        raise HTTPException(403, "Bu fotoğrafı silme yetkiniz yok")
    photo = db.get(models.MaintenanceNotePhoto, photo_id)
    if not photo:
        raise HTTPException(404, "Fotoğraf bulunamadı")
    note = photo.note
    m = note.maintenance if note else None
    if m and user.role == "technician" and m.assigned_to_id not in (None, user.id):
        raise HTTPException(403, "Bu bakım size atanmamış")
    fpath = NOTES_DIR / photo.filename if photo.filename else None
    label = photo.original_name
    mid_txt = f"İş Emri #{m.id}" if m else ""
    db.delete(photo)
    db.commit()
    if fpath and fpath.is_file():
        try:
            fpath.unlink()
        except OSError:
            pass
    services.log_action(
        db, user, "delete", "maintenance_note_photo", photo_id,
        "Bakım notundaki fotoğraf silindi", detail=f"{label} · {mid_txt}",
    )
    return {"ok": True}


# ================================================================ Yetki Yönetimi
@app.get("/api/permissions", response_model=schemas.PermissionsMatrix)
def get_permissions(db: Session = Depends(get_db), _=Depends(require_admin)):
    """Mühendis/usta rolleri için tam izin matrisi (yalnızca Admin görür/düzenler)."""
    matrix = services.get_permission_matrix(db)
    return schemas.PermissionsMatrix(**matrix)


@app.patch("/api/permissions", response_model=schemas.PermissionsMatrix)
def update_permission(
    body: schemas.PermissionUpdateBody,
    db: Session = Depends(get_db),
    actor=Depends(require_admin),
):
    if body.permission not in services.PERMISSION_KEYS:
        raise HTTPException(400, "Geçersiz izin anahtarı")
    services.set_permission(db, body.role, body.permission, body.allowed, actor=actor)
    matrix = services.get_permission_matrix(db)
    return schemas.PermissionsMatrix(**matrix)


@app.get("/api/permissions/defs", response_model=list[schemas.PermissionDefOut])
def list_permission_defs(_=Depends(require_admin)):
    """İzin anahtarlarının kategori/açıklama metadatası — Yetki Yönetimi
    sayfasında gruplu/açıklamalı görünüm için (bkz. services.get_permission_defs)."""
    return [schemas.PermissionDefOut(**d) for d in services.get_permission_defs()]


@app.post("/api/permissions/reset", response_model=schemas.PermissionsMatrix)
def reset_role_permissions(
    body: schemas.PermissionResetBody,
    db: Session = Depends(get_db),
    actor=Depends(require_admin),
):
    """Bir rolün TÜM izinlerini fabrika varsayılanlarına döndürür (ör. admin
    bir rolde çok fazla ayar değiştirip kafası karıştıysa tek tıkla temiz
    bir başlangıç noktasına dönebilir)."""
    defaults = services.get_default_role_permissions(body.role)
    for key, allowed in defaults.items():
        services.set_permission(db, body.role, key, allowed, actor=actor)
    matrix = services.get_permission_matrix(db)
    services.log_action(
        db, actor, "update", "permission", None,
        f"Yetkiler varsayılana sıfırlandı: {body.role}",
    )
    return schemas.PermissionsMatrix(**matrix)


@app.get("/api/permissions/me", response_model=schemas.MyPermissions)
def get_my_permissions(
    db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    """Oturum açan kullanıcının etkin izinleri (frontend'de buton/aksiyon
    görünürlüğünü buna göre ayarlar)."""
    if user.role == "admin":
        perms = {key: True for key in services.PERMISSION_KEYS}
    else:
        perms = services.get_role_permissions(db, user.role)
    return schemas.MyPermissions(role=user.role, permissions=perms)


def _masked_db_location() -> str:
    """DATABASE_URL'i, sırlar (parola) sızdırmadan okunur bir konum
    özetine çevirir. SQLite'ta ÇÖZÜMLENMİŞ MUTLAK dosya yolunu döner —
    'kod düzenlediğim dosyayla, uygulamanın gerçekten okuduğu dosya aynı
    mı' sorusunu anında yanıtlamak için (bkz. Docker named volume karışıklığı:
    docker-compose.yml'de DATABASE_URL=sqlite:////app/data/ulasim_bakim.db,
    yerel geliştirmede ise backend/ulasim_bakim.db — göreli yol, sunucunun
    NEREDEN başlatıldığına bağlı olduğundan bu ikisi kolayca karışabilir)."""
    if IS_SQLITE:
        raw_path = engine.url.database or ""
        try:
            return str(Path(raw_path).resolve())
        except Exception:
            return raw_path
    url = engine.url
    return f"{url.drivername}://{url.username or '?'}@{url.host or '?'}:{url.port or '?'}/{url.database or '?'}"


# ================================================================ Sistem Durumu (Admin)
@app.get("/api/system/status", response_model=schemas.SystemStatusOut)
def system_status(db: Session = Depends(get_db), _=Depends(require_admin)):
    """Admin için tanı/teşhis amaçlı sistem özeti: uygulamanın GERÇEKTE hangi
    veritabanı dosyasına/sunucusuna bağlı olduğu, temel sayaçlar, yedekleme/
    yükleme/log klasörleri ve süreç çalışma süresi. 'Kodu düzenledim ama
    ekranda değişiklik görünmüyor' / 'yanlış veritabanını düzenlemişim'
    türü sorunları saniyeler içinde teşhis etmek için tasarlandı."""
    plan_q = db.query(models.MaintenancePlan)
    backups = backup_utils.list_backups(BACKUP_DIR) if IS_SQLITE else []
    last_backup_at = max((b["created_at"] for b in backups), default=None)

    return schemas.SystemStatusOut(
        db_backend=engine.url.get_backend_name(),
        db_location=_masked_db_location(),
        foreign_keys_enforced=True if IS_SQLITE else None,
        started_at=_APP_STARTED_AT,
        uptime_seconds=(datetime.now() - _APP_STARTED_AT).total_seconds(),
        vehicle_count=db.query(models.Vehicle).count(),
        user_count=db.query(models.User).count(),
        plan_count=plan_q.count(),
        catalog_only_plan_count=plan_q.filter(models.MaintenancePlan.is_catalog_only == True).count(),  # noqa: E712
        unresolved_period_count=_count_unresolved_period_items(db),
        tree_node_count=db.query(models.TreeNode).count(),
        maintenance_pending=db.query(models.Maintenance).filter_by(status="pending").count(),
        maintenance_in_progress=db.query(models.Maintenance).filter_by(status="in_progress").count(),
        maintenance_completed=db.query(models.Maintenance).filter_by(status="completed").count(),
        backup_dir=str(BACKUP_DIR),
        upload_dir=str(UPLOADS_DIR),
        log_dir=config.get("LOG_DIR", str(Path(__file__).resolve().parent.parent / "logs")),
        last_backup_at=last_backup_at,
    )


# ================================================================ Yedekleme (Admin)
@app.get("/api/backups", response_model=list[schemas.BackupInfo])
def list_backups(_=Depends(require_admin)):
    """Mevcut otomatik veritabanı yedeklerini listeler (yalnızca Admin)."""
    return [schemas.BackupInfo(**b) for b in backup_utils.list_backups(BACKUP_DIR)]


@app.post("/api/backups/run", response_model=schemas.BackupRunResult)
def run_backup_now(db: Session = Depends(get_db), actor=Depends(require_admin)):
    """Yedeği hemen alır (günlük otomatik yedekten bağımsız, elle
    tetiklenen ek bir yedek). PostgreSQL kullanılıyorsa devre dışıdır."""
    path = backup_utils.run_backup_now(db, engine, BACKUP_DIR, actor=actor, note="elle tetiklendi")
    if path is None:
        return schemas.BackupRunResult(
            ok=False,
            message="Yedekleme yalnızca SQLite için desteklenir (PostgreSQL kullanılıyorsa harici yedekleme yapılandırın).",
        )
    return schemas.BackupRunResult(ok=True, filename=path.name, message="Yedek alındı.")


@app.get("/api/backups/{filename}/download")
def download_backup(filename: str, _=Depends(require_admin)):
    """Belirli bir yedek dosyasını indirir — düzenli olarak sunucu dışına
    (kendi bilgisayarınıza / bulut depolamaya) bir kopya almanız önerilir;
    bu, sunucunun kendisi tamamen kaybolsa bile veriyi korur."""
    # Path traversal koruması: yalnızca BACKUP_DIR içindeki, beklenen
    # adlandırma düzenine uyan dosyalara izin verilir.
    safe_name = Path(filename).name
    if not safe_name.startswith(backup_utils.BACKUP_FILE_PREFIX) or not safe_name.endswith(".db"):
        raise HTTPException(400, "Geçersiz yedek dosya adı")
    target = BACKUP_DIR / safe_name
    if not target.is_file():
        raise HTTPException(404, "Yedek dosyası bulunamadı")
    return FileResponse(target, media_type="application/octet-stream", filename=safe_name)


# ================================================================ Audit Log (Faz 17)
@app.get("/api/audit", response_model=schemas.AuditPage)
def list_audit(
    action: str | None = None,
    entity_type: str | None = None,
    actor_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
    _=Depends(require_admin),
):
    """Değiştirilemez işlem kayıtları — yalnızca Admin. Aksiyon türü, varlık
    türü, aktör, tarih aralığı ve serbest metinle filtrelenir; sayfalanır."""
    query = db.query(models.AuditLog)
    if action:
        query = query.filter(models.AuditLog.action == action)
    if entity_type:
        query = query.filter(models.AuditLog.entity_type == entity_type)
    if actor_id:
        query = query.filter(models.AuditLog.actor_id == actor_id)
    df = _parse_date(date_from)
    dt_ = _parse_date(date_to, end=True)
    if df is not None:
        query = query.filter(models.AuditLog.created_at >= df)
    if dt_ is not None:
        query = query.filter(models.AuditLog.created_at <= dt_)
    if q:
        like = f"%{q}%"
        query = query.filter(
            models.AuditLog.summary.ilike(like)
            | models.AuditLog.detail.ilike(like)
            | models.AuditLog.actor_name.ilike(like)
        )
    total = query.count()
    limit = max(1, min(limit, 500))
    items = (
        query.order_by(models.AuditLog.created_at.desc(), models.AuditLog.id.desc())
        .offset(max(0, offset))
        .limit(limit)
        .all()
    )
    return schemas.AuditPage(total=total, items=items)


@app.get("/api/audit/export")
def export_audit(
    format: str = "xlsx",
    action: str | None = None,
    entity_type: str | None = None,
    actor_id: int | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_admin),
):
    """İşlem kayıtlarını (audit log) Excel/PDF/CSV/Word olarak indirir (Faz 22).
    Audit sayfasındaki aktif filtreler aynen uygulanır; yalnızca Admin."""
    fmt = (format or "xlsx").lower()
    if fmt not in _EXPORT_FORMATS:
        raise HTTPException(400, "Geçersiz format (xlsx, csv, pdf, docx desteklenir)")
    query = db.query(models.AuditLog)
    if action:
        query = query.filter(models.AuditLog.action == action)
    if entity_type:
        query = query.filter(models.AuditLog.entity_type == entity_type)
    if actor_id:
        query = query.filter(models.AuditLog.actor_id == actor_id)
    df = _parse_date(date_from)
    dt_ = _parse_date(date_to, end=True)
    if df is not None:
        query = query.filter(models.AuditLog.created_at >= df)
    if dt_ is not None:
        query = query.filter(models.AuditLog.created_at <= dt_)
    if q:
        like = f"%{q}%"
        query = query.filter(
            models.AuditLog.summary.ilike(like)
            | models.AuditLog.detail.ilike(like)
            | models.AuditLog.actor_name.ilike(like)
        )
    logs = query.order_by(models.AuditLog.created_at.desc(), models.AuditLog.id.desc()).limit(5000).all()
    content = export_utils.build_audit_export(logs, fmt)
    services.log_action(
        db, user, "export", "system", None,
        f"İşlem Kayıtları {_EXPORT_FORMAT_TR[fmt]}'e aktarıldı ({len(logs)} kayıt)",
    )
    fname = export_utils.filename_for("islem_kayitlari", fmt)
    return Response(
        content=content,
        media_type=export_utils.MEDIA_TYPES[fmt],
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


# ================================================================ Günlük KM Simülasyonu
def _km_sim_status(db: Session) -> schemas.KmSimConfig:
    cfg = services.get_km_sim_config(db)
    return schemas.KmSimConfig(
        enabled=cfg["enabled"],
        min_km=cfg["min_km"],
        max_km=cfg["max_km"],
        last_date=cfg["last_date"],
        vehicle_count=db.query(models.Vehicle).count(),
    )


@app.get("/api/km-sim", response_model=schemas.KmSimConfig)
def get_km_sim(db: Session = Depends(get_db), _=Depends(require_admin)):
    """Günlük KM simülasyonu ayar/durumu (Admin)."""
    return _km_sim_status(db)


@app.patch("/api/km-sim", response_model=schemas.KmSimConfig)
def update_km_sim(
    body: schemas.KmSimUpdate, db: Session = Depends(get_db), actor=Depends(require_admin)
):
    """Simülasyonu aç/kapat ve günlük km aralığını (min/max) ayarla."""
    if body.enabled is not None:
        services.set_setting(db, "km_sim_enabled", "1" if body.enabled else "0")
    if body.min_km is not None:
        services.set_setting(db, "km_sim_min", body.min_km)
    if body.max_km is not None:
        services.set_setting(db, "km_sim_max", body.max_km)
    cfg = services.get_km_sim_config(db)
    if cfg["max_km"] < cfg["min_km"]:
        raise HTTPException(400, "Azami km, asgari km'den küçük olamaz")
    db.commit()
    services.log_action(
        db, actor, "update", "vehicle", None,
        f"KM simülasyonu ayarı: {'açık' if cfg['enabled'] else 'kapalı'}, "
        f"günlük {cfg['min_km']}–{cfg['max_km']} km",
    )
    return _km_sim_status(db)


@app.post("/api/km-sim/run", response_model=schemas.KmSimRunResult)
def run_km_sim(
    body: schemas.KmSimRunBody = schemas.KmSimRunBody(),
    db: Session = Depends(get_db),
    actor=Depends(require_admin),
):
    """Simülasyonu elle `days` gün ilerlet (test/hızlı ilerletme için).
    Otomatik günlük telafiden bağımsızdır; aç/kapat durumundan etkilenmez."""
    result = services.simulate_km_advance(db, body.days, actor=actor, note="elle ilerletme")
    return schemas.KmSimRunResult(**result)


# ============================================ Global Arama / Alarm / Bildirim
_STATUS_TR = {"pending": "Bekliyor", "in_progress": "Devam Ediyor", "completed": "Tamamlandı"}


@app.get("/api/search", response_model=schemas.SearchResults)
def global_search(q: str = "", db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Tek kutudan global arama: araç, iş emri, bakım planı, parça, personel
    ve PDF dokümanları tek uç noktadan bulur."""
    query = q.strip()
    if len(query) < 2:
        return schemas.SearchResults(query=query, total=0)
    pat = f"%{query}%"
    res = schemas.SearchResults(query=query, total=0)

    # Araçlar
    v_rows = (
        db.query(models.Vehicle)
        .filter(
            models.Vehicle.code.ilike(pat)
            | models.Vehicle.name.ilike(pat)
            | models.Vehicle.chassis_no.ilike(pat)
            | models.Vehicle.manufacturer.ilike(pat)
            | models.Vehicle.model.ilike(pat)
        )
        .order_by(models.Vehicle.code)
        .limit(8)
        .all()
    )
    for v in v_rows:
        sub = " · ".join(x for x in [v.name, f"{fmt_km(v.current_km)} km", v.model] if x)
        res.vehicles.append(schemas.SearchHit(type="vehicle", ref_id=v.id, title=v.code, subtitle=sub))

    # İş emirleri (bakımlar)
    m_conds = models.Vehicle.code.ilike(pat) | models.MaintenancePlan.name.ilike(pat)
    if query.isdigit():
        m_conds = m_conds | (models.Maintenance.id == int(query))
    m_rows = (
        db.query(models.Maintenance)
        .join(models.Vehicle, models.Maintenance.vehicle_id == models.Vehicle.id)
        .join(models.MaintenancePlan, models.Maintenance.plan_id == models.MaintenancePlan.id)
        .filter(m_conds)
        # Faz 26: henüz "Başlat" denmemiş aday iş emirleri diğer tüm
        # bakım sorgularında olduğu gibi genel aramada da görünmemeli.
        .filter(models.Maintenance.is_activated == True)  # noqa: E712
        .order_by(models.Maintenance.created_at.desc())
        .limit(8)
        .all()
    )
    for m in m_rows:
        res.maintenances.append(
            schemas.SearchHit(
                type="maintenance",
                ref_id=m.id,
                title=f"İş Emri #{m.id} — {m.vehicle.code}",
                subtitle=f"{m.plan.name} · {_STATUS_TR.get(m.status, m.status)}",
            )
        )

    # Bakım planları
    p_rows = (
        db.query(models.MaintenancePlan)
        .filter(models.MaintenancePlan.name.ilike(pat))
        .order_by(models.MaintenancePlan.interval_km)
        .limit(6)
        .all()
    )
    for p in p_rows:
        res.plans.append(
            schemas.SearchHit(type="plan", ref_id=p.id, title=p.name, subtitle=f"{fmt_km(p.interval_km)} km periyot")
        )

    # Parçalar
    part_rows = (
        db.query(models.PartCatalogItem)
        .filter(models.PartCatalogItem.name.ilike(pat) | models.PartCatalogItem.description.ilike(pat))
        .order_by(models.PartCatalogItem.name)
        .limit(8)
        .all()
    )
    for pt in part_rows:
        res.parts.append(
            schemas.SearchHit(type="part", ref_id=pt.id, title=pt.name, subtitle=pt.description[:80])
        )

    # Personel (yalnızca yetkili roller)
    if user.role in ("admin", "engineer", "amir") or services.has_permission(db, user, "manage_users"):
        u_rows = (
            db.query(models.User)
            .filter(models.User.full_name.ilike(pat) | models.User.username.ilike(pat))
            .order_by(models.User.full_name)
            .limit(8)
            .all()
        )
        for u in u_rows:
            role_tr = {"admin": "Admin", "engineer": "Mühendis", "technician": "Usta", "amir": "Amir"}.get(u.role, u.role)
            res.users.append(
                schemas.SearchHit(type="user", ref_id=u.id, title=u.full_name, subtitle=f"{role_tr} · {u.username}")
            )

    # PDF dokümanları (madde ekleri + parça ekleri)
    node_pdfs = (
        db.query(models.NodeAttachment)
        .filter(models.NodeAttachment.kind == "pdf", models.NodeAttachment.original_name.ilike(pat))
        .limit(6)
        .all()
    )
    for a in node_pdfs:
        res.documents.append(
            schemas.SearchHit(
                type="pdf", ref_id=a.id, title=a.original_name or a.filename,
                subtitle="Bakım ağacı eki", url=f"/uploads/attachments/{a.filename}",
            )
        )
    part_pdfs = (
        db.query(models.PartCatalogAttachment)
        .filter(models.PartCatalogAttachment.kind == "pdf", models.PartCatalogAttachment.original_name.ilike(pat))
        .limit(6)
        .all()
    )
    for a in part_pdfs:
        res.documents.append(
            schemas.SearchHit(
                type="pdf", ref_id=a.id, title=a.original_name or a.filename,
                subtitle="Parça eki", url=f"/uploads/parts/{a.filename}",
            )
        )

    res.total = (
        len(res.vehicles) + len(res.maintenances) + len(res.plans)
        + len(res.parts) + len(res.users) + len(res.documents)
    )
    return res


@app.get("/api/notifications", response_model=list[schemas.NotificationOut])
def notifications(
    limit: int = 40, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)
):
    """Bildirim Merkezi — işlem kayıtlarından (yeni iş emri, tamamlanan bakım,
    kilit açma, yeni kullanıcı) türetilen akış; ayrıca o an geciken bakımlar
    'overdue' bildirimi olarak eklenir."""
    limit = max(1, min(limit, 100))
    out: list[schemas.NotificationOut] = []
    can_users = user.role in ("admin", "engineer", "amir") or services.has_permission(db, user, "manage_users")

    # 1) Geciken bakımlar (o anki durumdan) — en üstte
    for it in _compute_upcoming_merged(db, include_open=True):
        if it["remaining_km"] > 0:
            continue
        v = it["vehicle"]
        m = (
            db.query(models.Maintenance)
            .filter(
                models.Maintenance.vehicle_id == v.id,
                models.Maintenance.target_km == it["target_km"],
                models.Maintenance.status.in_(["pending", "in_progress"]),
                # Faz 26: henüz "Başlat" denmemiş aday iş emirleri burada da
                # gerçek bir iş emriymiş gibi görünmemeli.
                models.Maintenance.is_activated == True,  # noqa: E712
            )
            .order_by(models.Maintenance.target_km.desc())
            .first()
        )
        detail = (
            f"{abs(it['remaining_km'])} gün geçti" if it.get("target_date") is not None
            else f"{fmt_km(abs(it['remaining_km']))} km geçildi"
        )
        out.append(
            schemas.NotificationOut(
                id=-(m.id if m else v.id),
                category="overdue",
                title=f"{v.code} — {it['plan_name']} bakımı gecikti",
                detail=detail,
                created_at=(m.created_at if m else datetime.now()),
                maintenance_id=(m.id if m else None),
            )
        )

    # 2) İşlem kaydından türetilen bildirimler
    rows = (
        db.query(models.AuditLog)
        .order_by(models.AuditLog.created_at.desc())
        .limit(300)
        .all()
    )
    for a in rows:
        if a.entity_type == "maintenance" and a.action == "create":
            # Faz 26: otomatik açılan ama admin/mühendis henüz "Başlat"
            # demediği için hiçbir listede görünmemesi gereken aday iş
            # emirleri, bu bildirim akışından da (herkese açık "work_order"
            # bildirimi) gizlenir. Sonradan başlatılırsa (is_activated=True
            # olursa) normal şekilde görünür — kayıt silinmişse (m_check
            # None) geçmiş bir bildirim olarak göstermeye devam edilir.
            m_check = db.get(models.Maintenance, a.entity_id) if a.entity_id else None
            if m_check is not None and not m_check.is_activated:
                continue
            cat = "work_order"
        elif a.entity_type == "maintenance" and a.action == "complete":
            cat = "completed"
        elif a.action == "unlock":
            cat = "unlock"
        elif a.entity_type == "user" and a.action == "create":
            if not can_users:
                continue
            cat = "user"
        elif a.entity_type == "suggestion" and a.action == "create":
            # Yeni iyileştirme talepleri, talebi değerlendirebilecek
            # rollere (admin/amir/mühendis) bildirim olarak düşer.
            if user.role not in ("admin", "amir", "engineer"):
                continue
            cat = "suggestion"
        else:
            continue
        out.append(
            schemas.NotificationOut(
                id=a.id,
                category=cat,
                title=a.summary or a.action,
                detail=a.detail,
                created_at=a.created_at,
                maintenance_id=a.entity_id if a.entity_type == "maintenance" else None,
            )
        )

    # 3) Düşük stok uyarıları — yalnızca admin/mühendis/amir'e, ve yalnızca
    # min_stock > 0 olan (yani takibi açıkça istenen) parçalar için; aksi
    # halde stok hiç girilmemiş yüzlerce parça bildirim akışını doldururdu.
    if user.role in ("admin", "engineer", "amir") or services.has_permission(db, user, "manage_users"):
        low_parts = (
            db.query(models.PartCatalogItem)
            .filter(models.PartCatalogItem.min_stock > 0)
            .filter(models.PartCatalogItem.stock_qty <= models.PartCatalogItem.min_stock)
            .order_by(models.PartCatalogItem.name)
            .all()
        )
        for p in low_parts:
            out.append(
                schemas.NotificationOut(
                    id=-(2_000_000_000 + p.id),
                    category="low_stock",
                    title=f"{p.name} stoğu azaldı",
                    detail=f"{_fmt_qty(p.stock_qty)} {p.unit} kaldı (eşik: {_fmt_qty(p.min_stock)} {p.unit})",
                    created_at=datetime.now(),
                    maintenance_id=None,
                )
            )

    out.sort(key=lambda n: n.created_at, reverse=True)
    return out[:limit]


# ================================================================ Canlı Güncelleme (SSE)
def _user_from_query_token(token: str, db: Session) -> models.User:
    """`GET /api/events/stream` için — EventSource tarayıcı API'si özel
    başlık (Authorization) GÖNDEREMEDİĞİ için token sorgu parametresiyle
    (`?token=...`) taşınır. Bu, auth.get_current_user'ın OAuth2PasswordBearer
    başlığı yerine query'den okuyan elle yazılmış eşdeğeridir — aynı
    SECRET_KEY/ALGORITHM ve doğrulama mantığını kullanır."""
    cred_error = HTTPException(status_code=401, detail="Oturum geçersiz")
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload.get("sub"))
    except (JWTError, TypeError, ValueError):
        raise cred_error
    user = db.get(models.User, user_id)
    if not user or not user.is_active:
        raise cred_error
    return user


@app.get("/api/events/stream")
async def events_stream(token: str):
    """Canlı güncelleme akışı (Sunucu Gönderimli Olaylar / SSE) — bir
    kullanıcı bir maddeyi tamamladığında, km güncellediğinde, bir alarmı
    onayladığında vb. (bkz. services.log_action -> events.broadcast) bağlı
    tüm istemcilere anlık bildirim gider. Frontend, hangi sayfanın hangi
    entity_type'larla ilgilendiğine kendi karar verir (bkz. useLiveEvents);
    burası kasıtlı olarak sade/kaba (coarse-grained) tutulmuştur — Redis vb.
    gerektirmeyen tek-process bir yayıncı kullanır (bkz. events.py).

    DİKKAT: bu bağlantı tarayıcı sekmesi açık kaldığı sürece SAATLERCE canlı
    kalabilir — bu yüzden kimlik doğrulaması için normal `Depends(get_db)`
    KULLANILMAZ (o, bağlantı süresince bir DB oturumunu açık tutardı ve çok
    sayıda açık sekmede bağlantı havuzunu tüketebilirdi). Bunun yerine kısa
    ömürlü, elle açılıp hemen kapatılan bir oturumla yalnızca token doğrulanır."""
    db = SessionLocal()
    try:
        _user_from_query_token(token, db)  # yalnızca geçerli oturum sahipleri bağlanabilir
    finally:
        db.close()
    sid = events.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            last_heartbeat = time.time()
            while True:
                for item in events.poll(sid):
                    yield f"data: {json.dumps(item)}\n\n"
                if time.time() - last_heartbeat > 20:
                    yield ": ping\n\n"  # proxy/tarayıcı bağlantıyı canlı tutar
                    last_heartbeat = time.time()
                await asyncio.sleep(0.5)
        finally:
            events.unsubscribe(sid)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _count_unresolved_period_items(db: Session) -> int:
    """'Periyodu Belirsiz (?)' (plan_type='unknown') planların ağaçlarındaki
    yaprak (en alt seviye) madde sayısını döner — kaynak Excel'de BAKIM
    periyodu hâlâ '?' olan, gözden geçirilmesi/netleştirilmesi gereken kalem
    sayısı. Dashboard'da ve Sistem Durumu'nda kalıcı bir hatırlatma olarak
    kullanılır (bkz. schemas.DashboardOut.unresolved_period_count)."""
    plan_ids = [
        pid for (pid,) in db.query(models.MaintenancePlan.id)
        .filter(models.MaintenancePlan.plan_type == "unknown")
        .all()
    ]
    if not plan_ids:
        return 0
    roots = (
        db.query(models.TreeNode)
        .filter(models.TreeNode.parent_id.is_(None), models.TreeNode.plan_id.in_(plan_ids))
        .all()
    )
    count = 0

    def walk(n: models.TreeNode) -> None:
        nonlocal count
        if not n.children:
            count += 1
            return
        for c in n.children:
            walk(c)

    for r in roots:
        walk(r)
    return count


# ================================================================ Dashboard
@app.get("/api/dashboard", response_model=schemas.DashboardOut)
def dashboard(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    q = db.query(models.Maintenance)
    # Faz 26: dashboard sayaçları/listeleri de yalnızca "Başlat" denmiş iş
    # emirlerini sayar/gösterir — otomatik adaylar Bakımlar sayfasındaki
    # "Başlatılmayı Bekleyenler" bölümünde ayrıca (başlatma yetkisi olanlara)
    # görünür.
    q = q.filter(models.Maintenance.is_activated == True)  # noqa: E712
    if user.role == "technician":
        q = q.filter(
            (models.Maintenance.assigned_to_id == user.id)
            | (models.Maintenance.assigned_to_id.is_(None))
        )

    # Bekleyen/Devam eden sayıları, Bakımlar sayfasındaki listeyle tutarlı
    # olsun diye araç bazında tekilleştirilir (bkz. services.dedupe_open_
    # by_vehicle): aynı aracın hem küçük hem büyük periyotlu açık bakımı
    # varsa yalnızca büyüğü sayılır.
    open_rows = q.filter(models.Maintenance.status.in_(["pending", "in_progress"])).all()
    deduped_open = services.dedupe_open_by_vehicle(open_rows)
    pending_count = sum(1 for m in deduped_open if m.status == "pending")
    in_progress_count = sum(1 for m in deduped_open if m.status == "in_progress")
    completed_count = q.filter_by(status="completed").count()

    month_start = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    completed_this_month = q.filter(
        models.Maintenance.status == "completed",
        models.Maintenance.completed_at >= month_start,
    ).count()

    vehicle_count = db.query(models.Vehicle).count()

    pending_user_approvals = 0
    if user.role == "admin":
        pending_user_approvals = db.query(models.User).filter_by(is_active=False).count()

    # Yaklaşan bakımlar: aktif araç + aktif plan için, hedefe göre sabit bir
    # önizleme penceresi (-%5 / +%10) içindeyse listelenir. Bu, gerçek
    # otomatik-açma toleransından (plan.lower_pct/upper_pct, çok daha geniş
    # olabilir) bağımsız, yalnızca "gerçekten yaklaşmış" olanları göstermek
    # için kullanılan sabit bir önizleme aralığıdır. Mantık services.py'de
    # tek yerde tutulur (Araçlar sayfasındaki kırmızı vurgulama da aynı
    # fonksiyonu kullanır).
    # Arayüz bu listeyi ilk bakışta 10 satırla sınırlar ve gerisini "Daha
    # fazlasını gör" ile açar (bkz. DashboardPage.tsx → DUE_TABLE_LIMIT), bu
    # yüzden burada 8 değil daha geniş bir üst sınır gönderilir. Sınır tamamen
    # kaldırılmaz — çok araçlı kurulumlarda dashboard yanıtı gereksiz büyümesin.
    vehicles = db.query(models.Vehicle).all()
    plans = db.query(models.MaintenancePlan).filter_by(is_active=True).all()
    upcoming = [
        schemas.UpcomingItem(**item)
        for item in _compute_upcoming_merged(db, vehicles, plans, limit=DASHBOARD_DUE_MAX)
    ]

    # Zamanı gelmiş (açılmış ama henüz başlanmamış = Bekliyor) bakımlar.
    # 1.000.000'e ulaşan araçta olduğu gibi, hedefe varınca bakım otomatik
    # açıldığı için "yaklaşan" penceresinden çıkar; burada Bekliyor olarak
    # dashboard'da görünür kalır. deduped_open ile aynı tekilleştirilmiş
    # kümeden alınır (küçük periyotlar büyük bakımın içine dahilse ayrı
    # satır olarak tekrar gösterilmez).
    #
    # Kullanıcı isteği: "bekliyor" durumundaki bakımlar bittiyse liste boş
    # kalmasın — BAŞLATILMIŞ (in_progress = "Planlandı") bakımlar da burada
    # görünmeye devam etsin; hiç açık bakım kalmadıysa sıra "yaklaşan"lara
    # geçsin (aşağıdaki `upcoming` listesi zaten öncelik sırasında).
    due_rows = [m for m in deduped_open if m.status in ("pending", "in_progress")]
    def _due_item(dm: models.Maintenance) -> schemas.UpcomingItem:
        is_time = dm.plan.plan_type == "time"
        if is_time:
            remaining_km = (dm.target_date - date.today()).days if dm.target_date else 0
            estimated_days = max(0, remaining_km)
            critical = remaining_km <= services.CRITICAL_WITHIN_DAYS
            urgent = remaining_km <= services.URGENT_WITHIN_DAYS
        else:
            remaining_km = dm.target_km - dm.vehicle.current_km
            estimated_days = (
                math.ceil(remaining_km / services.DEFAULT_CALENDAR_DAILY_KM) if remaining_km > 0 else 0
            )
            # KM bazlı planlarda kritiklik, planın kendi periyoduna (interval_km)
            # oranlı: kalan km periyodun %15'inin altındaysa "kritik" (urgent),
            # %5'inin altındaysa "çok kritik" (critical) — bkz. services.py
            # KRITIK_PCT_OF_INTERVAL / COK_KRITIK_PCT_OF_INTERVAL.
            interval_km = dm.plan.interval_km or 0
            urgent = remaining_km <= interval_km * services.KRITIK_PCT_OF_INTERVAL / 100
            critical = remaining_km <= interval_km * services.COK_KRITIK_PCT_OF_INTERVAL / 100
        return schemas.UpcomingItem(
            vehicle=schemas.VehicleOut.model_validate(dm.vehicle),
            plan_name=dm.plan.name,
            target_km=dm.target_km if not is_time else 0,
            remaining_km=remaining_km,
            target_date=dm.target_date if is_time else None,
            covered_tiers=_parse_tier_kms(
                ",".join(it.tiers for it in dm.items if it.tiers)
            ),
            estimated_days=estimated_days,
            urgent=urgent,
            critical=critical,
            maintenance_id=dm.id,
            # Arayüzdeki rozet metni bu alandan gelir: başlatılmış iş emri
            # "Planlandı", henüz başlanmamış ve hedefi geçmiş "Gecikti",
            # hedefi geçmemiş "Bekliyor" olarak gösterilir.
            status=(
                "planlandi"
                if dm.status == "in_progress"
                else ("gecikti" if remaining_km <= 0 else "planlanacak")
            ),
            assigned_shift_id=dm.assigned_shift_id,
            assigned_shift_name=dm.assigned_shift.name if dm.assigned_shift else None,
        )

    # Öncelik sırası (kullanıcı isteği: "önceliğe göre gösterilsin"):
    #   1) Hedefi geçmiş olanlar önce — en çok geciken en üstte,
    #   2) sonra hedefe kalan süreye göre en yakın olan en üstte.
    # km ve süre bazlı planlar farklı birimlerde olduğu için ortak ölçü
    # `estimated_days`'tir (bkz. _compute_upcoming_merged'deki aynı gerekçe).
    def _due_priority(u: schemas.UpcomingItem) -> tuple[int, int]:
        if u.remaining_km <= 0:
            return (0, u.remaining_km)  # en negatif (en çok geciken) başa
        return (1, u.estimated_days if u.estimated_days is not None else u.remaining_km)

    due_now = sorted((_due_item(dm) for dm in due_rows), key=_due_priority)[:DASHBOARD_DUE_MAX]

    # Yalnızca Bakım Planları sayfasında (GET /api/plans, varsayılan) GERÇEKTEN
    # görünen bir plana bağlı tamamlanan bakımlar gösterilir — o sayfa hem
    # pasif (is_active=False, ör. tier-merge ile birleştirilmiş) hem de yalnızca
    # katalog amaçlı (is_catalog_only=True, ör. bir temel periyodun tam katı —
    # kendi başına bakım tetiklemez) planları GÖSTERMEZ; burası da aynı iki
    # koşulu uygulamalı, aksi halde Bakım Planları'nda hiç karşılığı olmayan
    # garip/eski km değerleri (ör. "150M") görünmeye devam eder.
    # (Bakım Geçmişi sayfası bundan ETKİLENMEZ — tüm tamamlanan kayıtları,
    # planı pasif/katalog-amaçlı olsa bile, kalıcı olarak göstermeye devam eder.)
    recent_q = (
        db.query(models.Maintenance)
        .join(models.MaintenancePlan)
        .filter(
            models.Maintenance.status == "completed",
            models.MaintenancePlan.is_active == True,  # noqa: E712
            models.MaintenancePlan.is_catalog_only == False,  # noqa: E712
        )
    )
    if user.role == "technician":
        recent_q = recent_q.filter(
            (models.Maintenance.assigned_to_id == user.id)
            | (models.Maintenance.assigned_to_id.is_(None))
        )
    recent_completed = (
        recent_q.order_by(models.Maintenance.completed_at.desc()).limit(5).all()
    )
    for m in recent_completed:
        m.covered_tiers = services.compute_covered_tiers(m)

    # Plan ve araç bazlı özetlerdeki bekleyen/devam eden sayıları da
    # deduped_open'dan hesaplanır — aksi halde bir aracın hem küçük hem büyük
    # periyotlu açık bakımı varsa (ör. 25.000 + 50.000), burada ikisi de ayrı
    # ayrı sayılır ve üstteki tekilleştirilmiş "Bekleyen Bakım" toplamıyla
    # (pending_count/in_progress_count) tutarsız görünür (grafikte "9 araç
    # bekliyor" derken toplamda daha az gözükmesi gibi). Tamamlanan sayılar
    # tekilleştirilmez — geçmiş kayıt olduğu için belirsizlik yoktur.
    open_by_plan: dict[int, list[models.Maintenance]] = {}
    open_by_vehicle: dict[int, list[models.Maintenance]] = {}
    for m in deduped_open:
        open_by_plan.setdefault(m.plan_id, []).append(m)
        open_by_vehicle.setdefault(m.vehicle_id, []).append(m)

    # Plan bazlı özet (hangi periyotta kaç bekleyen/devam eden/tamamlanan var)
    by_plan: list[schemas.PlanSummary] = []
    for p in plans:
        plan_open = open_by_plan.get(p.id, [])
        pending_p = sum(1 for m in plan_open if m.status == "pending")
        in_progress_p = sum(1 for m in plan_open if m.status == "in_progress")
        completed_p = q.filter(models.Maintenance.plan_id == p.id, models.Maintenance.status == "completed").count()
        if pending_p or in_progress_p or completed_p:
            by_plan.append(
                schemas.PlanSummary(
                    plan=p, pending=pending_p, in_progress=in_progress_p, completed=completed_p
                )
            )
    by_plan.sort(key=lambda s: s.plan.interval_km)

    # Araç bazlı özet
    by_vehicle: list[schemas.VehicleSummary] = []
    for v in vehicles:
        v_open = open_by_vehicle.get(v.id, [])
        pending_v = sum(1 for m in v_open if m.status == "pending")
        in_progress_v = sum(1 for m in v_open if m.status == "in_progress")
        completed_v = q.filter(models.Maintenance.vehicle_id == v.id, models.Maintenance.status == "completed").count()
        by_vehicle.append(
            schemas.VehicleSummary(
                vehicle=v,
                pending=pending_v,
                in_progress=in_progress_v,
                completed=completed_v,
                open_maintenances=len(v_open),
            )
        )
    by_vehicle.sort(key=lambda s: -(s.pending + s.in_progress))

    # Yalnızca admin/mühendis/amir için hesapla (usta ekranında anlamsız,
    # gereksiz sorgu yükü olmasın).
    unresolved_period_count = (
        _count_unresolved_period_items(db) if user.role in ("admin", "engineer", "amir") else 0
    )

    return schemas.DashboardOut(
        vehicle_count=vehicle_count,
        pending_count=pending_count,
        in_progress_count=in_progress_count,
        completed_count=completed_count,
        completed_this_month=completed_this_month,
        pending_user_approvals=pending_user_approvals,
        due_now=due_now,
        upcoming=upcoming,
        recent_completed=recent_completed,
        by_plan=by_plan,
        by_vehicle=by_vehicle,
        unresolved_period_count=unresolved_period_count,
    )


# ================================================================ Faz 18 — KPI
@app.get("/api/kpi", response_model=schemas.KPIOut)
def get_kpi(
    date_from: str | None = None,
    date_to: str | None = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_permission("view_dashboard_analytics")),
):
    """Filo genelinde KPI raporu (Faz 18): tamamlanma oranı/süresi, geciken/
    kritik bakım sayıları, araç/model bazlı ve personel/vardiya bazlı
    performans. date_from/date_to 'YYYY-MM-DD' — esnek tarih aralığı
    (Bakımlar sayfasındaki filtrelerle aynı ayrıştırma, bkz. _parse_date).

    Usta rolü, view_dashboard_analytics izni açık olsa bile (bu izin aynı
    zamanda Dashboard'daki analiz grafiğini de kontrol ettiği için varsayılan
    olarak açıktır) KPI raporunu GÖREMEZ — bu, yönetici rollerine (admin/
    mühendis/amir) özel, kasıtlı ve sabit bir kısıtlamadır."""
    if user.role == "technician":
        raise HTTPException(403, "KPI raporunu görüntüleme yetkiniz yok")
    df = _parse_date(date_from)
    dt_ = _parse_date(date_to, end=True)
    report = services.compute_kpi_report(db, df, dt_)
    report["top_vehicles"] = report.pop("vehicles")[:10]
    return schemas.KPIOut(**report)


@app.get("/api/kpi/export")
def export_kpi(
    date_from: str | None = None,
    date_to: str | None = None,
    db: Session = Depends(get_db),
    user: models.User = Depends(require_permission("export_excel")),
):
    """KPI raporunu çok sayfalı bir Excel dosyası olarak indirir (Faz 18).
    get_kpi ile aynı sebeple usta rolüne kapalıdır (bkz. yukarısı)."""
    if user.role == "technician":
        raise HTTPException(403, "KPI raporunu dışa aktarma yetkiniz yok")
    df = _parse_date(date_from)
    dt_ = _parse_date(date_to, end=True)
    report = services.compute_kpi_report(db, df, dt_)
    content = export_utils.build_kpi_workbook(report, title="KPI Raporu")
    services.log_action(db, user, "export", "system", None, "KPI raporu Excel'e aktarıldı")
    fname = export_utils.filename_for("kpi_raporu", "xlsx")
    return Response(
        content=content,
        media_type=export_utils.MEDIA_TYPES["xlsx"],
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


# ================================================================ Süre bazlı bakım — zamanlayıcı
async def _time_maintenance_background_loop():
    """Sunucu açıkken düzenli aralıklarla takvim gününü kontrol eder; gün
    değiştiyse süre bazlı (aylık/yıllık) bakımları değerlendirir — KM
    simülasyonu döngüsüyle aynı desen (bkz. services.run_daily_time_
    maintenance_check)."""
    while True:
        await asyncio.sleep(1800)  # 30 dakikada bir kontrol
        try:
            db = SessionLocal()
            try:
                opened = services.run_daily_time_maintenance_check(db)
                if opened > 0:
                    print(f"[SÜRE-BAKIM] {opened} yeni aday iş emri açıldı.")
            finally:
                db.close()
        except Exception as e:  # arka plan görevi asla çökmemeli
            print(f"[SÜRE-BAKIM] arka plan hatası: {e}")


@app.on_event("startup")
async def _time_maintenance_startup():
    """Açılışta bugün için henüz kontrol edilmediyse hemen kontrol et ve
    arka plan döngüsünü başlat."""
    db = SessionLocal()
    try:
        opened = services.run_daily_time_maintenance_check(db)
        if opened > 0:
            print(f"[SÜRE-BAKIM] Açılışta {opened} yeni aday iş emri açıldı.")
    except Exception as e:
        print(f"[SÜRE-BAKIM] açılış kontrolü hatası: {e}")
    finally:
        db.close()
    asyncio.create_task(_time_maintenance_background_loop())


@app.post("/api/time-maintenance/run-now")
def run_time_maintenance_now(db: Session = Depends(get_db), actor=Depends(require_admin)):
    """Süre bazlı (aylık/yıllık) bakım kontrolünü hemen çalıştırır — günlük
    otomatik kontrolden (bkz. services.run_daily_time_maintenance_check,
    "bugün zaten kontrol edildiyse atla" kısıtlamasına sahiptir) BAĞIMSIZ
    ve o kısıtlamayı bypass eder. Özellikle toplu katalog + araç hizmete
    başlama tarihi içe aktarıldıktan hemen sonra, gecelik zamanlayıcıyı
    beklemeden aday iş emirlerini görmek için (km simülasyonundaki "Şimdi
    Çalıştır" ile aynı desen)."""
    opened = services.check_and_open_time_maintenances_all_vehicles(db)
    if opened:
        services.log_action(
            db, actor, "update", "system", None,
            f"Süre bazlı bakım kontrolü elle tetiklendi: {len(opened)} yeni aday iş emri açıldı.",
        )
    return {"opened": len(opened)}


# ================================================================ Zamanlanmış bakım başlatma — zamanlayıcı
async def _scheduled_maintenance_start_background_loop():
    """Sunucu açıkken düzenli aralıklarla, admin/mühendisin "Başlat" ile
    ileri bir tarih seçtiği aday iş emirlerinden zamanı gelmiş olanları
    otomatik başlatır (bkz. services.run_scheduled_maintenance_starts) —
    diğer zamanlayıcılarla aynı desen."""
    while True:
        await asyncio.sleep(900)  # 15 dakikada bir kontrol
        try:
            db = SessionLocal()
            try:
                started = services.run_scheduled_maintenance_starts(db)
                if started:
                    print(f"[ZAMANLANMIŞ-BAKIM] {len(started)} iş emri otomatik başlatıldı.")
            finally:
                db.close()
        except Exception as e:  # arka plan görevi asla çökmemeli
            print(f"[ZAMANLANMIŞ-BAKIM] arka plan hatası: {e}")


@app.on_event("startup")
async def _scheduled_maintenance_start_startup():
    """Açılışta, sunucu kapalıyken zamanı gelmiş olabilecek zamanlanmış
    başlatmaları hemen kontrol eder ve arka plan döngüsünü başlatır."""
    db = SessionLocal()
    try:
        started = services.run_scheduled_maintenance_starts(db)
        if started:
            print(f"[ZAMANLANMIŞ-BAKIM] Açılışta {len(started)} iş emri otomatik başlatıldı.")
    except Exception as e:
        print(f"[ZAMANLANMIŞ-BAKIM] açılış kontrolü hatası: {e}")
    finally:
        db.close()
    asyncio.create_task(_scheduled_maintenance_start_background_loop())


@app.post("/api/maintenances/run-scheduled-starts")
def run_scheduled_maintenance_starts_now(db: Session = Depends(get_db), actor=Depends(require_admin)):
    """Zamanlanmış bakım başlatma kontrolünü hemen çalıştırır — 15 dakikalık
    arka plan döngüsünü beklemeden (km simülasyonu/süre bazlı bakımdaki
    "Şimdi Çalıştır" ile aynı desen, esas olarak test/destek amaçlı)."""
    started = services.run_scheduled_maintenance_starts(db)
    return {"started": len(started)}


# ================================================================ Günlük KM simülasyonu — zamanlayıcı
async def _km_sim_background_loop():
    """Sunucu açıkken düzenli aralıklarla takvim gününü kontrol eder ve gün
    değiştiyse kaçırılan günleri telafi eder. Bilgisayar gece kapalı olsa bile
    sunucu bir sonraki açılışında (startup telafisi) günleri yakalar."""
    while True:
        await asyncio.sleep(1800)  # 30 dakikada bir kontrol
        try:
            db = SessionLocal()
            try:
                advanced = services.run_km_sim_catchup(db)
                if advanced:
                    print(f"[KM-SIM] {advanced} gün telafi edildi.")
            finally:
                db.close()
        except Exception as e:  # arka plan görevi asla çökmemeli
            print(f"[KM-SIM] arka plan hatası: {e}")


@app.on_event("startup")
async def _km_sim_startup():
    """Açılışta kaçırılan günleri telafi et ve arka plan döngüsünü başlat."""
    db = SessionLocal()
    try:
        advanced = services.run_km_sim_catchup(db)
        if advanced:
            print(f"[KM-SIM] Açılışta {advanced} gün telafi edildi.")
    except Exception as e:
        print(f"[KM-SIM] açılış telafisi hatası: {e}")
    finally:
        db.close()
    asyncio.create_task(_km_sim_background_loop())


@app.on_event("startup")
def _auto_maintenance_open_startup():
    """Item 5 + Başlangıç km — "Başlat yok" tam otomatik bakım modeli.

    (0) Başlangıç (baseline) km backfill: baseline'ı olmayan araçlar için ŞU
        ANKİ km başlangıç kabul edilir; bu km'nin ALTINDA kalan bakımlar bir
        daha asla otomatik açılmaz (sistemden önce yapılmış sayılır).
    (1) YALNIZCA bu ilk backfill anında: o araçların baseline'ının altında
        kalan, tamamlanmamış (açık) geçmiş iş emirleri silinir — yanlışlıkla
        "Bakım Gecikti" görünmesinler. Tamamlanmış gerçek geçmişe dokunulmaz.
        (Sonraki açılışlarda bu temizlik ÇALIŞMAZ; admin'in elle eklediği
        baseline-altı bakımlar korunur.)
    (2) Gizli kalmış (is_activated=False) tüm iş emirlerini aktifleştirir.
    (3) Her araç için baseline'ın ÜSTÜNE gelen en yakın bakımı açar."""
    db = SessionLocal()
    try:
        # (0) baseline backfill — hangi araçların baseline'ı yeni atandı, kaydet.
        newly = db.query(models.Vehicle).filter(models.Vehicle.baseline_km.is_(None)).all()
        newly_ids = [v.id for v in newly]
        for v in newly:
            v.baseline_km = v.current_km
        db.commit()

        # (1) yalnızca yeni baseline atanan araçlar için geçmiş (baseline altı)
        #     açık iş emirlerini temizle.
        removed = 0
        if newly_ids:
            stale = (
                db.query(models.Maintenance)
                .join(models.Vehicle, models.Maintenance.vehicle_id == models.Vehicle.id)
                .filter(
                    models.Maintenance.vehicle_id.in_(newly_ids),
                    models.Maintenance.status != "completed",
                    models.Maintenance.target_km.isnot(None),
                    models.Vehicle.baseline_km.isnot(None),
                    models.Maintenance.target_km <= models.Vehicle.baseline_km,
                )
                .all()
            )
            removed = len(stale)
            for m in stale:
                db.delete(m)
            db.commit()

        # (2) "Başlat yok": gizli kalmış adayları görünür yap.
        activated = (
            db.query(models.Maintenance)
            .filter(models.Maintenance.is_activated == False)  # noqa: E712
            .update({models.Maintenance.is_activated: True}, synchronize_session=False)
        )
        db.commit()

        # (3) baseline'ın üstüne gelen en yakın bakımları aç.
        opened_total = 0
        for v in db.query(models.Vehicle).all():
            opened = services.check_and_open_maintenances(db, v)
            opened += services.check_and_open_time_maintenances(db, v)
            opened_total += len(opened)
        if newly_ids or removed or activated or opened_total:
            print(
                f"[OTOMATIK-BAKIM] Açılışta {len(newly_ids)} araca baseline km atandı, "
                f"{removed} geçmiş (baseline altı) iş emri silindi, "
                f"{activated} bekleyen iş emri aktifleştirildi, {opened_total} yeni bakım açıldı."
            )
    except Exception as e:  # açılış asla çökmemeli
        print(f"[OTOMATIK-BAKIM] açılış hatası: {e}")
    finally:
        db.close()


@app.on_event("startup")
def _permissions_startup():
    """Açılışta eksik izin satırlarını varsayılan değerlerle tamamlar."""
    db = SessionLocal()
    try:
        services.ensure_default_role_permissions(db)
    finally:
        db.close()


@app.on_event("startup")
def _shifts_startup():
    """Açılışta hiç vardiya yoksa 3 varsayılan vardiyayı oluşturur."""
    db = SessionLocal()
    try:
        services.ensure_default_shifts(db)
    finally:
        db.close()


@app.on_event("startup")
def _completed_km_backfill_startup():
    """Kullanıcı isteği: "şuan tamamlananlar için de tamamlandığı km
    üzerinden yine hesapla ve yaz" — bu özellik eklenmeden ÖNCE tamamlanmış
    km bazlı iş emirlerinde completed_km'yi bir kereliğine doldurur (bkz.
    services.backfill_completed_km_for_finished_maintenances — yalnızca
    completed_km IS NULL satırları işlediği için idempotenttir, her
    açılışta tekrar çalışması zararsızdır)."""
    db = SessionLocal()
    try:
        n = services.backfill_completed_km_for_finished_maintenances(db)
        if n:
            print(f"[COMPLETED_KM] {n} tamamlanmış iş emrinde completed_km/sapma % dolduruldu")
    finally:
        db.close()


# ================================================================ Otomatik Yedekleme — zamanlayıcı
async def _backup_background_loop():
    """Sunucu açıkken düzenli aralıklarla günlük yedeğin alınıp
    alınmadığını kontrol eder (bkz. backup_utils.run_scheduled_backup_if_due)."""
    while True:
        await asyncio.sleep(1800)  # 30 dakikada bir kontrol
        try:
            db = SessionLocal()
            try:
                path = backup_utils.run_scheduled_backup_if_due(db, engine, BACKUP_DIR)
                if path:
                    print(f"[BACKUP] Otomatik yedek alındı: {path.name}")
            finally:
                db.close()
        except Exception as e:  # arka plan görevi asla çökmemeli
            print(f"[BACKUP] arka plan hatası: {e}")


@app.on_event("startup")
def _backup_startup():
    """Açılışta gerekiyorsa hemen bir yedek alır (ör. hiç yedek yoksa) ve
    arka plan döngüsünü başlatır."""
    db = SessionLocal()
    try:
        path = backup_utils.run_scheduled_backup_if_due(db, engine, BACKUP_DIR)
        if path:
            print(f"[BACKUP] Açılışta yedek alındı: {path.name}")
    except Exception as e:
        print(f"[BACKUP] açılış yedeği hatası: {e}")
    finally:
        db.close()
    asyncio.create_task(_backup_background_loop())


# ================================================================ Statik dosyalar
# Yüklenen 3D modeller (.glb) ve diğer dosyalar
app.mount("/uploads", StaticFiles(directory=str(UPLOADS_DIR)), name="uploads")

# frontend/dist varsa arayüz de aynı sunucudan sunulur (tek komutla çalıştırma).
_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if _dist.is_dir():
    # index.html HER ZAMAN taze çekilsin — içindeki <script src> hash'i her
    # derlemede değişir; tarayıcı index.html'i önbelleğe alırsa (özellikle
    # localhost DIŞINDA farklı bir adresten/IP'den girildiğinde, ki bu ayrı
    # bir tarayıcı önbelleği demektir) güncellemeler görünmez. /assets/
    # altındaki dosyalar zaten hash'li isim taşıdığı için sonsuza kadar
    # önbelleklenebilir — yalnızca index.html'e no-store uygulanır.
    @app.get("/", include_in_schema=False)
    def _serve_index():
        return FileResponse(str(_dist / "index.html"), headers={"Cache-Control": "no-store, must-revalidate"})

    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="frontend")
