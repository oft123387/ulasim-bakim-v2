"""Hafif otomatik migration: var olan SQLite veritabanına yeni sürümde
eklenen sütunları ekler. Alembic gerektirmez; yalnızca ekleme yapar,
veri kaybına yol açmaz. PostgreSQL kullanılıyorsa (üretim) atlanır —
o durumda migration'lar elle/Alembic ile yönetilmelidir.
"""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

# tablo -> [(sütun adı, SQL tipi ve varsayılan değeri), ...]
_NEW_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "vehicles": [
        ("manufacturer", "VARCHAR(100) DEFAULT ''"),
        ("model", "VARCHAR(100) DEFAULT ''"),
        ("chassis_no", "VARCHAR(100) DEFAULT ''"),
        ("notes", "VARCHAR(500) DEFAULT ''"),
        ("last_km_update_at", "DATETIME"),
        ("production_year", "INTEGER"),
        ("photo_filename", "VARCHAR(300) DEFAULT ''"),
        ("commissioning_date", "DATE"),
    ],
    "users": [
        ("email", "VARCHAR(200) DEFAULT ''"),
        ("email_verified", "BOOLEAN DEFAULT 0"),
        ("shift_id", "INTEGER"),
    ],
    "tree_nodes": [
        ("plan_id", "INTEGER"),
        ("note", "TEXT DEFAULT ''"),
        ("card_code", "VARCHAR(50) DEFAULT ''"),
    ],
    "maintenance_items": [
        ("source_node_id", "INTEGER"),
        ("tiers", "VARCHAR(100) DEFAULT ''"),
        ("exec_note", "TEXT DEFAULT ''"),
        ("exec_note_by_id", "INTEGER"),
        ("exec_note_at", "DATETIME"),
    ],
    "maintenance_plans": [
        ("vehicle_model", "VARCHAR(100) DEFAULT ''"),
        ("threshold_type", "VARCHAR(10) DEFAULT 'percent'"),
        ("lower_km", "FLOAT DEFAULT 0.0"),
        ("upper_km", "FLOAT DEFAULT 0.0"),
        ("plan_type", "VARCHAR(10) DEFAULT 'km'"),
        ("interval_months", "INTEGER"),
        ("is_catalog_only", "BOOLEAN DEFAULT 0"),
    ],
    "part_catalog_items": [
        ("parent_id", "INTEGER"),
        # FLOAT (INTEGER değil): kesirli stok miktarlarını (ör. 1 kg'lık bir
        # üründen 0.2 kg kullanımı) desteklemek için. SQLite'ta tür affinity'si
        # esnek olduğu için önceden INTEGER olarak eklenmiş bir sütun da
        # kesirli değerleri kayıpsız saklar — bu satır yalnızca SIFIRDAN
        # kurulan veritabanlarında doğru türle oluşturulmasını sağlar.
        ("stock_qty", "FLOAT DEFAULT 0"),
        ("min_stock", "FLOAT DEFAULT 0"),
        ("unit", "VARCHAR(20) DEFAULT 'adet'"),
    ],
    "maintenances": [
        # ÖNEMLİ: default '1' (TRUE) — bu sütun eklenmeden ÖNCE açılmış tüm
        # mevcut iş emirleri geriye dönük uyumluluk için otomatik olarak
        # "başlatılmış" sayılır (aksi halde bu migration'dan sonra sunucu
        # yeniden başlatılınca kullanıcının önünde olan tüm bakımlar aniden
        # Bakımlar/Takvim'den kaybolurdu). Yalnızca BUNDAN SONRA
        # check_and_open_maintenances ile açılan YENİ adaylar kod tarafında
        # açıkça is_activated=False verilerek "onay bekliyor" durumunda başlar.
        ("is_activated", "BOOLEAN DEFAULT 1"),
        ("activated_at", "DATETIME"),
        ("activated_by_id", "INTEGER"),
        ("target_date", "DATE"),
    ],
    "alarm_states": [
        ("emailed_at", "DATETIME"),
    ],
    "audit_logs": [
        ("old_value", "TEXT DEFAULT ''"),
        ("new_value", "TEXT DEFAULT ''"),
        ("ip_address", "VARCHAR(64) DEFAULT ''"),
        ("user_agent", "VARCHAR(300) DEFAULT ''"),
    ],
}

_NEW_TABLES_HANDLED_BY_CREATE_ALL = True  # diagram_images / hotspots create_all ile oluşur


def run_light_migrations(engine: Engine) -> None:
    if not engine.url.get_backend_name().startswith("sqlite"):
        return  # yalnızca SQLite için otomatik migration uygulanır

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    # Güvenlik notu (Faz 23 denetimi): buradaki f-string yalnızca yukarıdaki
    # _NEW_COLUMNS sabitinden (kaynak kodda gömülü, kullanıcı girdisi
    # OLMAYAN) tablo/sütun adlarıyla oluşturulur — SQL injection riski yoktur.
    with engine.begin() as conn:
        for table, columns in _NEW_COLUMNS.items():
            if table not in existing_tables:
                continue  # tablo henüz yok, create_all zaten doğru şemayla oluşturacak
            current_cols = {c["name"] for c in inspector.get_columns(table)}
            for col_name, col_def in columns:
                if col_name in current_cols:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"))
