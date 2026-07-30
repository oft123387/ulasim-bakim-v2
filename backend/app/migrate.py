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
        ("scheduled_start_at", "DATE"),
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


def _migrate_suggestions_hierarchical_approval(engine: Engine, existing_tables: set[str]) -> None:
    """Faz 30 — Öneriler artık (a) belirli bir araca değil bir ARAÇ
    MODELİNE (vehicle_model — Bozankaya/Sirio) bağlanabiliyor VE (b) tek
    aşamalı (tek onaylayan) yerine İKİ aşamalı hiyerarşik onaydan geçiyor:
    öneri önce AMİR'e düşer, amir onaylarsa MÜHENDİS'e düşer, amir
    reddederse mühendise hiç düşmez.

    Bunun için suggestions.vehicle_id artık NULL olabilmeli (bir modele
    bağlı öneride belirli bir araç yok) — SQLite'ta ALTER TABLE ile mevcut
    bir sütunun NOT NULL kısıtı DOĞRUDAN kaldırılamaz (yalnızca sütun
    EKLENEBİLİR), bu yüzden SQLite'ın resmi olarak belgelediği "tabloyu
    yeniden oluştur" tekniği kullanılır: doğru kısıtlı yeni bir tablo
    oluşturulur, TÜM mevcut veriler eski (tek aşamalı) status'e göre
    akıllıca yeni alanlara eşlenerek kopyalanır, eski tablo silinir, yenisi
    eski adına yeniden adlandırılır. `vehicle_model` sütunu zaten varsa
    (daha önce migrate edilmişse) hiçbir şey yapılmaz — idempotenttir."""
    if "suggestions" not in existing_tables:
        return  # tablo henüz yok, create_all zaten doğru şemayla oluşturacak
    inspector = inspect(engine)
    existing_cols = {c["name"] for c in inspector.get_columns("suggestions")}
    if "vehicle_model" in existing_cols:
        return  # zaten migrate edilmiş

    with engine.begin() as conn:
        conn.execute(text(
            """
            CREATE TABLE suggestions_new (
                id INTEGER PRIMARY KEY,
                vehicle_id INTEGER REFERENCES vehicles(id) ON DELETE CASCADE,
                vehicle_model VARCHAR(100) DEFAULT '',
                plan_id INTEGER REFERENCES maintenance_plans(id) ON DELETE SET NULL,
                text TEXT NOT NULL,
                status VARCHAR(20) DEFAULT 'pending',
                admin_note TEXT DEFAULT '',
                created_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                created_at DATETIME,
                reviewed_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                reviewed_at DATETIME,
                amir_status VARCHAR(20) DEFAULT 'pending',
                amir_reviewed_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                amir_reviewed_at DATETIME,
                amir_note TEXT DEFAULT '',
                engineer_status VARCHAR(20) DEFAULT 'pending',
                engineer_reviewed_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                engineer_reviewed_at DATETIME,
                engineer_note TEXT DEFAULT ''
            )
            """
        ))
        # Eski tek-aşamalı status -> yeni iki aşamalı alanlara eşleme:
        # rejected -> amir_status='rejected' (en erken kapı, geriye dönük en
        #             mantıklı varsayım), engineer hiç başlamamış sayılır;
        # approved -> her iki aşama da onaylanmış (zaten kabul edilmiş bir
        #             öneriyi yeniden değerlendirmeye zorlamamak için);
        # added    -> aynı şekilde ama mühendis aşaması 'added';
        # pending  -> ikisi de sıfırdan 'pending'.
        conn.execute(text(
            """
            INSERT INTO suggestions_new (
                id, vehicle_id, vehicle_model, plan_id, text, status, admin_note,
                created_by_id, created_at, reviewed_by_id, reviewed_at,
                amir_status, amir_reviewed_by_id, amir_reviewed_at, amir_note,
                engineer_status, engineer_reviewed_by_id, engineer_reviewed_at, engineer_note
            )
            SELECT
                id, vehicle_id, '', plan_id, text, status, admin_note,
                created_by_id, created_at, reviewed_by_id, reviewed_at,
                CASE status
                    WHEN 'rejected' THEN 'rejected'
                    WHEN 'approved' THEN 'approved'
                    WHEN 'added' THEN 'approved'
                    ELSE 'pending'
                END,
                CASE WHEN status IN ('approved', 'added', 'rejected') THEN reviewed_by_id END,
                CASE WHEN status IN ('approved', 'added', 'rejected') THEN reviewed_at END,
                '',
                CASE status
                    WHEN 'approved' THEN 'approved'
                    WHEN 'added' THEN 'added'
                    ELSE 'pending'
                END,
                CASE WHEN status IN ('approved', 'added') THEN reviewed_by_id END,
                CASE WHEN status IN ('approved', 'added') THEN reviewed_at END,
                ''
            FROM suggestions
            """
        ))
        conn.execute(text("DROP TABLE suggestions"))
        conn.execute(text("ALTER TABLE suggestions_new RENAME TO suggestions"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_suggestions_vehicle_id ON suggestions (vehicle_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_suggestions_status ON suggestions (status)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_suggestions_created_at ON suggestions (created_at)"))


def run_light_migrations(engine: Engine) -> None:
    if not engine.url.get_backend_name().startswith("sqlite"):
        return  # yalnızca SQLite için otomatik migration uygulanır

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    _migrate_suggestions_hierarchical_approval(engine, existing_tables)

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
