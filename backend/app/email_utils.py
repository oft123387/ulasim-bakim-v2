"""Doğrulama kodu / şifre sıfırlama e-postalarını göndermek için ince bir
SMTP sarmalayıcı. Gmail dahil herhangi bir SMTP sağlayıcısıyla çalışır
(Gmail için 'Uygulama Şifresi' — App Password — kullanılması gerekir,
normal hesap şifresi SMTP AUTH için genelde reddedilir).

Yapılandırma backend/.env üzerinden yapılır:
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USER=ornek@gmail.com
    SMTP_PASSWORD=uygulama-sifresi
    SMTP_FROM=ornek@gmail.com          (boşsa SMTP_USER kullanılır)

SMTP_HOST tanımlı değilse (yerel/deneme ortamı), e-posta gönderilmez;
kod yerine sunucu konsoluna yazılır ki geliştirme sırasında akış kesilmesin.
Bu durumda gönderim fonksiyonu False döner — çağıran taraf isterse kullanıcıya
"e-posta gönderilemedi, sistem yöneticinize danışın" gibi bir uyarı gösterebilir.

Kullanıcı isteği: e-postanın en üstünde (metnin üzerinde) Kayseri Ulaşım
banner görseli görünsün. Bunun için gövde artık düz metnin yanında, aynı
metni banner görseliyle birlikte gösteren bir HTML sürümüyle de gönderilir
(multipart/alternative — Gmail HTML'i gösterir, düz metin istemcileri
alternatif olarak eski davranışı korur). Banner, harici bir URL'ye değil,
e-postaya CID ile GÖMÜLÜ (inline) bir görsele referans verir ki resim
engellemeyen hiçbir istemcide kırık görsel/harici yükleme uyarısı çıkmasın.
"""

import os
import smtplib
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from . import config
from .logging_setup import logger

APP_NAME = "Ulaşım Bakım Yönetim Sistemi"

# Kullanıcının yüklediği Kayseri Ulaşım banner görseli — bkz. yukarıdaki
# yorum. Dosya yoksa (ör. banner değiştirilip silinirse) e-posta sorunsuzca
# düz metne geri döner, gönderim asla banner yüzünden başarısız olmaz.
_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
_BANNER_PATH = os.path.join(_ASSETS_DIR, "email_banner.jpg")
_BANNER_CID = "ulasim-bakim-banner"


def is_configured() -> bool:
    return bool(config.get("SMTP_HOST", ""))


def config_summary() -> dict:
    """SMTP ayarlarının teşhis için güvenli özeti — şifre ASLA döndürülmez,
    yalnızca girilip girilmediği ve uzunluğu bildirilir (bkz. smtp_test.py ve
    /api/system/status)."""
    password = config.get("SMTP_PASSWORD", "")
    return {
        "host": config.get("SMTP_HOST", "") or "(boş)",
        "port": config.get("SMTP_PORT", "587") or "587",
        "user": config.get("SMTP_USER", "") or "(boş)",
        "from": config.get("SMTP_FROM", "") or "(boş — SMTP_USER kullanılacak)",
        "password_set": bool(password),
        "password_len_stripped": len(password.replace(" ", "")),
        "banner_found": os.path.isfile(_BANNER_PATH),
    }


def _html_body(body: str) -> str:
    """Düz metin gövdeyi, üstte banner görseliyle birlikte basit bir HTML
    e-postaya çevirir. Kod gibi boşluklu/girintili satırların göründüğü gibi
    kalması için <pre> benzeri davranış yerine satır sonları <br>'a çevrilir
    (boşluklar &nbsp; ile korunur ki doğrulama kodu ortalanmış görünsün)."""
    escaped = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html_lines = []
    for line in escaped.split("\n"):
        leading = len(line) - len(line.lstrip(" "))
        line = ("&nbsp;" * leading) + line[leading:]
        html_lines.append(line)
    html_text = "<br>".join(html_lines)
    return f"""\
<!DOCTYPE html>
<html lang="tr">
  <body style="margin:0; padding:0; background:#f2f4f7; font-family: Arial, Helvetica, sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f2f4f7; padding:24px 0;">
      <tr>
        <td align="center">
          <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px; width:100%; background:#ffffff; border-radius:10px; overflow:hidden; box-shadow:0 2px 10px rgba(0,0,0,0.08);">
            <tr>
              <td>
                <img src="cid:{_BANNER_CID}" alt="{APP_NAME}" width="600" style="display:block; width:100%; height:auto; border:0;" />
              </td>
            </tr>
            <tr>
              <td style="padding:28px 32px; color:#1a1a1a; font-size:14.5px; line-height:1.7;">
                {html_text}
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""


def send_email(to_addr: str, subject: str, body: str) -> bool:
    host = config.get("SMTP_HOST", "")
    if not host:
        # SMTP_HOST yoksa gönderim yapılmaz. Bu satır ÖNEMLİ bir teşhis
        # ipucudur: sunucuda mail hiç gitmiyorsa ilk bakılacak yer budur —
        # backend/.env'de SMTP_HOST var mı, docker compose onu env_file ile
        # konteynere aktarmış mı (bkz. docker-compose.yml).
        logger.warning(
            "E-posta gönderilmedi: SMTP yapılandırılmamış (SMTP_HOST boş). "
            "alıcı=%s konu=%s", to_addr, subject
        )
        logger.info("[SMTP kapalı — kod konsola yazıldı]\n%s", body)
        return False

    port = int(config.get("SMTP_PORT", "587") or "587")
    user = config.get("SMTP_USER", "")
    # Gmail "Uygulama Şifresi" ekranda okunabilirlik için boşluklu gösterilir
    # (ör. "abcd efgh ijkl mnop"); kopyala-yapıştırda bu boşluklar genelde
    # olduğu gibi gelir. Google boşluksuz hali de kabul eder, biz burada
    # temizleyerek her iki şekilde girilse de çalışmasını sağlıyoruz.
    password = config.get("SMTP_PASSWORD", "").replace(" ", "")
    from_addr = config.get("SMTP_FROM", "") or user or to_addr

    has_banner = os.path.isfile(_BANNER_PATH)
    if has_banner:
        # multipart/related( multipart/alternative(plain, html), image )
        # — e-posta istemcisi HTML'i destekliyorsa banner'lı sürümü, aksi
        # halde düz metni gösterir; ikisi de RFC uyumlu tek e-postada gider.
        msg = MIMEMultipart("related")
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(body, "plain", "utf-8"))
        alt.attach(MIMEText(_html_body(body), "html", "utf-8"))
        msg.attach(alt)
        with open(_BANNER_PATH, "rb") as f:
            img = MIMEImage(f.read())
        img.add_header("Content-ID", f"<{_BANNER_CID}>")
        img.add_header("Content-Disposition", "inline", filename="banner.jpg")
        msg.attach(img)
    else:
        msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr

    # NOT: 465 numaralı port "implicit SSL"dir — starttls ile konuşulmaz,
    # doğrudan SMTP_SSL ile bağlanılması gerekir. Önceden bu ayrım yoktu ve
    # SMTP_PORT=465 girildiğinde bağlantı sessizce zaman aşımına uğruyordu.
    use_ssl = port == 465
    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
        with server:
            if not use_ssl:
                server.starttls()
            if user:
                server.login(user, password)
            server.sendmail(from_addr, [to_addr], msg.as_string())
        logger.info("E-posta gönderildi: alıcı=%s konu=%s", to_addr, subject)
        return True
    except smtplib.SMTPAuthenticationError as e:
        # Gmail'de en sık sebep: normal hesap şifresi girilmiş olması.
        # SMTP AUTH için 2 Adımlı Doğrulama açık + "Uygulama Şifresi" şart.
        logger.error(
            "E-posta gönderilemedi (KİMLİK DOĞRULAMA): alıcı=%s kullanıcı=%s hata=%s — "
            "Gmail kullanıyorsanız SMTP_PASSWORD normal şifre DEĞİL, 16 haneli "
            "Uygulama Şifresi olmalı.", to_addr, user, e
        )
        return False
    except (OSError, smtplib.SMTPException) as e:
        # Zaman aşımı / bağlantı reddi genelde sunucunun 587 numaralı porta
        # giden trafiğinin kurumsal güvenlik duvarınca engellenmesidir.
        logger.error(
            "E-posta gönderilemedi (BAĞLANTI/SMTP): alıcı=%s sunucu=%s:%s hata=%s: %s",
            to_addr, host, port, type(e).__name__, e
        )
        return False
    except Exception as e:  # noqa: BLE001 — e-posta hatası isteği kesmemeli
        logger.exception("E-posta gönderilemedi (beklenmeyen): alıcı=%s hata=%s", to_addr, e)
        return False


def send_verification_code(to_addr: str, code: str, purpose: str) -> bool:
    if purpose == "reset_password":
        subject = f"{APP_NAME} — Şifre Sıfırlama Kodu"
        intro = "Şifrenizi sıfırlamak için aşağıdaki kodu kullanın:"
    else:
        subject = f"{APP_NAME} — E-posta Doğrulama Kodu"
        intro = "E-posta adresinizi doğrulamak için aşağıdaki kodu kullanın:"
    body = (
        f"{intro}\n\n"
        f"    {code}\n\n"
        f"Bu kod {15} dakika süreyle geçerlidir. Bu isteği siz yapmadıysanız "
        f"bu e-postayı yok sayabilirsiniz.\n\n"
        f"— {APP_NAME}"
    )
    return send_email(to_addr, subject, body)
