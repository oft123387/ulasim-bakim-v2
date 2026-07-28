"""pytest ortak kurulumu.

ÖNEMLİ: Ortam değişkenleri (DATABASE_URL, SECRET_KEY, UPLOAD_DIR, BACKUP_DIR,
LOG_DIR) bu dosyanın EN ÜSTÜNDE, herhangi bir `app.*` modülü import
edilmeden ÖNCE ayarlanır. app/database.py ve app/main.py, DATABASE_URL'i
İLK import edildiklerinde (modül seviyesinde) okuyup veritabanı motorunu
oluşturuyor — bu yüzden env değişkenleri bir fixture içinde (gecikmeli
çalışır) değil, doğrudan burada, dosyanın en başında ayarlanmalı. Böylece
testler:
  - gerçek geliştirme veritabanına (backend/ulasim_bakim.db) DOKUNMAZ,
  - her çalıştırmada sıfırdan, izole bir geçici SQLite dosyasıyla başlar,
  - yüklenen dosyalar/yedekler/loglar gerçek backend/ klasörlerine değil
    geçici bir klasöre yazılır.
"""

import os
import tempfile
from pathlib import Path

_TMP_ROOT = Path(tempfile.mkdtemp(prefix="ulasim_bakim_test_"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_ROOT / 'test.db'}"
os.environ["SECRET_KEY"] = "pytest-test-secret-key-not-for-production"
os.environ["UPLOAD_DIR"] = str(_TMP_ROOT / "uploads")
os.environ["BACKUP_DIR"] = str(_TMP_ROOT / "backups")
os.environ["LOG_DIR"] = str(_TMP_ROOT / "logs")
os.environ["ALLOWED_ORIGIN"] = "*"

import io  # noqa: E402
import sys  # noqa: E402

import openpyxl  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from app import models  # noqa: E402
from app import rate_limit  # noqa: E402
from app.auth import hash_password  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.excel_utils import import_maintenance_tree  # noqa: E402
from app.main import app  # noqa: E402

# TestClient tüm istekleri tek bir "sahte" IP'den (testclient) ve gerçek bir
# HTTP round-trip'i olmadan, neredeyse anında gönderir — bu yüzden uçtan uca
# bir senaryo (yüzlerce istek) saniyeler içinde tamamlanır ve gerçek kullanımda
# hiç sorun olmayacak API hız sınırlayıcısına (dakikada 300 istek) takılır.
# Bu, iş mantığını değil güvenlik sertleştirmesini test ediyor olurdu — test
# oturumu için sınırı fiilen devre dışı bırakıyoruz.
rate_limit.api_limiter.max_events = 1_000_000
rate_limit.login_limiter.max_events = 1_000_000


def _seed_database() -> None:
    """seed.py ile aynı mantık: kullanıcılar + örnek araçlar + gerçek
    koruyucu bakım ağacının (data/koruyucu_bakim.xlsx) içe aktarılması —
    test senaryolarının çoğu bu gerçek ağacın yapısına (ARAÇ ÜSTÜ/İÇİ/ALTI,
    25.000/50.000/... km planları) dayanıyor."""
    db = SessionLocal()
    try:
        db.add_all(
            [
                models.User(
                    username="admin", full_name="Sistem Yöneticisi", role="admin",
                    password_hash=hash_password("admin123"),
                ),
                models.User(
                    username="usta", full_name="Ahmet Yılmaz", role="technician",
                    password_hash=hash_password("usta123"),
                ),
            ]
        )
        db.add_all(
            [
                models.Vehicle(code="3801", name="Tramvay 3801", current_km=1_000_000),
                models.Vehicle(code="3802", name="Tramvay 3802", current_km=487_000),
            ]
        )
        db.commit()

        xlsx_path = BACKEND_ROOT / "data" / "koruyucu_bakim.xlsx"
        with open(xlsx_path, "rb") as f:
            import_maintenance_tree(db, f.read())
    finally:
        db.close()


@pytest.fixture(scope="session")
def client():
    """Tüm test oturumu boyunca tek bir FastAPI TestClient + izole
    veritabanı. `with TestClient(app)` kullanmak, gerçek sunucu
    açılışındaki TÜM @app.on_event("startup") kancalarını (izin/vardiya
    varsayılanları, SECRET_KEY kontrolü, yedekleme/km-simülasyonu
    başlatıcıları) da tetikler — böylece testler gerçek sunucu davranışını
    olabildiğince yakından yansıtır."""
    Base.metadata.create_all(bind=engine)
    _seed_database()
    with TestClient(app) as c:
        yield c


def login(client: TestClient, username: str, password: str) -> str:
    r = client.post("/api/auth/login", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def admin_token(client):
    return login(client, "admin", "admin123")


@pytest.fixture(scope="session")
def usta_token(client):
    return login(client, "usta", "usta123")


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def leaves(items: list, acc: list | None = None) -> list:
    """Bir madde ağacında yalnızca en alt seviye (yaprak) maddeleri düz bir
    listeye toplar — test_e2e.py'deki yardımcıyla aynı."""
    if acc is None:
        acc = []
    for i in items:
        if i["children"]:
            leaves(i["children"], acc)
        else:
            acc.append(i)
    return acc


def make_xlsx(rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
