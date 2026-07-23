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
"""

import smtplib
from email.mime.text import MIMEText

from . import config

APP_NAME = "Ulaşım Bakım Yönetim Sistemi"


def is_configured() -> bool:
    return bool(config.get("SMTP_HOST", ""))


def send_email(to_addr: str, subject: str, body: str) -> bool:
    host = config.get("SMTP_HOST", "")
    if not host:
        print(f"[email devre dışı — SMTP yapılandırılmamış] to={to_addr} subject={subject}\n{body}")
        return False

    port = int(config.get("SMTP_PORT", "587") or "587")
    user = config.get("SMTP_USER", "")
    # Gmail "Uygulama Şifresi" ekranda okunabilirlik için boşluklu gösterilir
    # (ör. "abcd efgh ijkl mnop"); kopyala-yapıştırda bu boşluklar genelde
    # olduğu gibi gelir. Google boşluksuz hali de kabul eder, biz burada
    # temizleyerek her iki şekilde girilse de çalışmasını sağlıyoruz.
    password = config.get("SMTP_PASSWORD", "").replace(" ", "")
    from_addr = config.get("SMTP_FROM", "") or user or to_addr

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr

    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls()
            if user:
                server.login(user, password)
            server.sendmail(from_addr, [to_addr], msg.as_string())
        return True
    except Exception as e:  # noqa: BLE001 — e-posta hatası isteği kesmemeli
        print(f"[email gönderilemedi] to={to_addr}: {e}")
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
