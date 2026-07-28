from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config

# Varsayılan: SQLite (kurulum gerektirmez).
# backend/.env dosyasından veya ortam değişkeninden okunur:
#   DATABASE_URL=postgresql+psycopg2://kullanici:sifre@sunucu:5432/ulasim_bakim
DATABASE_URL = config.get("DATABASE_URL", "sqlite:///./ulasim_bakim.db")
IS_SQLITE = DATABASE_URL.startswith("sqlite")

connect_args = {"check_same_thread": False} if IS_SQLITE else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

if IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):
        """Yıllarca çok kullanıcılı kullanımda 'database is locked'
        hatalarını en aza indirir:
        - WAL: okuyucular yazıcıyı, yazıcı okuyucuları bloklamaz
          (varsayılan rollback-journal modunda tüm dosya kilitlenir).
        - busy_timeout: eşzamanlı bir yazma zaten sürüyorsa hemen hata
          vermek yerine birkaç saniye bekleyip tekrar dener.
        - synchronous=NORMAL: WAL ile birlikte güvenli ve daha hızlı.
        - foreign_keys=ON: SQLite VARSAYILAN OLARAK yabancı anahtar
          zorlamasını kapalı tutar — models.py'deki ondelete="CASCADE"/
          "SET NULL" tanımları bu ayar olmadan fiilen hiçbir işe yaramaz
          (bütünlük tamamen uygulama koduna kalır). Her bağlantıda açıkça
          açılması gerekir (kalıcı bir veritabanı ayarı değildir). Mevcut
          tüm silme akışları (bkz. services.py/main.py) ya ilgili alt
          kayıtları önceden elle siliyor/NULL'luyor ya da SQLAlchemy'nin
          cascade="all, delete-orphan" ilişkileriyle bunu zaten
          yapıyordu — bu ayar onlarla çakışmaz, yalnızca DB seviyesinde
          ikinci bir güvenlik ağı ekler ve o ana kadar (pragma kapalıyken)
          fark edilmeyen birkaç öksüz kayıt senaryosunu (ör. bir araç
          silindiğinde ona bağlı alarm/öneri kayıtları) otomatik temizler."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
