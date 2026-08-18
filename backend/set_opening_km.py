"""TEK SEFERLİK — Açılış (baseline) km'sini toplu ayarlar.

Her araç için açılış km'si = ŞU ANKİ km − 10.000 (varsayılan) olarak yazılır.
Bu değerin ALTINDA/eşitinde kalan bakım hedefleri bir daha ASLA otomatik
açılmaz — sistemden önce yapılmış sayılırlar (bkz. services.
check_and_open_maintenances ve models.Vehicle.baseline_km). Geriye dönük
sahte "tamamlandı" kaydı ÜRETİLMEZ; o hedefler sadece hiç açılmaz.

NEDEN 10.000 km GERİYE?
    Açılış km'si aracın ŞU ANKİ km'sine eşit yazılırsa (uygulamanın
    açılıştaki varsayılan backfill davranışı budur — bkz. main.py
    _auto_maintenance_open_startup), aracın AZ ÖNCE geçtiği bakım hedefi de
    baseline'ın altında kalır ve bir daha hiç açılmaz. 10.000 km geriye
    alınca, tolerans penceresi içinde yeni geçilmiş bakımlar iş emri olarak
    açılmaya devam eder; yalnızca daha eski olanlar kapanır.

NE DEĞİŞTİRİR / NE DEĞİŞTİRMEZ
    ✓ vehicles.baseline_km sütunu (tüm araçlar)
    ✓ İsteğe bağlı olarak (--bakim-ac) yeni uygun hedefler için iş emri açar
    ✓ İsteğe bağlı olarak (--tum-is-emirlerini-sil) TÜM iş emirlerini siler
    ✗ Bu seçenek verilmezse mevcut iş emirlerine DOKUNMAZ
    ✗ İşlem kayıtlarına (audit_logs) hiçbir durumda dokunmaz
    ✗ Araçların current_km değerine dokunmaz

TAM SIFIRLAMA (--tum-is-emirlerini-sil)
    Bakım Geçmişi dahil TÜM iş emirlerini (açık + tamamlanmış) kalıcı olarak
    siler, sonra açılış km'sini yazar. Sıra önemlidir: önce silinir, sonra
    açılış km'si yazılır — böylece açılış km'sinin altında kalan eski hedefler
    bir daha ASLA açılmaz. Geri alınamaz; bu yüzden ayrıca --evet-eminim
    gerekir. (Aynı silme işlemi arayüzde de var: Bakımlar → Tehlikeli Bölge.)

TEKRAR ÇALIŞTIRMA
    Betik, uygulandığını app_settings tablosuna yazar ve ikinci kez
    çalıştırılırsa uyarıp durur (--force ile zorlanabilir). Değer her zaman
    "current_km − N" olarak hesaplandığı için, km ilerledikten SONRA tekrar
    çalıştırmak baseline'ı ileri taşır ve arada kalan bakımları gizler —
    bu yüzden koruma vardır.

KULLANIM
    Önizleme (hiçbir şey yazmaz):
        cd backend && python set_opening_km.py
    Uygula:
        cd backend && python set_opening_km.py --apply

    Docker ile çalışan kurulumda (servis adı: app):
        docker compose exec app python set_opening_km.py
        docker compose exec app python set_opening_km.py --apply

SEÇENEKLER
    --apply                  Gerçekten yaz (yoksa yalnızca önizleme)
    --geri N                 Kaç km geriye alınsın (varsayılan 10000)
    --bakim-ac               Yazdıktan sonra uygun hedefler için iş emri aç
    --tum-is-emirlerini-sil  TÜM iş emirlerini sil (tamamlananlar dahil)
    --evet-eminim            Yukarıdaki silme için zorunlu onay
    --force                  Daha önce uygulanmış olsa bile tekrar çalıştır
    --yedek-yok              Uygulamadan önce otomatik yedek ALMA (önerilmez)

TAM SIFIRLAMA ÖRNEĞİ
    docker compose exec app python set_opening_km.py \
        --apply --tum-is-emirlerini-sil --evet-eminim --bakim-ac
"""

import sys
from datetime import datetime
from pathlib import Path

from app import backup_utils, config, models, services
from app.database import IS_SQLITE, SessionLocal, engine

VARSAYILAN_GERI_KM = 10_000
AYAR_ANAHTARI = "opening_km_applied_at"


def _fmt(n) -> str:
    if n is None:
        return "—"
    return f"{int(n):,}".replace(",", ".")


def _arg_int(name: str, default: int) -> int:
    """--geri 5000 biçimindeki sayısal argümanı okur."""
    if name not in sys.argv:
        return default
    i = sys.argv.index(name)
    if i + 1 >= len(sys.argv):
        raise SystemExit(f"HATA: {name} için bir sayı verilmedi.")
    try:
        return int(sys.argv[i + 1])
    except ValueError:
        raise SystemExit(f"HATA: {name} için geçersiz sayı: {sys.argv[i + 1]}")


def main() -> None:
    apply = "--apply" in sys.argv
    force = "--force" in sys.argv
    bakim_ac = "--bakim-ac" in sys.argv
    yedek_al = "--yedek-yok" not in sys.argv
    hepsini_sil = "--tum-is-emirlerini-sil" in sys.argv
    eminim = "--evet-eminim" in sys.argv
    geri_km = _arg_int("--geri", VARSAYILAN_GERI_KM)

    if geri_km < 0:
        raise SystemExit("HATA: --geri negatif olamaz.")
    if hepsini_sil and apply and not eminim:
        raise SystemExit(
            "HATA: --tum-is-emirlerini-sil GERİ ALINAMAZ bir işlemdir.\n"
            "      Tamamlanmış bakım geçmişi dahil TÜM iş emirleri silinir.\n"
            "      Gerçekten istiyorsanız --evet-eminim de ekleyin."
        )

    db = SessionLocal()
    try:
        print("=" * 78)
        print("AÇILIŞ (BASELINE) KM TOPLU AYARLAMA")
        print(f"Kural : açılış km = mevcut km − {_fmt(geri_km)}")
        print("Mod   :", "UYGULA (--apply)" if apply else "ÖNİZLEME (hiçbir şey yazılmaz)")
        if hepsini_sil:
            print("Ek    : TAM SIFIRLAMA — tüm iş emirleri silinecek")
        print("=" * 78)

        onceki = services.get_setting(db, AYAR_ANAHTARI)
        if onceki and not force:
            print()
            print(f"UYARI: Bu betik daha önce çalıştırılmış ({onceki}).")
            print("Tekrar çalıştırmak açılış km'sini İLERİ taşır ve arada kalan")
            print("bakımları kalıcı olarak gizler.")
            print("Gerçekten tekrar çalıştırmak istiyorsanız --force ekleyin.")
            return

        vehicles = db.query(models.Vehicle).order_by(models.Vehicle.code).all()
        if not vehicles:
            print("\nSistemde araç yok — yapacak bir şey bulunamadı.")
            return

        # ---------------------------------------------------------- önizleme
        degisecek: list[tuple[models.Vehicle, int | None, int]] = []
        yukselenler: list[models.Vehicle] = []
        for v in vehicles:
            yeni = max(0, (v.current_km or 0) - geri_km)
            eski = v.baseline_km
            if eski == yeni:
                continue
            degisecek.append((v, eski, yeni))
            # Açılış km'sinin YÜKSELMESİ, o aralıktaki bakımların kalıcı
            # olarak gizlenmesi demektir — ayrıca uyarılır.
            if eski is not None and yeni > eski:
                yukselenler.append(v)

        print(f"\nToplam araç             : {len(vehicles)}")
        print(f"Açılış km'si değişecek  : {len(degisecek)}")
        print(f"Zaten doğru olan        : {len(vehicles) - len(degisecek)}")

        if degisecek:
            print("\n" + "-" * 78)
            print(f"{'Araç':<10}{'Mevcut km':>14}{'Eski açılış':>16}{'Yeni açılış':>16}")
            print("-" * 78)
            for v, eski, yeni in degisecek[:20]:
                print(f"{v.code:<10}{_fmt(v.current_km):>14}{_fmt(eski):>16}{_fmt(yeni):>16}")
            if len(degisecek) > 20:
                print(f"... ve {len(degisecek) - 20} araç daha")

        if yukselenler:
            print("\n" + "!" * 78)
            print(f"DİKKAT: {len(yukselenler)} araçta açılış km'si YÜKSELİYOR.")
            print("Bu araçlarda, eski ve yeni açılış km'si arasındaki bakım")
            print("hedefleri bundan sonra hiç açılmayacak:")
            for v in yukselenler[:10]:
                print(f"   • {v.code}: {_fmt(v.baseline_km)} → "
                      f"{_fmt(max(0, (v.current_km or 0) - geri_km))}")
            if len(yukselenler) > 10:
                print(f"   ... ve {len(yukselenler) - 10} araç daha")
            print("!" * 78)

        # Bilgi amaçlı: yeni açılış km'sinin altında kalan AÇIK iş emirleri.
        # Bunlara DOKUNULMAZ — kullanıcı isterse Bakımlar sayfasından
        # kendisi kapatır/siler.
        alt_acik = []
        for v, _eski, yeni in degisecek:
            for m in v.maintenances:
                if (
                    m.status != "completed"
                    and m.target_km is not None
                    and m.target_km > 0          # süre bakımlarında target_km negatiftir
                    and m.target_km <= yeni
                ):
                    alt_acik.append((v, m))
        if hepsini_sil:
            toplam_ie = db.query(models.Maintenance).count()
            tamamlanan = (
                db.query(models.Maintenance)
                .filter(models.Maintenance.status == "completed")
                .count()
            )
            print("\n" + "!" * 78)
            print("TAM SIFIRLAMA İSTENDİ — silinecek kayıtlar:")
            print(f"   • Toplam iş emri     : {toplam_ie}")
            print(f"     - tamamlanmış      : {tamamlanan}  (Bakım Geçmişi sayfası boşalır)")
            print(f"     - açık/devam eden  : {toplam_ie - tamamlanan}")
            print(f"   • Checklist maddesi  : {db.query(models.MaintenanceItem).count()}")
            print(f"   • Bakım notu         : {db.query(models.MaintenanceNote).count()}")
            print("   İşlem kayıtlarına (audit log) DOKUNULMAZ.")
            print("   Bu işlem GERİ ALINAMAZ.")
            print("!" * 78)

        if alt_acik and not hepsini_sil:
            print(f"\nBilgi: yeni açılış km'sinin ALTINDA kalan {len(alt_acik)} AÇIK iş emri var.")
            print("Bu betik bunlara DOKUNMAZ (silmez, tamamlandı işaretlemez).")
            for v, m in alt_acik[:10]:
                print(f"   • {v.code} — hedef {_fmt(m.target_km)} km, durum: {m.status}")
            if len(alt_acik) > 10:
                print(f"   ... ve {len(alt_acik) - 10} iş emri daha")

        if not apply:
            print("\n" + "-" * 78)
            print("Bu bir ÖNİZLEMEDİR — hiçbir değişiklik yapılmadı.")
            print("Gerçekten uygulamak için sonuna --apply ekleyin.")
            return

        if not degisecek and not hepsini_sil:
            print("\nDeğişecek araç yok — çıkılıyor.")
            return

        # ------------------------------------------------------------ yedek
        if yedek_al and IS_SQLITE:
            backup_dir = Path(
                config.get(
                    "BACKUP_DIR",
                    str(Path(__file__).resolve().parent / "backups"),
                )
            )
            try:
                path = backup_utils.run_backup_now(
                    db, engine, backup_dir, actor=None, note="açılış km ayarlamasından önce"
                )
                print(f"\nYedek alındı: {path.name if path else '(alınamadı)'}")
            except Exception as e:  # yedek alınamazsa da devam etme
                raise SystemExit(f"HATA: yedek alınamadı, işlem iptal edildi: {e}")
        elif yedek_al:
            print("\nNot: SQLite kullanılmıyor — otomatik yedek atlandı "
                  "(kendi yedeğinizi almış olun).")

        # -------------------------------------------------- tam sıfırlama
        # SIRA ÖNEMLİ: önce silinir, SONRA açılış km'si yazılır. Tersi olsaydı
        # silme ile baseline yazımı arasında açılan bir iş emri kalabilirdi.
        # Silme deseni DELETE /api/maintenances/all ile birebir aynıdır: alt
        # tablolar önce, ana tablo sonra (SQLite'ta bulk delete FK cascade'e
        # güvenmesin diye).
        if hepsini_sil:
            silinen = db.query(models.Maintenance).count()
            db.query(models.MaintenanceItem).delete(synchronize_session=False)
            db.query(models.MaintenanceNote).delete(synchronize_session=False)
            db.query(models.Maintenance).delete(synchronize_session=False)
            db.commit()
            # Toplu silme (synchronize_session=False) oturumun kimlik
            # haritasını (identity map) temizlemez. SQLite silinen birincil
            # anahtarları yeniden kullandığı için, hemen ardından açılan yeni
            # bir iş emri aynı id'yi alabilir ve SQLAlchemy "identity map
            # already had an identity ... replacing it" uyarısı verir.
            # Zararsızdır ama kafa karıştırır — yalnızca iş emriyle ilgili
            # nesneler haritadan düşürülerek önlenir (araç nesneleri oturumda
            # kalmalı, aşağıda baseline_km'leri yazılacak).
            for _obj in list(db.identity_map.values()):
                if isinstance(
                    _obj,
                    (models.Maintenance, models.MaintenanceItem, models.MaintenanceNote),
                ):
                    db.expunge(_obj)
            services.log_action(
                db, None, "delete_all", "maintenance", None,
                f"TÜM bakım iş emirleri silindi ({silinen} kayıt, tamamlananlar dahil)",
                detail="Açılış km ayarlama betiği üzerinden (set_opening_km.py)",
            )
            print(f"\nSilindi: {silinen} iş emri (checklist maddeleri ve notlarıyla birlikte).")

        # ------------------------------------------------------------ uygula
        for v, _eski, yeni in degisecek:
            v.baseline_km = yeni
        services.set_setting(db, AYAR_ANAHTARI, datetime.now().isoformat(timespec="seconds"))
        db.commit()

        services.log_action(
            db, None, "update", "vehicle", None,
            f"Açılış (baseline) km toplu ayarlandı: {len(degisecek)} araç",
            detail=(f"Kural: mevcut km − {_fmt(geri_km)}. "
                    f"Açılış km'si yükselen araç: {len(yukselenler)}. "
                    f"Altında kalan açık iş emri: {len(alt_acik)} (dokunulmadı)."),
        )

        print("\n" + "-" * 78)
        print(f"TAMAMLANDI — {len(degisecek)} aracın açılış km'si güncellendi.")

        # ------------------------------------------- isteğe bağlı: bakım aç
        if bakim_ac:
            toplam = 0
            for v in vehicles:
                try:
                    acilan = services.check_and_open_maintenances(db, v)
                    acilan += services.check_and_open_time_maintenances(db, v)
                    toplam += len(acilan)
                except Exception as e:
                    print(f"   ! {v.code} için bakım kontrolü başarısız: {e}")
            print(f"Yeni açılan iş emri: {toplam}")
        else:
            print("Not: yeni iş emirleri açılmadı. Uygun hedefler, bir sonraki km")
            print("     güncellemesinde veya sunucu yeniden başladığında açılır.")
            print("     Hemen açmak için: --bakim-ac ekleyin.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
