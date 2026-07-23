"""Parça Bilgi Bankası — kapak görsellerinden otomatik arka plan kaldırma.

Hafif bir yapay zeka modeli (u2netp, ~4.7MB) kullanır. Model ilk çağrıda
internetten indirilir ve önbelleğe alınır (bkz. Dockerfile/docker-compose.yml
'deki U2NET_HOME ortam değişkeni + model-cache volume — sayesinde konteyner
yeniden başlatılsa/yeniden derlense bile model tekrar tekrar indirilmez).

Bu özellik "best effort"tur: herhangi bir sebeple (internet yok, bozuk
görsel, model indirilemedi vb.) başarısız olursa orijinal görsel baytları
aynen döner — arka plan kaldırma başarısız olduğu için görsel yükleme
tamamen engellenmez.
"""

from io import BytesIO

try:
    # iPhone/iPad fotoğrafları varsayılan olarak .heic/.heif formatında olur;
    # tarayıcılar bu formatı doğrudan gösteremez (yalnızca Safari destekler).
    # Bu kaydı yapınca PIL.Image.open (rembg dahil, uygulama genelinde) HEIC/
    # HEIF dosyalarını da açabilir hale gelir.
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:
    pass

_HEIC_EXTS = {".heic", ".heif"}


def convert_to_browser_image(content: bytes, ext: str) -> tuple[bytes, str]:
    """.heic/.heif gibi tarayıcının doğrudan gösteremediği formatları PNG'ye
    çevirir; diğer formatlarda dokunmaz. Dönüştürme başarısız olursa (ör.
    pillow-heif kurulu değil/bozuk dosya) orijinal bayt+uzantıyı aynen döner
    — en azından dosya kaybolmaz, indirilebilir kalır."""
    ext = ext.lower()
    if ext not in _HEIC_EXTS:
        return content, ext
    try:
        from PIL import Image

        img = Image.open(BytesIO(content)).convert("RGB")
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue(), ".png"
    except Exception:
        return content, ext


def remove_background(image_bytes: bytes) -> bytes:
    try:
        from PIL import Image
        from rembg import new_session, remove

        session = _get_session()
        out = remove(image_bytes, session=session)
        img = Image.open(BytesIO(out)).convert("RGBA")
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return image_bytes


_session = None


def _get_session():
    global _session
    if _session is None:
        from rembg import new_session

        _session = new_session("u2netp")
    return _session
