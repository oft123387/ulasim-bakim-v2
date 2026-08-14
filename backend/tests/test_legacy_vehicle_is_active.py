"""Eski `vehicles.is_active` sütununun otomatik temizlenmesi.

Projenin ilk sürümlerinde Vehicle modelinde `is_active` alanı vardı ve tablo
`is_active BOOLEAN NOT NULL` (varsayılansız) olarak oluşturulmuştu. Alan
modelden çıkarıldı ama SQLite'ta sütun yerinde kaldığı için ESKİ
veritabanlarında yeni araç eklemek imkânsız hale gelmişti:

    sqlite3.IntegrityError: NOT NULL constraint failed: vehicles.is_active

Excel'den araç/km içe aktarırken dosyada sistemde olmayan bir araç kodu
geçtiğinde tüm yükleme 500 ile düşüyordu. Bu test, eski şemayı birebir taklit
edip migration'ın sorunu çözdüğünü doğrular.
"""

from sqlalchemy import inspect, text

from app.database import engine
from app.migrate import run_light_migrations

from .conftest import auth


def _add_legacy_column() -> None:
    """Eski şemayı taklit et: NOT NULL, varsayılansız is_active sütunu.

    SQLite var olan bir tabloya NOT NULL sütunu ancak DEFAULT ile ekletir;
    asıl sorunlu durum (INSERT'te değer gönderilmemesi) yine de oluşur çünkü
    SQLAlchemy modeli bu sütunu bilmez ve sütun listesine hiç almaz — ama
    DEFAULT varken SQLite değeri kendisi doldurur. Bu yüzden gerçek üretim
    şemasına sadık kalmak için sütunu DEFAULT'suz oluşturabilmek adına tablo
    yeniden yazılır.
    """
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE vehicles ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"))
        # DEFAULT'u şemadan düşürmek için tabloyu yeniden yaz (üretimdeki
        # eski şema DEFAULT içermiyordu).
        conn.execute(text("PRAGMA writable_schema = ON"))
        conn.execute(text(
            "UPDATE sqlite_master SET sql = replace(sql, 'BOOLEAN NOT NULL DEFAULT 1', "
            "'BOOLEAN NOT NULL') WHERE type = 'table' AND name = 'vehicles'"
        ))
        conn.execute(text("PRAGMA writable_schema = OFF"))


def test_legacy_is_active_column_is_dropped_and_vehicle_creation_works(client, admin_token):
    admin = admin_token

    _add_legacy_column()
    assert "is_active" in {c["name"] for c in inspect(engine).get_columns("vehicles")}

    run_light_migrations(engine)

    assert "is_active" not in {c["name"] for c in inspect(engine).get_columns("vehicles")}

    # Migration sonrası yeni araç eklenebilmeli (asıl hatanın yaşandığı yol).
    r = client.post(
        "/api/vehicles",
        headers=auth(admin),
        json={"code": "9991", "name": "Legacy Test", "current_km": 1000, "model": "Legacy Model"},
    )
    assert r.status_code == 200, r.text


def test_migration_is_idempotent(client, admin_token):
    """Sütun zaten yoksa migration hata vermeden geçmeli (her açılışta çalışır)."""
    run_light_migrations(engine)
    run_light_migrations(engine)
    assert "is_active" not in {c["name"] for c in inspect(engine).get_columns("vehicles")}
