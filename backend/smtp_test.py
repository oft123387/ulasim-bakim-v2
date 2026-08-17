"""SMTP teşhis betiği — "mail gitmiyor" sorununu adım adım daraltır.

Kullanım (sunucuda, çalışan konteynerin İÇİNDE — uygulamanın gördüğü
ayarlarla test etmek için önemlidir):

    docker compose exec app python -m backend.smtp_test
    docker compose exec app python -m backend.smtp_test alici@ornek.com

Docker'sız (Windows/geliştirme):

    cd backend && python smtp_test.py

Adresi verirseniz gerçek bir deneme e-postası gönderir; vermezseniz
yalnızca bağlantı ve kimlik doğrulamayı test eder.

Hiçbir zaman şifreyi ekrana yazmaz.
"""

from __future__ import annotations

import smtplib
import socket
import ssl
import sys

try:  # paket olarak (-m backend.smtp_test)
    from app import config, email_utils
except ImportError:  # doğrudan (python smtp_test.py)
    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    from app import config, email_utils


def main() -> int:
    to_addr = sys.argv[1] if len(sys.argv) > 1 else ""

    print("=" * 62)
    print(" SMTP TEŞHİS")
    print("=" * 62)

    cfg = email_utils.config_summary()
    for k, v in cfg.items():
        print(f"  {k:22} = {v}")
    print()

    host = config.get("SMTP_HOST", "")
    if not host:
        print("SONUÇ: SMTP_HOST BOŞ — uygulama e-posta göndermeyi hiç denemiyor.")
        print()
        print("Yapılacak:")
        print("  1) Sunucuda backend/.env dosyasında SMTP_HOST/USER/PASSWORD/FROM var mı bakın.")
        print("  2) Varsa, konteynerin bunu görüp görmediğini kontrol edin:")
        print("       docker compose exec app printenv | grep SMTP")
        print("     Boş çıkıyorsa: docker compose up -d  (env_file yeniden okunur)")
        return 1

    port = int(config.get("SMTP_PORT", "587") or "587")
    user = config.get("SMTP_USER", "")
    password = config.get("SMTP_PASSWORD", "").replace(" ", "")

    # 1) DNS
    print(f"[1/4] DNS çözümleme: {host} ... ", end="", flush=True)
    try:
        ip = socket.gethostbyname(host)
        print(f"OK ({ip})")
    except OSError as e:
        print(f"BAŞARISIZ — {e}")
        print("\nSONUÇ: Sunucunun DNS'i çalışmıyor veya internet erişimi yok.")
        return 1

    # 2) TCP
    print(f"[2/4] TCP bağlantısı: {host}:{port} ... ", end="", flush=True)
    try:
        with socket.create_connection((host, port), timeout=15):
            print("OK")
    except OSError as e:
        print(f"BAŞARISIZ — {e}")
        print()
        print("SONUÇ: Porta ulaşılamıyor. En sık sebep, kurumsal güvenlik")
        print("duvarının giden 587/465 SMTP trafiğini engellemesidir.")
        print("Deneyin: sunucudan  nc -vz smtp.gmail.com 587")
        print("Engelliyse ağ yöneticisinden izin isteyin ya da şirket içi bir")
        print("SMTP röle sunucusu kullanın (SMTP_HOST'u ona çevirin).")
        return 1

    # 3) STARTTLS / SSL
    print(f"[3/4] TLS el sıkışma ({'implicit SSL' if port == 465 else 'STARTTLS'}) ... ", end="", flush=True)
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=20)
        else:
            server = smtplib.SMTP(host, port, timeout=20)
            server.ehlo()
            server.starttls()
        server.ehlo()
        print("OK")
    except (OSError, smtplib.SMTPException, ssl.SSLError) as e:
        print(f"BAŞARISIZ — {type(e).__name__}: {e}")
        print("\nSONUÇ: TLS kurulamadı. Port 465 kullanıyorsanız STARTTLS değil")
        print("implicit SSL gerekir; 587 için STARTTLS doğrudur.")
        return 1

    # 4) Giriş
    if not user:
        print("[4/4] Giriş: SMTP_USER boş, atlanıyor.")
    else:
        print(f"[4/4] Giriş: {user} ... ", end="", flush=True)
        try:
            server.login(user, password)
            print("OK")
        except smtplib.SMTPAuthenticationError as e:
            print(f"BAŞARISIZ — {e.smtp_code} {e.smtp_error!r}")
            print()
            print("SONUÇ: Kullanıcı adı/şifre reddedildi.")
            print("Gmail için SMTP_PASSWORD normal hesap şifreniz OLAMAZ:")
            print("  · Google Hesabı > Güvenlik > 2 Adımlı Doğrulama'yı AÇIN")
            print("  · Sonra 'Uygulama Şifreleri'nden 16 haneli bir şifre üretin")
            print("  · Onu SMTP_PASSWORD'e yazın (boşluklu da olabilir)")
            print(f"  · Şu an girilen şifre {len(password)} karakter "
                  f"(uygulama şifresi boşluksuz 16 olur)")
            server.quit()
            return 1
        except smtplib.SMTPException as e:
            print(f"BAŞARISIZ — {e}")
            server.quit()
            return 1

    # 5) Gerçek gönderim
    if to_addr:
        print(f"[+] Deneme e-postası gönderiliyor → {to_addr} ... ", end="", flush=True)
        server.quit()
        ok = email_utils.send_email(
            to_addr,
            f"{email_utils.APP_NAME} — SMTP test",
            "Bu bir test e-postasıdır. Bunu görüyorsanız SMTP ayarları çalışıyor.",
        )
        print("GÖNDERİLDİ" if ok else "BAŞARISIZ (ayrıntı için app.log)")
        if ok:
            print("\nGelen kutusunda yoksa SPAM/Önemsiz klasörüne bakın.")
        return 0 if ok else 1

    server.quit()
    print()
    print("SONUÇ: Bağlantı ve kimlik doğrulama SORUNSUZ.")
    print("Mail yine de ulaşmıyorsa:")
    print("  · Alıcının SPAM klasörüne bakın (en sık sebep).")
    print("  · Gerçek gönderim testi:  python -m backend.smtp_test size@ornek.com")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
