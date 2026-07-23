"""QR kod üretimi (Faz 22 — Araç ve Parça QR kodları).

Her araç ve her Parça Bilgi Bankası maddesi için, telefonla okutulunca
uygulamayı doğrudan o kaydın sayfasına (araçta: bakım geçmişi/profil,
parçada: parça bilgi/doküman sayfası) açan bir QR kod üretilir. Kod isteği
yapan tarayıcının o an kullandığı adres (localhost / LAN IP / alan adı
fark etmez — request.base_url) üzerinden üretildiğinden, ekstra bir
yapılandırmaya gerek kalmaz."""

from __future__ import annotations

import io

import qrcode
from qrcode.constants import ERROR_CORRECT_M

# Uygulamanın kendi renk paletiyle tutarlı (Badge/Excel başlık rengiyle aynı).
_QR_FILL = "#1B4DA0"
_QR_BACK = "#FFFFFF"


def build_qr_png(data: str, box_size: int = 9, border: int = 3) -> bytes:
    """Verilen metni/URL'yi PNG bayt dizisi olarak QR koda dönüştürür."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color=_QR_FILL, back_color=_QR_BACK)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
