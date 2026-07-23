"""Otomatik veritabanı yedekleme (yalnızca SQLite).

Sistem yıllarca kesintisiz kullanılacağı için iki ayrı riske karşı önlem
alır:

1. Veri kaybı — canlı veritabanı dosyasının bulunduğu volume bozulur/silinirse
   (insan hatası, disk arızası, yanlış `docker volume rm` vb.) elde ayrı bir
   volume'de (backups) güncel bir kopya kalır.
2. Aşırı disk şişmesi — hem canlı veritabanı periyodik olarak VACUUM ile
   sıkıştırılır (silinen kayıtların bıraktığı boş sayfalar geri kazanılır),
   hem de yedek dosyaları belirli bir sayının (BACKUP_KEEP) üzerine
   çıkınca en eskiden başlanarak otomatik silinir.

PostgreSQL kullanılıyorsa (üretimde büyük ölçek için önerilir) bu modül
devre dışı kalır — o durumda yedekleme pg_dump/managed servis üzerinden
ayrıca yapılandırılmalıdır.
"""

import sqlite3
from datetime import date, datetime
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from . import services

BACKUP_KEEP = 30  # kaç yedek dosyası saklanır (fazlası otomatik silinir)
BACKUP_FILE_PREFIX = "ulasim_bakim_"
BACKUP_HOUR_THRESHOLD = 3  # günlük yedek, saat 03:00'ten sonraki ilk kontrolde alınır


def _sqlite_path_from_engine(engine: Engine) -> Path | None:
    url = str(engine.url)
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        return None
    return Path(url[len(prefix):])


def create_backup(engine: Engine, backup_dir: Path) -> Path | None:
    """Canlı veritabanını sıkıştırıp (VACUUM) ayrı bir yedek klasörüne,
    zaman damgalı bir dosya olarak kopyalar. SQLite'ın online backup
    API'sini kullanır — uygulama çalışırken güvenle çalıştırılabilir,
    diğer isteklerin başarısız olmasına yol açmaz."""
    src_path = _sqlite_path_from_engine(engine)
    if src_path is None or not src_path.is_file():
        return None

    backup_dir.mkdir(parents=True, exist_ok=True)

    # Canlı veritabanını sıkıştır — yıllar içinde tamamlanan/silinen
    # kayıtların bıraktığı boş sayfaları geri kazanır.
    with sqlite3.connect(str(src_path), timeout=30) as conn:
        conn.execute("VACUUM")

    # Mikrosaniye dahil — aynı saniye içinde birden fazla yedek tetiklenirse
    # (ör. açılış yedeği + hemen ardından elle "Şimdi Yedekle") dosya adı
    # çakışıp bir öncekinin üzerine yazılmasın diye.
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest_path = backup_dir / f"{BACKUP_FILE_PREFIX}{stamp}.db"
    # Yine de bir çakışma olursa (aynı mikrosaniye), sayaç ekleyerek kaçın.
    n = 1
    while dest_path.exists():
        dest_path = backup_dir / f"{BACKUP_FILE_PREFIX}{stamp}_{n}.db"
        n += 1

    src_conn = sqlite3.connect(str(src_path), timeout=30)
    dest_conn = sqlite3.connect(str(dest_path))
    try:
        src_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        src_conn.close()

    return dest_path


def prune_old_backups(backup_dir: Path, keep: int = BACKUP_KEEP) -> int:
    """En yeni `keep` yedeği bırakır, daha eskilerini siler.
    Dönüş: silinen dosya sayısı."""
    if not backup_dir.is_dir():
        return 0
    files = sorted(
        backup_dir.glob(f"{BACKUP_FILE_PREFIX}*.db"), key=lambda p: p.name, reverse=True
    )
    removed = 0
    for f in files[keep:]:
        try:
            f.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def list_backups(backup_dir: Path) -> list[dict]:
    if not backup_dir.is_dir():
        return []
    out: list[dict] = []
    for f in sorted(backup_dir.glob(f"{BACKUP_FILE_PREFIX}*.db"), key=lambda p: p.name, reverse=True):
        stat = f.stat()
        out.append(
            {
                "filename": f.name,
                "size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            }
        )
    return out


def run_backup_now(
    db: Session, engine: Engine, backup_dir: Path, actor=None, note: str = "elle"
) -> Path | None:
    """Yedeği hemen alır, eskileri temizler, ayarı günceller ve audit
    kaydı düşer. Dönüş: oluşturulan dosyanın yolu (SQLite değilse None)."""
    path = create_backup(engine, backup_dir)
    if path is None:
        return None
    removed = prune_old_backups(backup_dir)
    services.set_setting(db, "last_backup_date", date.today().isoformat())
    db.commit()
    services.log_action(
        db, actor, "backup", "system", None,
        f"Veritabanı yedeği alındı: {path.name}",
        detail=f"{removed} eski yedek silindi ({note})",
    )
    return path


def run_scheduled_backup_if_due(db: Session, engine: Engine, backup_dir: Path) -> Path | None:
    """Günde en fazla bir kez, saat 03:00'ten sonraki ilk kontrolde
    (ya da hiç yedek yoksa hemen) otomatik yedek alır. Sunucu her zaman
    açık kalmasa bile (ör. gece kapatılırsa) bir sonraki açılışta veya
    30 dakikalık kontrol döngüsünde yakalanır."""
    last = services.get_setting(db, "last_backup_date", "") or ""
    today = date.today().isoformat()
    if last == today:
        return None
    has_any = any(backup_dir.glob(f"{BACKUP_FILE_PREFIX}*.db")) if backup_dir.is_dir() else False
    if not has_any or datetime.now().hour >= BACKUP_HOUR_THRESHOLD:
        return run_backup_now(db, engine, backup_dir, actor=None, note="otomatik günlük")
    return None
