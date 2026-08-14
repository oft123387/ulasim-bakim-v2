"""Çekirdek iş kuralları: otomatik bakım açma, otomatik tamamlanma, kilit."""

import math
import random
import secrets
from contextvars import ContextVar
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from . import events, models

# main.py'deki _capture_request_context middleware'i her istekte bu iki
# değişkeni doldurur; log_action bunları otomatik okuyarak denetim kaydına
# IP/tarayıcı bilgisini ekler — her çağıran fonksiyonun `request` parametresi
# alıp elle geçirmesine gerek kalmaz.
REQUEST_IP: ContextVar[str] = ContextVar("REQUEST_IP", default="")
REQUEST_UA: ContextVar[str] = ContextVar("REQUEST_UA", default="")


# ------------------------------------------------------ açık bakım tekilleştirme
def dedupe_open_by_vehicle(open_maintenances: list["models.Maintenance"]) -> list["models.Maintenance"]:
    """Aynı araç için, AYNI TÜRDEN (km ya da süre) birden fazla açık
    (Bekliyor/Devam Ediyor) iş emri varsa yalnızca hedefi en büyük olanı
    döner — küçük periyotlar (ör. 25.000/50.000 km ya da 3 ay/1 yıl) zaten
    o büyük bakımın kombine checklist'ine dahildir, ayrı sayılmaz/gösterilmez.
    KM bazlı ve SÜRE bazlı bakımlar birbirinden tamamen bağımsız iş akışları
    olduğu için (biri diğerinin checklist'ini kapsamaz) asla birbirini
    gizlemez — anahtar (vehicle_id, plan.plan_type) ikilisidir. Yalnızca
    TAMAMLANMAMIŞ kayıtlar verilmeli (tamamlananlar geçmiş kayıt olduğu için
    bu tekilleştirmeye tabi değildir — ayrıca sayılır).

    Dashboard'daki "Bekleyen Bakım" sayısı ile Bakımlar sayfasındaki
    listenin (aynı mantığı istemci tarafında uygulayan) tutarlı olması
    için bu tek yerde tutulur."""
    best_by_key: dict[tuple[int, str], models.Maintenance] = {}
    for m in open_maintenances:
        plan_type = getattr(m.plan, "plan_type", "km") if m.plan else "km"
        key = (m.vehicle_id, plan_type)
        cur = best_by_key.get(key)
        # target_km süre bakımlarında negatif sentinel'dir (-hedef ay sayısı);
        # abs() alınca km'de olduğu gibi "büyük periyot = daha yönetici/daha
        # ileri" karşılaştırması her iki türde de doğru sonucu verir.
        if cur is None or abs(m.target_km) > abs(cur.target_km):
            best_by_key[key] = m
    return list(best_by_key.values())


# ---------------------------------------------------------------- audit log
def log_action(
    db: Session,
    actor: "models.User | None",
    action: str,
    entity_type: str,
    entity_id: int | None = None,
    summary: str = "",
    detail: str = "",
    commit: bool = True,
    old_value: str | None = None,
    new_value: str | None = None,
) -> models.AuditLog:
    """Faz 17 — Değiştirilemez işlem kaydı ekler (append-only).

    `commit=False` verilirse kaydı çağıran işlemle aynı transaction'da
    tutar (çağıran commit eder); varsayılan olarak hemen kaydeder.

    `old_value`/`new_value`: bir alanın değişmeden önceki/sonraki değeri —
    yalnızca çağıran taraf bunu anlamlı bulduğunda doldurulur (ör. rol
    değişikliği, izin değişikliği, km güncellemesi). Boş bırakılırsa
    `detail` serbest metin olarak aynı bilgiyi taşımaya devam edebilir.
    `ip_address`/`user_agent` her zaman otomatik doldurulur — main.py'deki
    middleware tarafından istek başında ContextVar'a yazılır.
    """
    entry = models.AuditLog(
        actor_id=actor.id if actor else None,
        actor_name=actor.full_name if actor else "Sistem",
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary[:300],
        detail=detail,
        old_value=(old_value or "")[:1000],
        new_value=(new_value or "")[:1000],
        ip_address=REQUEST_IP.get()[:64],
        user_agent=REQUEST_UA.get()[:300],
    )
    db.add(entry)
    if commit:
        db.commit()
    else:
        db.flush()
    # Bağlı tüm istemcilere (bkz. events.py, /api/events/stream) "bir şey
    # değişti" sinyali gönderir — log_action neredeyse her mutasyon
    # endpoint'inden çağrıldığı için (main.py) bu, ayrı ayrı her endpoint'e
    # yayın kodu eklemek yerine tek, merkezi bir kanca (hook) noktasıdır.
    events.broadcast(entity_type, action)
    return entry

# Dashboard/Araçlar listesinde "yaklaşan bakım" olarak gösterilecek sabit
# önizleme penceresi. Planın kendi (çok daha geniş olabilen) otomatik-açma
# toleransından (lower_pct/upper_pct) bağımsızdır — yalnızca "gerçekten
# yaklaşmış" olanları göstermek için kullanılır.
PREVIEW_LOWER_PCT = 5.0
PREVIEW_UPPER_PCT = 10.0

# Kullanıcının Sirio/Bozankaya km-tolerans tablosundan onaylanan kural:
# yüzdelik tolerans (lower_pct=%5 / upper_pct=%10), periyot büyüdükçe
# sınırsız büyümez — HER İKİ yönde de en fazla 3.000 km'de tavanlanır. Tablo
# örneği: 1M (10.000 km) alt=-500 (%5) üst=1.000 (%10) — tavana değmiyor;
# 6M (60.000 km) alt=-3.000 (%5 tam tavanda) üst=3.000 (%10=6.000 olurdu,
# tavanlanır); 1Y ve üzeri periyotların TÜMÜNDE alt=üst=3.000 sabit kalır
# (çünkü %5/%10'u zaten 3.000'i çoktan aşar). Bozankaya tablosunda da
# birebir aynı desen (25.000: alt=-1250/üst=2500 tavana değmiyor; 50.000:
# alt=-2500 tavana değmiyor, üst=3000 tavanlanır; 100.000+ hepsi ±3.000).
MAX_TOLERANCE_KM = 3000.0

# Günlük ortalama km varsayımı (takvim projeksiyonu ve "kalan gün" tahmini
# aynı sabiti kullanır, tutarlı kalsın diye tek yerde tanımlanır).
DEFAULT_CALENDAR_DAILY_KM = 300.0

# Bir bakıma tahminen bu kadar gün veya daha az kaldıysa "acil / kritik"
# olarak işaretlenir (dashboard ve Araçlar sayfasında daha belirgin gösterilir).
URGENT_WITHIN_DAYS = 14

# Bunun daha da altında (kalan km VEYA tahmini gün bazında) ise "çok kritik"
# — acil kümesinin en üst/en belirgin alt kümesi. Dashboard ve Araçlar
# sayfasında ayrıca vurgulanır (ör. kırmızı yerine daha koyu/parlayan ton).
# Süre bazlı (aylık/yıllık) planlarda hâlâ kullanılır.
CRITICAL_WITHIN_DAYS = 7
CRITICAL_WITHIN_KM = 1000

# KM bazlı planlarda (1M, 3M, 6M, ...) "kritik"/"çok kritik" eşikleri artık
# SABİT km değil, planın kendi periyoduna (interval_km) ORANLI yüzdelerdir.
# Kullanıcının onayladığı somut örnek: 1M (10.000 km) periyodunda kalan km
# 1.500'ün altına düşünce "kritik", 500'ün altına düşünce "çok kritik" olur
# (1500/10000 = %15, 500/10000 = %5). "6 yı yap" yanıtıyla bu oranın TÜM
# periyotlara (ör. 6M = 6 x 1M için de aynı yüzdelerle) genellenmesi
# onaylandı — yani eşik mutlak km değil, periyodun yüzdesi olarak sabit kalır.
KRITIK_PCT_OF_INTERVAL = 15.0
COK_KRITIK_PCT_OF_INTERVAL = 5.0

# ---------------------------------------------------------------------------
# Araçlar sayfasındaki "sonraki ilk bakım" durumu için 4 sabit kategori
# (kullanıcının onayladığı somut tanım):
#   Yaklaşıyor     : hedefe kalan km <= 5.000 (henüz "bakım takip periyodu"
#                    penceresine girmemiş, ama uzaktan görülüyor).
#   Planlanacak    : "bakım takip periyodu" penceresi içinde VE henüz bir iş
#                    emri açılıp başlatılmamış.
#   Planlandı      : aynı pencere içinde VE zaten bir iş emri açılıp
#                    başlatılmış (is_activated=True).
#   Bakım gecikti  : araç bu pencerenin de ötesine geçmiş (hedefi aşmış).
#
# "Bakım takip periyodu" = planın KENDİ (periyoda oranlı, MAX_TOLERANCE_KM'de
# tavanlanan) önizleme penceresi (bkz. PREVIEW_LOWER_PCT/PREVIEW_UPPER_PCT
# yukarıda) — kullanıcının verdiği "(-3000, +3000)" örneği, bu pencerenin
# büyük periyotlarda ulaştığı tavan (cap) değeridir; küçük periyotlarda
# (ör. 1M/3M) pencere zaten daha dardır, bu doğal olarak korunur.
#
# Süre bazlı (aylık/yıllık) planlarda karşılık gelen birim km değil gündür;
# burada YENİ bir sabit icat etmek yerine önceden var olan URGENT_WITHIN_DAYS
# (14 gün, "Yaklaşıyor" dış sınırı) ve CRITICAL_WITHIN_DAYS (7 gün, takip
# penceresinin yarı genişliği) sabitleri aynı anlamla yeniden kullanılır.
APPROACHING_WITHIN_KM = 5000.0


def _classify_upcoming_status(
    remaining: float, lower_offset: float, upper_offset: float,
    approaching_offset: float, is_started: bool,
) -> str | None:
    """Yukarıdaki 4 kategoriyi tek bir yerde hesaplayan ortak yardımcı —
    hem km hem süre bazlı `compute_upcoming_*` fonksiyonlarınca kullanılır.

    `remaining`: hedefe kalan (km ya da gün, İMZALI — negatifse hedef
    aşılmış demektir). `lower_offset`/`upper_offset`: "bakım takip
    periyodu" penceresinin hedefin ÖNCESİ/SONRASI yarı genişlikleri.
    `approaching_offset`: pencerenin dışında ama hâlâ "Yaklaşıyor"
    sayılacağı üst sınır (km: APPROACHING_WITHIN_KM, süre: URGENT_WITHIN_DAYS).
    `is_started`: gerçekten açılıp başlatılmış (is_activated=True) bir iş
    emri var mı — YALNIZCA pencere içindeyken (lower_offset) Planlandı/
    Planlanacak ayrımını belirler. Kullanıcının onayladığı kesin tanım:
      Yaklaşıyor    : hedefe kalan <= approaching_offset (ör. 5.000 km),
                      pencerenin (lower_offset) DIŞINDA — başlatılmış olsun
                      ya da olmasın, pencere dışındaysa etiket hep budur.
      Planlanacak   : pencere içinde VE henüz başlatılmamış.
      Planlandı     : pencere içinde VE başlatılmış.
      Bakım gecikti : pencerenin (upper_offset) ötesine geçilmiş.
    Bu ayrım yalnızca ETİKETLEME içindir — bir iş emrinin Bakımlar
    sayfasında "Başlatılanlar" ya da "Başlatılmayı Bekleyenler" sekmesinde
    görünmesi bu status alanına göre DEĞİL, doğrudan maintenance_id'nin
    (yani gerçekten başlatılmış olup olmadığının) dolu olup olmamasına göre
    belirlenir (bkz. MaintenancesPage.tsx flowTabItems) — böylece pencere
    dışında ama başlatılmış bir iş emri "Yaklaşıyor" etiketiyle birlikte
    yine de Başlatılanlar'da görünür.

    Döner: "gecikti" | "planlandi" | "planlanacak" | "yaklasiyor" | None
    (None: henüz hiçbir uyarı gösterilecek kadar yakın değil)."""
    if remaining < -upper_offset:
        return "gecikti"
    if remaining <= lower_offset:
        return "planlandi" if is_started else "planlanacak"
    if remaining <= approaching_offset:
        return "yaklasiyor"
    return None


def plan_window(plan: "models.MaintenancePlan", target: int) -> tuple[float, float]:
    """Bir planın belirli bir hedef km için açılma (tolerans) penceresini
    (alt, üst) döner. Plan.threshold_type'a göre ya yüzdelik (periyoda
    bağlı) ya da sabit km (periyottan bağımsız) hesaplanır — bkz.
    MaintenancePlan model docstring'i.

    Yüzdelik (pct) tolerans, periyot büyüdükçe SINIRSIZ büyümez — kullanıcının
    onayladığı km-tolerans tablosuna göre her iki yönde de en fazla
    MAX_TOLERANCE_KM'de tavanlanır (bkz. yukarıdaki sabitin açıklaması)."""
    if plan.threshold_type == "km":
        return target - plan.lower_km, target + plan.upper_km
    lower = min(plan.interval_km * plan.lower_pct / 100, MAX_TOLERANCE_KM)
    upper = min(plan.interval_km * plan.upper_pct / 100, MAX_TOLERANCE_KM)
    return target - lower, target + upper


# ---------------------------------------------------------------- bakım açma
def check_and_open_maintenances(
    db: Session, vehicle: models.Vehicle, skip_passed_targets: bool = False
) -> list[models.Maintenance]:
    """Araç kilometresi bir bakım aralığına girdiyse bakımı bir kez açar.

    `skip_passed_targets=True` verilirse, aracın ŞU ANKİ km'sinin zaten
    geçmiş olduğu (target_km <= vehicle.current_km) hedefler için iş emri
    AÇILMAZ. Bu, sisteme sonradan yüksek km ile kaydedilen bir araç için
    kullanılır (ör. araç 250.000 km'de sisteme girildiğinde 250.000 km'lik
    bakım kendiliğinden açılmamalı — o bakım muhtemelen sisteme girmeden
    önce gerçek hayatta zaten yapılmıştır). Aracın henüz ulaşmadığı bir
    sonraki hedef (ör. 275.000 km) toleransa girdiğinde ise normal şekilde
    açılır.

    Kademeli/kümülatif mantık: bir hedef km birden fazla planın periyoduna
    tam bölünüyorsa (ör. 150.000 km hem 25.000 hem 50.000'e bölünür), TEK
    bir iş emri açılır. Bu iş emrini "yöneten" plan, o hedefe bölünen
    planlar arasında periyodu en büyük olanıdır (ör. 150.000'de 50.000
    km bakımı; 300.000'de 300.000 km bakımı — çünkü 25.000/50.000/100.000/
    300.000'in hepsine bölünür). İş emrinin checklist'i, uygulanan tüm
    planların ağaçlarının birleşimidir (her plan kendi grubu altında).

    Aday hedef km'ler yalnızca araç şu anki kilometresine EN YAKIN
    kontrol noktalarından (tüm planların periyotlarının en büyük ortak
    böleni = "temel birim" bazında) hesaplanır. Örneğin periyotlar
    25.000/50.000/100.000/200.000/300.000/500.000 ise temel birim
    25.000'dir; 500.000'i biraz geçmiş bir araç için tek aday
    500.000 (geçilen en son nokta) ve 525.000 (bir sonraki) olur —
    600.000 gibi uzak bir nokta asla aday olmaz. Bu, önceden aday
    havuzunun her planın KENDİ periyoduna göre ayrı ayrı üretilmesinden
    kaynaklanan hatayı giderir: o eski yöntemde 500.000'i biraz geçen
    bir araç için 600.000 (300.000 planının bir sonraki katı) da aday
    oluyor ve 300.000 km'lik geniş tolerans penceresiyle erken/yanlış
    şekilde açılabiliyordu.
    """
    opened: list[models.Maintenance] = []
    km = vehicle.current_km
    plans = db.query(models.MaintenancePlan).filter_by(is_active=True).all()
    # Yalnızca "genel" (vehicle_model="") planlar veya bu aracın modeliyle
    # birebir eşleşen planlar uygulanır — farklı araç modelleri için ayrı
    # yüklenmiş bakım ağaçları birbirine karışmaz. Katalog-amaçlı planlar
    # (is_catalog_only — bir temel periyodun katı, ör. 800.000 km) kendi
    # başına bakım TETİKLEMEZ; onu bölen temel plan (ör. 200.000 km) zaten
    # o katta da tetiklenir (bkz. excel_utils.canonical_base_interval).
    active_plans = [
        p for p in plans
        if p.interval_km > 0
        and not p.is_catalog_only
        and _model_matches(p.vehicle_model, vehicle.model)
    ]
    if not active_plans:
        return opened

    # Temel birim = tüm periyotların en büyük ortak böleni (GCD).
    base_unit = active_plans[0].interval_km
    for plan in active_plans[1:]:
        base_unit = math.gcd(base_unit, plan.interval_km)
    if base_unit <= 0:
        return opened

    # Yalnızca aracın şu anki konumuna en yakın iki kontrol noktası aday olur:
    # geçilmiş olan en son nokta ve bir sonraki nokta.
    candidates: set[int] = set()
    candidates.add((km // base_unit) * base_unit)
    candidates.add(((km // base_unit) + 1) * base_unit)
    candidates.discard(0)

    # Başlangıç (baseline) km'sinin ALTINDA/eşitinde kalan hedefler asla açılmaz
    # — araç sisteme girmeden ÖNCE yapılmış sayılan bakımlar (ör. 200.000 km
    # bakımı) hiçbir zaman "eksik/geciken" olarak görünmez; yalnızca baseline'ın
    # ÜSTÜNE gelen yeni bakımlar açılır (kullanıcı isteği). baseline None ise
    # (henüz backfill edilmemiş) bu kural uygulanmaz.
    baseline = vehicle.baseline_km
    for target in sorted(candidates):
        if skip_passed_targets and target <= km:
            continue
        if baseline is not None and target <= baseline:
            continue
        applicable = [p for p in active_plans if target % p.interval_km == 0]
        if not applicable:
            continue
        governing = max(applicable, key=lambda p: p.interval_km)

        lower, upper = plan_window(governing, target)
        if not (lower <= km <= upper):
            continue

        exists = (
            db.query(models.Maintenance)
            .filter_by(vehicle_id=vehicle.id, plan_id=governing.id, target_km=target)
            .first()
        )
        if exists:
            continue  # Aynı bakım ikinci kez açılamaz

        m = models.Maintenance(
            vehicle_id=vehicle.id,
            plan_id=governing.id,
            target_km=target,
            km_at_open=km,
            status="pending",
            # DÜZELTME: bu satır önceden ("Item 5 — 'Başlat yok' tam otomatik
            # model" — Faz 26'dan ÖNCEKİ, artık geçersiz bir tasarımın kalıntısı)
            # is_activated=True idi. Bu, models.Maintenance.is_activated alanının
            # kendi belgelenmiş sözleşmesiyle (bkz. models.py: "Faz 26 — Bakım
            # başlatma onayı: otomatik-açma (check_and_open_maintenances) bir iş
            # emrini artık DOĞRUDAN görünür/çalışılabilir yapmaz; yalnızca bir
            # 'aday' olarak oluşturur (is_activated=False)") DOĞRUDAN çelişiyordu
            # — sonuç olarak "Başlatılmayı Bekleyenler" listesi otomatik açılan
            # adaylar için HİÇBİR ZAMAN dolmuyordu (adaylar zaten doğrudan aktif
            # doğuyordu) ve zamanlanmış başlatma (scheduled_start_at) mekanizması
            # otomatik tespit için işlevsiz kalıyordu. False olması belgelenen
            # sözleşmeyi ve testleri (test_scheduled_maintenance_start.py) karşılar.
            is_activated=False,
        )
        db.add(m)
        db.flush()
        _copy_tree_snapshot(db, m, applicable)
        opened.append(m)

    if opened:
        db.commit()
    return opened


# Bulk katalog/excel_utils import edemediği için burada küçük bir kopyası
# tutulan Türkçe karakter normalizasyonu (bkz. excel_utils._TR_MAP/_norm) —
# yalnızca "Sirio" araç modelini büyük/küçük harf ve Türkçe karakterden
# (ör. "SİRİO") bağımsız tanımak için kullanılır.
_TR_MAP = str.maketrans("çÇğĞıİöÖşŞüÜ", "cCgGiIoOsSuU")


def _is_sirio_model(vehicle_model: str | None) -> bool:
    if not vehicle_model:
        return False
    return "sirio" in vehicle_model.translate(_TR_MAP).lower()


def _norm_model(s: str | None) -> str:
    """Bir araç modeli adını karşılaştırmaya uygun, normalize edilmiş hale
    getirir: baştaki/sondaki boşluklar kırpılır, Türkçe karakterler
    (İ/I/ı/i, ş/Ş, ç/Ç, ...) ASCII karşılıklarına çevrilir ve küçük harfe
    indirilir (bkz. _TR_MAP/_is_sirio_model — aynı normalizasyon)."""
    return (s or "").strip().translate(_TR_MAP).lower()


def _model_matches(plan_vehicle_model: str | None, vehicle_model: str | None) -> bool:
    """Bir planın `vehicle_model`'inin, verilen aracın modeliyle uygulanabilir
    olup olmadığını belirler. Boş/None `plan_vehicle_model` HER ZAMAN
    uygulanabilirdir ("Genel / Tüm Araçlar" planı). Aksi halde karşılaştırma
    büyük/küçük harf, baş/son boşluk ve Türkçe karakter farklarından
    BAĞIMSIZ yapılır — aksi halde ör. "Sirio" / "SİRİO" / "Sirio " gibi salt
    yazım farkı olan bir plan, aracın modeliyle görsel olarak aynı görünse
    bile birebir (==) karşılaştırmada sessizce eşleşmeyip TÜM kademeli/
    otomatik-açma/yaklaşan-bakım hesaplarından dışlanabiliyordu (kullanıcı
    geri bildirimi: bir "3M" planı, aynı modeldeki 1M/6M planlarıyla birlikte
    birleşik bakıma dahil olması gerekirken sessizce dışlanıyordu)."""
    if not plan_vehicle_model:
        return True
    return _norm_model(plan_vehicle_model) == _norm_model(vehicle_model)


# Sirio km-tolerans tablosundaki periyot kodlarıyla BİREBİR aynı birim
# (bkz. excel_utils._KM_CODE_UNIT): M = 10.000 km, Y = 12 x 10.000 = 120.000 km.
_SIRIO_KM_UNIT = {"M": 10_000, "Y": 120_000}


def sirio_period_label(interval_km: int) -> str | None:
    """Sirio'nun km periyodunu, kullanıcının onayladığı km-tolerans
    tablosundaki gibi kısa "M"/"Y" koduna çevirir (ör. 1.440.000 -> "12Y",
    30.000 -> "3M"). Periyot bu birimlerden birine TAM bölünmüyorsa (Sirio
    tablosunda görünmeyen bir değer) None döner — çağıran taraf o zaman
    normal "X km Bakımı" biçimine düşer. Y önce denenir (daha büyük birim);
    ör. 120.000 hem 12M hem 1Y olurdu, tabloya göre "1Y" tercih edilir."""
    if interval_km <= 0:
        return None
    if interval_km % _SIRIO_KM_UNIT["Y"] == 0:
        return f"{interval_km // _SIRIO_KM_UNIT['Y']}Y"
    if interval_km % _SIRIO_KM_UNIT["M"] == 0:
        return f"{interval_km // _SIRIO_KM_UNIT['M']}M"
    return None


# ---------------------------------------------------------------- süre bazlı (aylık/yıllık) bakım açma
def default_plan_name(
    plan_type: str, interval_km: int, interval_months: int | None, vehicle_model: str | None = None,
) -> str:
    """Admin plan adını boş bırakırsa türe göre otomatik, tutarlı bir ad
    üretir — km planlarında mevcut "25.000 km Bakımı" kuralıyla aynı biçim,
    süre planlarında "3 Aylık Bakım" / "1 Yıllık Bakım" gibi. plan_type=
    "unknown" (toplu katalogda periyodu yorumlanamayan, ör. "?" satırları
    için kullanılan geçici tür — bkz. excel_utils.import_bulk_catalog) hem
    km hem süre motorundan hariç tutulan, "belirsiz" madde grubudur.

    Sirio modeli İSTİSNA: kullanıcının açık talimatı üzerine ("Sirio'nun
    TÜM bakımları Y veya M olarak gösterilecek") km planlarında "X km
    Bakımı" yerine kısa periyot kodu (ör. "12Y", "3M") kullanılır — bkz.
    sirio_period_label. Bozankaya ve diğer modeller etkilenmez (onların
    periyotları M/Y birimine tam bölünmediği için zaten normal km biçiminde
    kalır)."""
    if plan_type == "unknown":
        return "Periyodu Belirsiz (?)"
    if plan_type == "time" and interval_months:
        if interval_months % 12 == 0:
            years = interval_months // 12
            return f"{years} Yıllık Bakım"
        return f"{interval_months} Aylık Bakım"
    if plan_type == "km" and _is_sirio_model(vehicle_model):
        label = sirio_period_label(interval_km)
        if label:
            return label
    return f"{fmt_km_static(interval_km)} km Bakımı"


def normalize_sirio_plan_names(db: Session) -> list[dict]:
    """default_plan_name'e Sirio kısa kod (M/Y) desteği eklendiğinde, o
    tarihten ÖNCE zaten "X km Bakımı" biçiminde oluşturulmuş Sirio planları
    otomatik olarak yeniden adlandırılmaz (default_plan_name yalnızca YENİ
    plan oluşturulurken çağrılır). Bu fonksiyon var olan TÜM aktif Sirio km
    planlarının adını, periyodu M/Y'ye tam bölünüyorsa kısa koda çevirir —
    kullanıcının "Sirio'nun TÜM bakımları Y veya M olarak gösterilecek"
    talimatını geriye dönük uygulamak için (bkz. POST /api/plans/normalize-sirio-names).

    Yalnızca adı hâlâ otomatik üretilmiş görünen (değiştirilecek yeni adla
    aynı olmayan) planları değiştirir; admin'in bilinçli olarak farklı,
    özel bir ad verdiği planlara dokunmaz İSTİSNASI YOKTUR — kullanıcının
    talimatı "TÜMÜ" olduğundan koşulsuz uygulanır, yalnızca ad zaten doğru
    kısa koddaysa (değişiklik yok) atlanır."""
    plans = (
        db.query(models.MaintenancePlan)
        .filter_by(plan_type="km", is_active=True)
        .all()
    )
    renamed: list[dict] = []
    for p in plans:
        if not _is_sirio_model(p.vehicle_model):
            continue
        label = sirio_period_label(p.interval_km)
        if not label or p.name == label:
            continue
        renamed.append({"plan_id": p.id, "old_name": p.name, "new_name": label})
        p.name = label
    if renamed:
        db.commit()
    return renamed


def fmt_km_static(n: int) -> str:
    """main.fmt_km ile aynı biçim (services.py main.py'yi import edemediği
    için burada küçük bir kopyası tutulur — bkz. main.fmt_km)."""
    return f"{n:,}".replace(",", ".")


def add_months(d: date, months: int) -> date:
    """`d` tarihine `months` ay ekler (takvim-güvenli — ör. 31 Ocak + 1 ay
    = 28/29 Şubat, taşma olmaz)."""
    import calendar

    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def elapsed_months(start: date, today: date) -> int:
    """`start` tarihinden `today`'e kadar TAM geçen ay sayısı (ör. 15 Ocak'tan
    10 Mart'a: 1 tam ay geçmiş — 14 Mart'a kadar 2. ay tamamlanmaz)."""
    months = (today.year - start.year) * 12 + (today.month - start.month)
    if today.day < start.day:
        months -= 1
    return max(0, months)


def time_plan_window(plan: "models.MaintenancePlan", target_months: int) -> tuple[float, float]:
    """Süre planı için (km planındaki plan_window ile aynı mantık, ay
    biriminde): lower_pct/upper_pct, interval_months'un yüzdesi olarak
    yorumlanır. threshold_type="km" süre planlarında desteklenmez (basit
    tutulur — admin isterse lower_pct/upper_pct'i 0'a çok yakın ayarlayarak
    sıkı bir pencere tanımlayabilir)."""
    return (
        target_months - plan.interval_months * plan.lower_pct / 100,
        target_months + plan.interval_months * plan.upper_pct / 100,
    )


def check_and_open_time_maintenances(
    db: Session, vehicle: "models.Vehicle", skip_passed_targets: bool = False
) -> list[models.Maintenance]:
    """Süre bazlı (aylık/yıllık) bakımları otomatik açar — km motoruyla
    (check_and_open_maintenances) BİREBİR AYNI algoritma, tek farkla:
    "aracın şu anki km'si" yerine "aracın hizmete başlama tarihinden bugüne
    kadar geçen tam ay sayısı" kullanılır. Kademeli/kümülatif birleştirme
    (ör. 3 aylık ve 1 yıllık bakım aynı ay denk gelirse tek iş emri) km
    motorundaki mantıkla aynı şekilde çalışır.

    target_km sütununda dahili benzersizlik için NEGATİF bir sentinel
    (-target_months) tutulur — bkz. models.Maintenance yorumu; gerçek
    tarih için her zaman target_date kullanılır."""
    opened: list[models.Maintenance] = []
    if not vehicle.commissioning_date:
        return opened
    today = date.today()
    elapsed = elapsed_months(vehicle.commissioning_date, today)

    plans = db.query(models.MaintenancePlan).filter_by(is_active=True, plan_type="time").all()
    active_plans = [
        p for p in plans
        if p.interval_months and p.interval_months > 0
        and not p.is_catalog_only
        and _model_matches(p.vehicle_model, vehicle.model)
    ]
    if not active_plans:
        return opened

    base_unit = active_plans[0].interval_months
    for plan in active_plans[1:]:
        base_unit = math.gcd(base_unit, plan.interval_months)
    if base_unit <= 0:
        return opened

    candidates: set[int] = set()
    candidates.add((elapsed // base_unit) * base_unit)
    candidates.add(((elapsed // base_unit) + 1) * base_unit)
    candidates.discard(0)

    for target in sorted(candidates):
        if skip_passed_targets and target <= elapsed:
            continue
        applicable = [p for p in active_plans if target % p.interval_months == 0]
        if not applicable:
            continue
        governing = max(applicable, key=lambda p: p.interval_months)

        lower, upper = time_plan_window(governing, target)
        if not (lower <= elapsed <= upper):
            continue

        sentinel_km = -target
        exists = (
            db.query(models.Maintenance)
            .filter_by(vehicle_id=vehicle.id, plan_id=governing.id, target_km=sentinel_km)
            .first()
        )
        if exists:
            continue

        target_date = add_months(vehicle.commissioning_date, target)
        m = models.Maintenance(
            vehicle_id=vehicle.id,
            plan_id=governing.id,
            target_km=sentinel_km,
            target_date=target_date,
            km_at_open=vehicle.current_km,
            status="pending",
            # DÜZELTME: km-bazlı check_and_open_maintenances ile AYNI hata
            # (bkz. yukarıdaki açıklama) — süre bazlı otomatik-açma da Faz 26
            # sözleşmesine uyması için is_activated=False olmalı.
            is_activated=False,
        )
        db.add(m)
        db.flush()
        _copy_tree_snapshot(db, m, applicable)
        opened.append(m)

    if opened:
        db.commit()
    return opened


def check_and_open_time_maintenances_all_vehicles(db: Session) -> list[models.Maintenance]:
    """Zamanlanmış arka plan görevinin çağırdığı toplu kontrol — bkz.
    main.py `_time_maintenance_background_loop` (km-sim/backup ile aynı
    desen). Hizmete başlama tarihi tanımlı her araç için çalışır."""
    opened: list[models.Maintenance] = []
    vehicles = (
        db.query(models.Vehicle)
        .filter(models.Vehicle.commissioning_date.isnot(None))
        .all()
    )
    for v in vehicles:
        opened.extend(check_and_open_time_maintenances(db, v))
    return opened


def create_manual_time_maintenance(
    db: Session,
    vehicle: "models.Vehicle",
    plan: "models.MaintenancePlan",
    target_date: date,
    actor: "models.User",
) -> models.Maintenance:
    """create_manual_maintenance'ın süre planları için karşılığı."""
    if plan.plan_type != "time":
        raise ValueError("Bu plan süre bazlı değil — target_date yerine target_km kullanın")
    if not plan.is_active:
        raise ValueError(f"'{plan.name}' planı pasif — pasif bir plan için bakım iş emri açılamaz")
    if plan.vehicle_model and not _model_matches(plan.vehicle_model, vehicle.model):
        raise ValueError(
            f"'{plan.name}' planı yalnızca '{plan.vehicle_model}' modeli için tanımlı — "
            f"{vehicle.code} bu modelde değil ({vehicle.model or 'model belirtilmemiş'})"
        )
    if not vehicle.commissioning_date:
        raise ValueError(f"{vehicle.code} aracının hizmete başlama tarihi tanımlı değil")
    target_months = elapsed_months(vehicle.commissioning_date, target_date)
    sentinel_km = -target_months
    exists = (
        db.query(models.Maintenance)
        .filter_by(vehicle_id=vehicle.id, plan_id=plan.id, target_km=sentinel_km)
        .first()
    )
    if exists:
        raise ValueError("Bu araç için bu periyotta ve hedef tarihte zaten bir iş emri var")

    m = models.Maintenance(
        vehicle_id=vehicle.id,
        plan_id=plan.id,
        target_km=sentinel_km,
        target_date=target_date,
        km_at_open=vehicle.current_km,
        status="pending",
        is_activated=True,
        activated_at=datetime.now(),
        activated_by_id=actor.id,
    )
    db.add(m)
    db.flush()
    _copy_tree_snapshot(db, m, [plan])
    db.commit()
    return m


def activate_maintenance(db: Session, m: "models.Maintenance", actor: "models.User | None" = None) -> None:
    """Faz 26: Admin/Mühendis'in Bakımlar sayfasındaki "Başlatılmayı
    Bekleyenler" bölümünden bir aday iş emrini onaylayıp devreye almasıdır.
    Bundan sonra iş emri Bakımlar listesinde, Takvim'de, Dashboard'da ve
    Alarm Merkezi'nde normal şekilde görünür ve teknisyen çalışabilir.

    actor=None: zamanlanmış otomatik başlatma (bkz. run_scheduled_maintenance_
    starts) gibi bir kullanıcı eylemi olmayan durumlar için — activated_by
    boş kalır, yalnızca activated_at doldurulur."""
    m.is_activated = True
    m.activated_at = datetime.now()
    m.activated_by_id = actor.id if actor else None
    # Artık gerçekten başladığına göre bekleyen bir zamanlama varsa anlamını
    # yitirir — temizlenir (aksi halde pending-start listesinden çıkmış bir
    # iş emri için hâlâ "zamanlandı" görünürdü, hiçbir yerde gösterilmese de).
    m.scheduled_start_at = None
    db.commit()


def schedule_maintenance_start(db: Session, m: "models.Maintenance", scheduled_date: "date") -> None:
    """Admin/Mühendis "Başlat" derken bugün yerine ileri bir tarih seçtiğinde
    çağrılır — iş emri hemen değil, o tarih geldiğinde arka plan
    zamanlayıcısı tarafından otomatik başlatılır (bkz.
    run_scheduled_maintenance_starts)."""
    m.scheduled_start_at = scheduled_date
    db.commit()


def cancel_scheduled_maintenance_start(db: Session, m: "models.Maintenance") -> None:
    """Daha önce girilmiş bir otomatik-başlatma tarihini iptal eder — iş
    emri "Başlatılmayı Bekleyenler" listesinde zamansız aday olarak kalmaya
    devam eder."""
    m.scheduled_start_at = None
    db.commit()


def run_scheduled_maintenance_starts(db: Session) -> list["models.Maintenance"]:
    """Zamanlanmış başlangıç tarihi gelmiş (bugüne eşit veya geçmiş) ve henüz
    başlatılmamış tüm aday iş emirlerini bulur ve otomatik başlatır. Arka
    plan zamanlayıcısından (main.py) ve elle tetikleme uç noktasından
    çağrılır — km simülasyonu/süre bazlı bakım zamanlayıcılarıyla aynı desen."""
    today = date.today()
    candidates = (
        db.query(models.Maintenance)
        .filter(
            models.Maintenance.is_activated == False,  # noqa: E712
            models.Maintenance.scheduled_start_at.isnot(None),
            models.Maintenance.scheduled_start_at <= today,
        )
        .all()
    )
    for m in candidates:
        planned = m.scheduled_start_at
        activate_maintenance(db, m, None)
        log_action(
            db, None, "start", "maintenance", m.id,
            f"Zamanlanmış bakım otomatik başlatıldı: {m.vehicle.code} — {m.plan.name}",
            detail=f"Planlanan tarih: {planned.strftime('%d.%m.%Y')}" if planned else "",
        )
    return candidates


def create_manual_maintenance(
    db: Session,
    vehicle: "models.Vehicle",
    plan: "models.MaintenancePlan",
    target_km: int,
    actor: "models.User",
) -> models.Maintenance:
    """Faz 28 — Manuel bakım ekleme: otomatik tespiti (check_and_open_maintenances)
    beklemeden admin/mühendis/amir'in istediği araç + periyot + hedef km için
    elle bir iş emri açmasıdır (ör. otomatik algılama bir nedenle kaçırılmışsa
    ya da planlı periyodun dışında ek bir bakım gerekiyorsa). Otomatik açılan
    adayların aksine "Başlat" onayı beklemez — zaten bilinçli bir yetkili
    işlemi olduğundan doğrudan is_activated=True ile oluşturulur."""
    if target_km <= 0:
        raise ValueError("Hedef km 0'dan büyük olmalı")
    if not plan.is_active:
        raise ValueError(f"'{plan.name}' planı pasif — pasif bir plan için bakım iş emri açılamaz")
    if plan.vehicle_model and not _model_matches(plan.vehicle_model, vehicle.model):
        raise ValueError(
            f"'{plan.name}' planı yalnızca '{plan.vehicle_model}' modeli için tanımlı — "
            f"{vehicle.code} bu modelde değil ({vehicle.model or 'model belirtilmemiş'})"
        )
    # Hedef km'nin aracın güncel km'sinden makul bir aralıkta olduğunu
    # doğrula — çok uzak bir değer muhtemelen bir yazım hatasıdır (ör.
    # 25.000 km'lik bir planda 500.000 km hedef girilmesi gibi). Sınır,
    # planın periyoduna göre esnektir (küçük periyotlu planlarda katı,
    # büyük periyotlu planlarda daha geniş olsun diye) ama her zaman en az
    # 50.000 km'lik bir pay bırakır.
    max_gap = max(plan.interval_km * 3, 50_000)
    if abs(target_km - vehicle.current_km) > max_gap:
        def _tr_km(n: int) -> str:
            return f"{n:,}".replace(",", ".")
        raise ValueError(
            f"Hedef km ({_tr_km(target_km)}) aracın güncel kilometresinden "
            f"({_tr_km(vehicle.current_km)}) çok uzak görünüyor — lütfen kontrol edin."
        )
    exists = (
        db.query(models.Maintenance)
        .filter_by(vehicle_id=vehicle.id, plan_id=plan.id, target_km=target_km)
        .first()
    )
    if exists:
        raise ValueError("Bu araç için bu periyotta ve hedef km'de zaten bir iş emri var")

    m = models.Maintenance(
        vehicle_id=vehicle.id,
        plan_id=plan.id,
        target_km=target_km,
        km_at_open=vehicle.current_km,
        status="pending",
        is_activated=True,
        activated_at=datetime.now(),
        activated_by_id=actor.id,
    )
    db.add(m)
    db.flush()
    # Kademeli/kümülatif mantık burada da geçerli: hedef km, seçilen plandan
    # BAŞKA aktif bir planın periyoduna da tam bölünüyorsa (ör. 3M periyodu
    # seçilip hedef 1.530.000 girildiğinde, bu hedef aynı zamanda 1M'in de
    # tam katıdır), o planın maddeleri de checklist'e dahil edilir —
    # check_and_open_maintenances'taki otomatik açılışla AYNI davranış
    # (bkz. _copy_tree_snapshot). Seçilen plan hedefe tam bölünmese bile
    # (manuel girişte serbest bir hedef km olabilir) her zaman dahil edilir.
    active_plans = [
        p for p in db.query(models.MaintenancePlan).filter_by(is_active=True).all()
        if p.plan_type == "km" and p.interval_km > 0 and not p.is_catalog_only
        and _model_matches(p.vehicle_model, vehicle.model)
    ]
    applicable = [p for p in active_plans if target_km % p.interval_km == 0]
    if plan.id not in {p.id for p in applicable}:
        applicable.append(plan)
    _copy_tree_snapshot(db, m, applicable)
    db.commit()
    return m


def queue_upcoming_maintenance(
    db: Session, vehicle: "models.Vehicle", target_km: int
) -> models.Maintenance:
    """Bir yaklaşan km bakımını başlatma onayı kuyruğuna alır.

    Bu, elle bakım eklemekten farklıdır: iş emri ``is_activated=False`` olarak
    oluşturulur; yetkili daha sonra Bakımlar ekranından hemen başlatabilir ya
    da ileri bir tarihe zamanlayabilir. Aynı araç/hedef için tekrar çağrılırsa
    mevcut kaydı döndürür. Böylece 3M, 6M, 9M ve 15M gibi ortak katlarda 3M
    ağacı da aynı birleşik checklist'e dahil edilir.
    """
    active_plans = [
        p for p in db.query(models.MaintenancePlan).filter_by(is_active=True).all()
        if p.plan_type == "km" and p.interval_km > 0 and not p.is_catalog_only
        and _model_matches(p.vehicle_model, vehicle.model)
    ]
    applicable = [p for p in active_plans if target_km % p.interval_km == 0]
    if not applicable:
        raise ValueError("Bu araç ve hedef km için uygulanabilir aktif bakım planı yok")
    governing = max(applicable, key=lambda p: p.interval_km)
    existing = (
        db.query(models.Maintenance)
        .filter_by(vehicle_id=vehicle.id, plan_id=governing.id, target_km=target_km)
        .first()
    )
    if existing:
        return existing
    m = models.Maintenance(
        vehicle_id=vehicle.id,
        plan_id=governing.id,
        target_km=target_km,
        km_at_open=vehicle.current_km,
        status="pending",
        # DÜZELTME: bu satır önceden (eski, tek adımlı "Planlamaya Al" akışından
        # kalma) is_activated=True idi — ama yukarıdaki docstring ve ÇAĞRAN
        # taraf (main.py plan_upcoming_maintenance, frontend planUpcoming/
        # startUpcoming) her zaman bunu ÇAĞIRDIKTAN HEMEN SONRA ayrıca
        # POST /maintenances/{id}/start çağırıyor. is_activated burada zaten
        # True verilince /start "Bu bakım zaten başlatılmış" (400) hatası
        # atıyordu — kullanıcıya yanlışlıkla hata gösteriliyordu VE asıl
        # activate_maintenance() hiç çalışmadığı için activated_at/
        # activated_by_id boş kalıp "start" denetim kaydı hiç yazılmıyordu
        # (iş emri görünürde başlamış gibi dursa da gerçek aktivasyon
        # bilgisi eksik kalıyordu). False olması, ardından gelen /start
        # çağrısının işini gerçekten yapmasını sağlar.
        is_activated=False,
    )
    db.add(m)
    db.flush()
    _copy_tree_snapshot(db, m, applicable)
    db.commit()
    return m


def _plan_size(p: "models.MaintenancePlan") -> int:
    """Planın periyot büyüklüğü — km planlarında interval_km, süre
    planlarında interval_months. `_copy_tree_snapshot` içindeki sıralama/
    "tiers" etiketleme mantığı bunu tek, tür-bağımsız bir sayı olarak
    kullanır (bir çağrıda hem km hem süre planı asla karışmaz — bkz.
    check_and_open_maintenances / check_and_open_time_maintenances, her
    biri kendi türündeki planlarla ayrı ayrı çalışır)."""
    return p.interval_km if p.plan_type == "km" else (p.interval_months or 0)


def _plan_roots(db: Session, plan: models.MaintenancePlan) -> list[models.TreeNode]:
    """Bir planın kök ağaç düğümleri (yoksa genel/ortak şablona düşer)."""
    roots = (
        db.query(models.TreeNode)
        .filter_by(parent_id=None, plan_id=plan.id)
        .order_by(models.TreeNode.order_index)
        .all()
    )
    if not roots:
        roots = (
            db.query(models.TreeNode)
            .filter_by(parent_id=None, plan_id=None)
            .order_by(models.TreeNode.order_index)
            .all()
        )
    return roots


def _load_vehicle_mandatory_exceptions(db: Session, vehicle_id: int) -> dict[int, bool]:
    """Bir araç için tanımlı tüm TreeNodeVehicleException'ları
    {node_id: is_mandatory} sözlüğüne önceden yükler — ağaç kopyalanırken
    düğüm başına ayrı bir sorgu atmamak için (bkz. _copy_tree_snapshot /
    resync_maintenance_tiers)."""
    return {
        e.node_id: e.is_mandatory
        for e in db.query(models.TreeNodeVehicleException).filter_by(vehicle_id=vehicle_id).all()
    }


def _node_is_mandatory_for_vehicle(node: models.TreeNode, exceptions: dict[int, bool]) -> bool:
    """Bir düğümün bu araç için zorunlu olup olmadığı: önce araca özel bir
    istisna var mı bakılır (varsa o geçerlidir), yoksa düğümün GENEL (tüm
    araçlar) varsayılanı (TreeNode.is_mandatory) kullanılır."""
    return exceptions.get(node.id, node.is_mandatory)


def _copy_tree_snapshot(db: Session, m: models.Maintenance, applicable_plans: list[models.MaintenancePlan]) -> None:
    """Bakım ağacı şablon(lar)ını iş emrine kopyalar.

    Tek plan uygulanıyorsa (yaygın durum), o planın ağacı doğrudan kopyalanır.

    Birden fazla plan uygulanıyorsa (kademeli/kümülatif bakım) tüm planların
    ağaçları TEK bir ağaçta BİRLEŞTİRİLİR ve TEKİLLEŞTİRİLİR: aynı yoldaki
    (aynı ana bölüm > alt bölüm > … > madde) düğüm yalnızca bir kez oluşturulur,
    farklı periyotlardaki alt maddeleri o tek düğümün altında toplanır. Her
    madde, hangi periyot(lar)a ait olduğunu `tiers` alanında taşır (ör.
    "25000,50000,100000") — böylece bakım ekranında "Tümü" görünümünde her
    görev bir kez görünürken, tek bir periyot (ör. 25.000) seçildiğinde ağaç
    yine yalnızca o periyodun maddelerine göre süzülebilir.

    En büyük (yöneten) plan önce işlenir; böylece paylaşılan düğümlerde
    source_node_id yöneten plana ait olur ve 3D model parça eşleşmeleri
    (PartHotspot, plana özel) doğru maddeye bağlanmaya devam eder.
    """
    ordered = sorted(applicable_plans, key=_plan_size)
    mandatory_exceptions = _load_vehicle_mandatory_exceptions(db, m.vehicle_id)

    if len(ordered) <= 1:
        plan = ordered[0] if ordered else None
        roots = _plan_roots(db, plan) if plan else []

        def copy(node: models.TreeNode, parent_item_id: int | None):
            item = models.MaintenanceItem(
                maintenance_id=m.id,
                parent_id=parent_item_id,
                source_node_id=node.id,
                name=node.name,
                order_index=node.order_index,
                # Madde bu araç için "zorunlu değil" olarak işaretlenmişse
                # (genel varsayılan ya da bu araca özel istisna üzerinden),
                # iş emri açılır açılmaz otomatik tiklenmiş gelir — usta
                # ekstra bir işlem yapmasına gerek kalmaz.
                is_completed=not _node_is_mandatory_for_vehicle(node, mandatory_exceptions),
            )
            db.add(item)
            db.flush()
            for child in node.children:
                copy(child, item.id)

        for r in roots:
            copy(r, None)
        return

    # --- Çok planlı: birleşik + tekilleştirilmiş ağaç ---
    by_path: dict[tuple[str, ...], models.MaintenanceItem] = {}
    tiers: dict[tuple[str, ...], set[int]] = {}
    order_counter: dict[int | None, int] = {}

    def ensure(node: models.TreeNode, parent_item: models.MaintenanceItem | None,
               parent_path: tuple[str, ...], km: int) -> tuple[models.MaintenanceItem, tuple[str, ...]]:
        key = parent_path + (node.name,)
        item = by_path.get(key)
        if item is None:
            parent_id = parent_item.id if parent_item else None
            idx = order_counter.get(parent_id, 0)
            order_counter[parent_id] = idx + 1
            item = models.MaintenanceItem(
                maintenance_id=m.id,
                parent_id=parent_id,
                source_node_id=node.id,
                name=node.name,
                order_index=idx,
                is_completed=not _node_is_mandatory_for_vehicle(node, mandatory_exceptions),
            )
            db.add(item)
            db.flush()
            by_path[key] = item
            tiers[key] = set()
        tiers[key].add(km)
        return item, key

    def walk(node: models.TreeNode, parent_item: models.MaintenanceItem | None,
             parent_path: tuple[str, ...], km: int):
        item, key = ensure(node, parent_item, parent_path, km)
        for child in node.children:
            walk(child, item, key, km)

    # Yöneten (en büyük) plan önce → paylaşılan düğümler onun source_node_id'sini alır
    for plan in sorted(ordered, key=lambda p: -_plan_size(p)):
        for r in _plan_roots(db, plan):
            walk(r, None, tuple(), _plan_size(plan))

    for key, item in by_path.items():
        item.tiers = ",".join(str(k) for k in sorted(tiers[key]))


def resync_maintenance_tiers(db: Session, m: "models.Maintenance") -> dict:
    """Zaten açılmış (silinmemiş) bir iş emrinin checklist'ini, hedef km'sinin
    kapsadığı TÜM uygulanabilir km planlarıyla yeniden hizalar.

    Neden gerekli: create_manual_maintenance eskiden yalnızca kullanıcının
    seçtiği TEK planın ağacını kopyalıyordu (ör. 3M periyodu seçilip hedef
    1.530.000 girildiğinde, bu hedef aynı zamanda 1M'in de tam katı olsa
    bile 1M'in maddeleri hiç eklenmiyordu). Bu artık create_manual_maintenance
    içinde düzeltildi (bkz. _copy_tree_snapshot çağrısı), ama DÜZELTMEDEN
    ÖNCE açılmış, hâlâ açık (Bekliyor/Devam Ediyor) iş emirleri eksik kalmaya
    devam eder — bu fonksiyon onları, TAMAMLANMIŞ maddelere hiç dokunmadan,
    yalnızca eksik olan düğümleri ekleyerek düzeltir.

    Tamamlanmış (status='completed') bir iş emrinin ağacı asla değiştirilmez
    — o artık kalıcı bir kayıttır. Süre bazlı (aylık/yıllık) planlar için de
    desteklenmez (kademeli birleştirme yalnızca km planlarında var)."""
    if m.status == "completed":
        raise ValueError("Tamamlanmış bir iş emrinin ağacı yeniden hizalanamaz")
    if m.plan.plan_type != "km":
        raise ValueError("Bu işlem yalnızca km bazlı planlar için desteklenir")

    vehicle = m.vehicle
    active_plans = [
        p for p in db.query(models.MaintenancePlan).filter_by(is_active=True).all()
        if p.plan_type == "km" and p.interval_km > 0 and not p.is_catalog_only
        and _model_matches(p.vehicle_model, vehicle.model)
    ]
    applicable = [p for p in active_plans if m.target_km % p.interval_km == 0]
    if m.plan_id not in {p.id for p in applicable}:
        applicable.append(m.plan)

    # Mevcut ağaçtaki maddeleri yol (isim hiyerarşisi) anahtarına göre indeksle
    # — MaintenanceItem'da doğrudan bir "children" ilişkisi yok, parent_id
    # üzerinden elle kurulur (bkz. main.py _build_item_tree ile aynı desen).
    by_id = {it.id: it for it in m.items}
    children_of: dict[int | None, list[models.MaintenanceItem]] = {}
    for it in m.items:
        children_of.setdefault(it.parent_id, []).append(it)
    for kids in children_of.values():
        kids.sort(key=lambda x: x.order_index)

    existing_by_path: dict[tuple[str, ...], models.MaintenanceItem] = {}
    existing_tiers: dict[tuple[str, ...], set[int]] = {}

    def index_existing(item: models.MaintenanceItem, parent_path: tuple[str, ...]):
        key = parent_path + (item.name,)
        existing_by_path[key] = item
        existing_tiers[key] = set(int(x) for x in item.tiers.split(",") if x) if item.tiers else set()
        for child in children_of.get(item.id, []):
            index_existing(child, key)

    for r in children_of.get(None, []):
        index_existing(r, tuple())

    # Tek-plan snapshot'ında tiers hiç yazılmadığı için, bu iş emrini "yöneten"
    # planın kendi periyodu HER ZAMAN zaten temsil edilmiş sayılır.
    already_covered: set[int] = {m.plan.interval_km}
    for tset in existing_tiers.values():
        already_covered |= tset

    to_merge = [p for p in applicable if p.interval_km not in already_covered]
    if not to_merge:
        return {"added_items": 0, "merged_periods": [], "diagnostics": _diagnose_missing_tiers(db, m)}

    order_counter: dict[int | None, int] = {}
    for item in by_id.values():
        order_counter[item.parent_id] = max(order_counter.get(item.parent_id, -1), item.order_index) + 1

    added = 0
    mandatory_exceptions = _load_vehicle_mandatory_exceptions(db, m.vehicle_id)

    def ensure(node: models.TreeNode, parent_item: models.MaintenanceItem | None,
               parent_path: tuple[str, ...], km: int) -> tuple[models.MaintenanceItem, tuple[str, ...]]:
        nonlocal added
        key = parent_path + (node.name,)
        item = existing_by_path.get(key)
        if item is None:
            parent_id = parent_item.id if parent_item else None
            idx = order_counter.get(parent_id, 0)
            order_counter[parent_id] = idx + 1
            item = models.MaintenanceItem(
                maintenance_id=m.id,
                parent_id=parent_id,
                source_node_id=node.id,
                name=node.name,
                order_index=idx,
                is_completed=not _node_is_mandatory_for_vehicle(node, mandatory_exceptions),
            )
            db.add(item)
            db.flush()
            existing_by_path[key] = item
            existing_tiers[key] = set()
            added += 1
        existing_tiers[key].add(km)
        item.tiers = ",".join(str(k) for k in sorted(existing_tiers[key]))
        return item, key

    def walk(node: models.TreeNode, parent_item: models.MaintenanceItem | None,
             parent_path: tuple[str, ...], km: int):
        item, key = ensure(node, parent_item, parent_path, km)
        for child in node.children:
            walk(child, item, key, km)

    for plan in to_merge:
        for r in _plan_roots(db, plan):
            walk(r, None, tuple(), plan.interval_km)

    db.commit()
    return {"added_items": added, "merged_periods": [p.interval_km for p in to_merge], "diagnostics": []}


def _diagnose_missing_tiers(db: Session, m: "models.Maintenance") -> list[dict]:
    """resync_maintenance_tiers hiçbir eksik periyot bulamadığında ("checklist
    zaten güncel") gerçekten öyle mi yoksa bir plan SESSİZCE mi dışlanıyor
    anlaşılamıyordu (kullanıcı geri bildirimi: "3M gelmiyor" ama neden
    olduğu belirsizdi). Bu fonksiyon, aracın modeliyle eşleşen (bkz.
    _model_matches — büyük/küçük harf ve Türkçe karakterden bağımsız) HER
    km bazlı planı, aktiflik/katalog/bölünebilirlik durumuyla birlikte
    döner ki hangi planın neden dahil olmadığı tek bakışta görülebilsin."""
    vehicle = m.vehicle
    all_km_plans = [
        p for p in db.query(models.MaintenancePlan).all()
        if p.plan_type == "km" and p.interval_km > 0 and _model_matches(p.vehicle_model, vehicle.model)
    ]
    out: list[dict] = []
    for p in sorted(all_km_plans, key=lambda p: p.interval_km):
        divides = m.target_km % p.interval_km == 0
        reasons = []
        if not p.is_active:
            reasons.append("plan pasif")
        if p.is_catalog_only:
            reasons.append("plan katalog-amaçlı işaretli")
        if not divides:
            reasons.append(f"hedef km ({m.target_km}) bu periyoda ({p.interval_km}) tam bölünmüyor")
        out.append({
            "plan_id": p.id,
            "name": p.name,
            "interval_km": p.interval_km,
            "vehicle_model": p.vehicle_model,
            "is_active": p.is_active,
            "is_catalog_only": p.is_catalog_only,
            "divides_target": divides,
            "excluded_reason": ", ".join(reasons) if reasons else None,
        })
    return out


# ------------------------------------------------------- yaklaşan bakımlar
def compute_upcoming_maintenances(
    db: Session,
    vehicles: list[models.Vehicle] | None = None,
    plans: list[models.MaintenancePlan] | None = None,
    limit: int | None = None,
    include_open: bool = False,
) -> list[dict]:
    """Yaklaşan bakımları, otomatik-açma ile AYNI kümülatif mantıkla hesaplar:
    her aday hedef km için o hedefe bölünen tüm planlar tek bir bakımda
    birleştirilir; ayrı ayrı "25.000 yaklaşıyor / 50.000 yaklaşıyor" satırları
    gösterilmez — o noktaya denk gelen TEK bir birleşik bakım (yöneten =
    hedefe bölünen en büyük periyot) gösterilir. Kapsanan tüm periyotlar
    `covered_tiers` alanında döner.

    Dashboard'daki "Yaklaşan Bakımlar" kutusu (limit=8) ve Araçlar
    sayfasındaki kırmızı vurgulama (limit=None) ortak kullanır.

    Dönüş: [{"vehicle": Vehicle, "plan_name": str, "target_km": int,
             "remaining_km": int, "covered_tiers": list[int]}, ...]
    """
    if vehicles is None:
        vehicles = db.query(models.Vehicle).all()
    if plans is None:
        plans = db.query(models.MaintenancePlan).filter_by(is_active=True).all()
    today = date.today()

    upcoming: list[dict] = []
    for v in vehicles:
        km = v.current_km
        # Yalnızca "genel" (vehicle_model="") planlar veya bu aracın
        # modeliyle birebir eşleşen planlar bu araç için değerlendirilir.
        active_plans = [
            p for p in plans
            if p.interval_km > 0 and _model_matches(p.vehicle_model, v.model)
        ]
        if not active_plans:
            continue

        # ÖNEMLİ: Aday hedefler check_and_open_maintenances ile BİREBİR AYNI
        # yöntemle (temel birim = tüm periyotların GCD'si) üretilir. Eskiden
        # her plan için AYRI AYRI (kendi periyoduna göre) aday üretiliyordu;
        # bu da ör. araç 500.000 km bakımına yaklaşırken (henüz 500.000'e
        # ulaşmamışken) 25.000 km'lik planın kendi geçmiş katı için de ayrı
        # bir "25.000 km bakımı geçti" adayı doğurup yanlış/çelişkili uyarı
        # veriyordu. Temel birim yaklaşımında araç için HER ZAMAN yalnızca
        # iki aday (geçilen son nokta, bir sonraki nokta) vardır ve doğru
        # "yöneten" plan (o noktaya bölünen en büyük periyot) otomatik seçilir
        # — böylece gösterilen tek uyarı, gerçekte açılacak/açılmış iş emriyle
        # her zaman tutarlıdır.
        base_unit = active_plans[0].interval_km
        for p in active_plans[1:]:
            base_unit = math.gcd(base_unit, p.interval_km)
        if base_unit <= 0:
            continue

        targets: set[int] = set()
        targets.add((km // base_unit) * base_unit)
        targets.add(((km // base_unit) + 1) * base_unit)
        targets.discard(0)

        for target in targets:
            applicable = [p for p in active_plans if target % p.interval_km == 0]
            if not applicable:
                continue
            governing = max(applicable, key=lambda p: p.interval_km)
            exists = (
                db.query(models.Maintenance)
                .filter_by(vehicle_id=v.id, plan_id=governing.id, target_km=target)
                .first()
            )
            pending_scheduled_start_at = None
            if exists is not None and not exists.is_activated:
                # Faz 26: admin/mühendis henüz "Başlat" demediyse, bu iş emri
                # HENÜZ açılmamış gibi davranılır (yalnızca önizleme
                # penceresine girdiğinde "yaklaşıyor" olarak gösterilir).
                # scheduled_start_at'i (varsa) exists=None yapmadan ÖNCE
                # kaydediyoruz ki arayüz "Zamanlandı: X" rozetini gösterebilsin.
                pending_scheduled_start_at = exists.scheduled_start_at
                exists = None
            # Başlangıç (baseline) km'sinin ALTINDA/eşitinde kalan hedefler,
            # GERÇEK bir iş emri YOKKEN (yalnızca otomatik önizleme adayı iken)
            # hiç gösterilmez — araç sisteme girmeden önce yapılmış sayılan
            # bakımlar (ör. 200.000 km) "geciken/yaklaşan" olarak asla
            # listelenmez. Elle açılmış gerçek bir iş emri varsa (admin bilinçli
            # eklemiş) baseline'dan bağımsız her zaman görünür. baseline None ise
            # kural uygulanmaz (check_and_open ile birebir aynı mantık).
            if exists is None and v.baseline_km is not None and target <= v.baseline_km:
                continue
            if exists is not None:
                # Tamamlanmış bakım her zaman listeden düşer. Açık (Bekliyor/
                # Devam Ediyor) iş emri ise include_open=True çağrılarında
                # (Araçlar sayfası uyarıları) listede kalır — bakım henüz
                # yapılmadıysa araç "yaklaşan/zamanı gelen" görünmeye devam
                # etmeli. Dashboard (include_open=False) bunları ayrıca
                # due_now listesinde gösterdiği için burada atlar.
                if not include_open or exists.status == "completed":
                    continue
            elif include_open:
                # Araçlar sayfası (include_open=True): "Yaklaşıyor" durumu
                # planın dar otomatik-açma toleransından bağımsız olarak
                # APPROACHING_WITHIN_KM (5.000 km) öncesinden başlamalı, ve
                # "Bakım gecikti" durumu iş emri HİÇ açılmamış olsa bile (ne
                # kadar aşılmış olursa olsun) gösterilebilmeli — bu yüzden
                # burada yalnızca "henüz çok erken" (>5.000 km kala) adaylar
                # elenir; ne kadar gecikmiş olduğuna üst sınır KONULMAZ.
                if km < target - APPROACHING_WITHIN_KM:
                    continue
            else:
                # ÖNEMLİ: Önizleme penceresi (PREVIEW_LOWER/UPPER_PCT) yalnızca
                # HENÜZ AÇILMAMIŞ adaylar için "yaklaşıyor mu?" kontrolüdür.
                # Zaten gerçek bir iş emri açılmışsa (yukarıdaki exists bloğu)
                # bu pencereyi UYGULAMAYIZ — planın kendi (farklı olabilen)
                # açma toleransıyla erken açılmış bir iş emri, burada dar
                # önizleme penceresine takılıp listeden düşerse, o gerçek/
                # bekleyen iş emri "yaklaşan bakım" olarak hiç görünmez ve
                # yerine (yanlışlıkla) daha eski/aşılmış başka bir adayın
                # gösterilmesine yol açar. Dashboard'un dar (limit=8) kutusu
                # kalabalıklaşmasın diye burada (include_open=False) hâlâ
                # planın KENDİ dar önizleme penceresi kullanılır.
                lower_bound = target - min(governing.interval_km * PREVIEW_LOWER_PCT / 100, MAX_TOLERANCE_KM)
                upper_bound = target + min(governing.interval_km * PREVIEW_UPPER_PCT / 100, MAX_TOLERANCE_KM)
                if not (lower_bound <= km <= upper_bound):
                    continue
            # "Bakım takip periyodu" penceresinin yarı genişlikleri (Yaklaşıyor/
            # Planlanacak/Planlandı/Gecikti sınıflandırması için) — yukarıdaki
            # lower_bound/upper_bound ile AYNI formül, ama artık `exists`
            # dalından bağımsız olarak HER ZAMAN hesaplanır (o kontrol yalnızca
            # henüz açılmamış adayları önizleme listesine alıp almamayı
            # belirliyordu; durum sınıflandırması ise açılmış olsun olmasın
            # her zaman gerekir).
            window_lower_offset = min(governing.interval_km * PREVIEW_LOWER_PCT / 100, MAX_TOLERANCE_KM)
            window_upper_offset = min(governing.interval_km * PREVIEW_UPPER_PCT / 100, MAX_TOLERANCE_KM)
            remaining_km = target - km
            if not include_open and exists is None and remaining_km <= 0:
                # Dashboard'daki "Yaklaşan Bakımlar" kutusu (include_open=
                # False) yalnızca GERÇEKTEN yaklaşan (hedefe henüz ulaşılmamış)
                # adayları göstermelidir. Hedefi çoktan geçmiş ama kimse
                # "Başlat" demediği için hâlâ aday sayılan bir kayıt burada
                # gösterilirse, sınırlı (limit=8) kutunun slotlarını işgal
                # edip gerçekten yaklaşan başka bir aracı dışarıda bırakır —
                # bu tür unutulmuş/başlatılmamış adaylar zaten Bakımlar
                # sayfasındaki "Başlatılmayı Bekleyenler" listesinde ayrıca
                # (aciliyet sırasına göre) görünür, burada tekrar gösterilmez.
                continue
            # Günlük ortalama km varsayımıyla kaç güne kaldığı tahmini
            # (yalnızca bilgi amaçlı gösterim için — kritiklik artık buna
            # göre değil, aşağıdaki periyot-oranlı km eşiklerine göre belirlenir).
            # Hedef zaten geçilmişse (remaining_km <= 0) 0 gün.
            estimated_days = (
                math.ceil(remaining_km / DEFAULT_CALENDAR_DAILY_KM) if remaining_km > 0 else 0
            )
            # Kritiklik eşikleri, "yöneten" planın periyoduna (governing.interval_km)
            # oranlı: kalan km bu periyodun %15'inin altına düşünce "kritik" (urgent),
            # %5'inin altına düşünce "çok kritik" (critical) olur. remaining_km araç
            # kilometresi arttıkça yalnızca azalabildiği için "çok kritik" durumu
            # doğal olarak kalıcıdır (bir kez tetiklenince geri dönmez).
            kritik_threshold_km = governing.interval_km * KRITIK_PCT_OF_INTERVAL / 100
            cok_kritik_threshold_km = governing.interval_km * COK_KRITIK_PCT_OF_INTERVAL / 100
            # "Kritik/Çok Kritik" alarm rozeti yalnızca gerçekten AÇILMIŞ
            # (is_activated=True) bir iş emri için gösterilir. Henüz kimse
            # "Başlat" demediyse (exists burada None'dır — ya hiç kayıt yok
            # ya da is_activated=False) hedef ne kadar geçilmiş olursa olsun
            # yalnızca nötr "Bakım Yaklaşıyor" olarak görünür — aksi halde
            # başlatılmayı bekleyen bir aday, sanki az önce tamamlanan bir
            # bakımla ilgiliymiş gibi yanlışlıkla "hâlâ kritik" izlenimi verir.
            is_started = exists is not None
            # Takvim sayfasındaki sürükle-bırak yeniden zamanlama için: bu
            # hedefin GERÇEK tolerans penceresi (planın kendi threshold_type
            # ayarına göre — yukarıdaki dar PREVIEW_* önizleme penceresinden
            # FARKLI, "bakım ne zamana kadar ertelenebilir/öne alınabilir"
            # sorusunun asıl cevabı) km cinsinden hesaplanır, sonra günlük
            # ortalama km varsayımıyla takvim tarihine çevrilir. Aktif
            # (is_activated=True) bir kayıt varsa arayüz zaten kendi
            # plan nesnesinden (m.plan) aynı hesaplamayı birebir yapabildiği
            # için bu alanlar esas olarak HENÜZ başlatılmamış (gri) adaylar
            # içindir.
            plan_lower_km, plan_upper_km = plan_window(governing, target)
            # ÖNEMLİ yuvarlama kuralı: (plan_lower_km - km) normalde NEGATİFTİR
            # (alt sınır km'si zaten geçilmiş) — math.floor (sıfırdan uzağa,
            # daha NEGATİFE yuvarlar) kullanılır ki alt sınır tarihi olması
            # gerekenden GEÇ değil, en az olması gerektiği kadar ERKEN çıksın.
            # math.ceil kullanılsaydı pencere ~1 gün daha DAR olurdu — Takvim
            # sayfasında "tolerans içindeyken bile sürükleyemiyorum" şikâyetinin
            # kök nedeni buydu. Üst sınırda ise math.ceil (yukarı, daha CÖMERT)
            # doğru yöndür.
            days_to_lower = math.floor((plan_lower_km - km) / DEFAULT_CALENDAR_DAILY_KM)
            days_to_upper = math.ceil((plan_upper_km - km) / DEFAULT_CALENDAR_DAILY_KM)
            window_lower_date = today + timedelta(days=days_to_lower)
            window_upper_date = today + timedelta(days=max(days_to_lower, days_to_upper))
            upcoming.append(
                {
                    "vehicle": v,
                    "plan_name": governing.name,
                    "target_km": target,
                    "remaining_km": remaining_km,
                    "covered_tiers": sorted(p.interval_km for p in applicable),
                    "estimated_days": estimated_days,
                    "urgent": is_started and remaining_km <= kritik_threshold_km,
                    "critical": is_started and remaining_km <= cok_kritik_threshold_km,
                    # Yaklaşıyor/Planlanacak/Planlandı/Bakım gecikti — bkz.
                    # _classify_upcoming_status docstring'i.
                    "status": _classify_upcoming_status(
                        remaining_km, window_lower_offset, window_upper_offset,
                        APPROACHING_WITHIN_KM, is_started,
                    ),
                    # Zaten açılmış (Bekliyor/Devam Ediyor) gerçek bir iş
                    # emrine karşılık geliyorsa o kaydın id'si; henüz
                    # açılmamış (yalnızca yaklaşan önizleme) bir aday ise None.
                    "maintenance_id": exists.id if exists is not None else None,
                    "assigned_shift_id": exists.assigned_shift_id if exists is not None else None,
                    "assigned_shift_name": (
                        exists.assigned_shift.name if exists is not None and exists.assigned_shift else None
                    ),
                    # Bkz. compute_upcoming_time_maintenances — km sürümünde
                    # önceden eksikti (aday henüz başlatılmamışken "İleri
                    # Tarihe Al" ile zamanlanmışsa Takvim/Bakımlar sayfası
                    # bunu göremiyordu), burada da aynı alan eklendi.
                    "scheduled_start_at": pending_scheduled_start_at,
                    "window_lower_date": window_lower_date,
                    "window_upper_date": window_upper_date,
                }
            )

    # Aynı araçta İKİ aday (geçilen son nokta + bir sonraki nokta) için de
    # gerçekten açılmış iş emri varsa (ör. hem 500.000 km'lik eski/aşılmış
    # bir iş emri hem de bir sonraki 525.000 gibi yakın bir iş emri), yalnızca
    # hedef km'si EN BÜYÜK olan gösterilir — dedupe_open_by_vehicle ile aynı
    # kural (küçük periyot, daha büyük bir checkpoint'e ulaşılınca zaten
    # kapsanmış sayılır). Bunu yapmazsak eski/aşılmış iş emri (çok daha
    # negatif remaining_km'si nedeniyle) sıralamada öne geçip "500.000 km
    # bakımı X gün geçti" gibi yanıltıcı bir uyarının, gerçekte yaklaşan
    # "25.000 km bakımı"nın önüne geçmesine yol açar.
    best_open_target: dict[int, int] = {}
    for u in upcoming:
        if u["maintenance_id"] is not None:
            vid = u["vehicle"].id
            if u["target_km"] > best_open_target.get(vid, -1):
                best_open_target[vid] = u["target_km"]
    upcoming = [
        u for u in upcoming
        if u["maintenance_id"] is None or u["target_km"] == best_open_target[u["vehicle"].id]
    ]

    upcoming.sort(key=lambda u: u["remaining_km"])
    if limit is not None:
        upcoming = upcoming[:limit]
    return upcoming


def compute_upcoming_time_maintenances(
    db: Session,
    vehicles: list[models.Vehicle] | None = None,
    plans: list[models.MaintenancePlan] | None = None,
    limit: int | None = None,
    include_open: bool = False,
) -> list[dict]:
    """compute_upcoming_maintenances'ın süre bazlı planlar için karşılığı.
    KM'deki "günlük ortalama km" varsayımıyla gün tahmini yapmaya gerek
    yoktur — hedef zaten bir takvim tarihidir, "kalan gün" doğrudan
    (target_date - bugün).days'tir.

    Dönüş sözlüğü km sürümüyle AYNI anahtarları taşır ve BİLEREK aynı
    "birim-agnostik" alanları yeniden kullanır ki çağıran taraf (main.py
    `_compute_upcoming_merged`) iki listeyi birleştirip tek bir sırada
    gösterebilsin:
      - target_km: gerçek Maintenance satırındaki NEGATİF sentinel
        (-hedef ay sayısı) — target_km'ye göre eşleşen mevcut genel kodun
        değişiklik yapılmadan çalışmasını sağlar.
      - remaining_km: burada KM değil, İMZALI kalan gün sayısıdır (negatifse
        gecikmiş demektir) — km tarafındaki "remaining_km <= 0 → gecikti"
        kuralıyla birebir uyumlu kalır.
      - target_date: gerçek takvim tarihi (yalnızca süre bakımlarında dolu)."""
    if vehicles is None:
        vehicles = db.query(models.Vehicle).all()
    if plans is None:
        plans = db.query(models.MaintenancePlan).filter_by(is_active=True).all()
    today = date.today()

    upcoming: list[dict] = []
    for v in vehicles:
        if not v.commissioning_date:
            continue
        elapsed = elapsed_months(v.commissioning_date, today)
        active_plans = [
            p for p in plans
            if p.plan_type == "time" and p.interval_months
            and _model_matches(p.vehicle_model, v.model)
        ]
        if not active_plans:
            continue

        base_unit = active_plans[0].interval_months
        for p in active_plans[1:]:
            base_unit = math.gcd(base_unit, p.interval_months)
        if base_unit <= 0:
            continue

        targets: set[int] = set()
        targets.add((elapsed // base_unit) * base_unit)
        targets.add(((elapsed // base_unit) + 1) * base_unit)
        targets.discard(0)

        for target in targets:
            applicable = [p for p in active_plans if target % p.interval_months == 0]
            if not applicable:
                continue
            governing = max(applicable, key=lambda p: p.interval_months)
            sentinel_km = -target
            exists = (
                db.query(models.Maintenance)
                .filter_by(vehicle_id=v.id, plan_id=governing.id, target_km=sentinel_km)
                .first()
            )
            pending_scheduled_start_at = None
            if exists is not None and not exists.is_activated:
                pending_scheduled_start_at = exists.scheduled_start_at
                exists = None
            target_date = add_months(v.commissioning_date, target)
            signed_days = (target_date - today).days
            if exists is not None:
                if not include_open or exists.status == "completed":
                    continue
            elif include_open:
                # Araçlar sayfası: bkz. compute_upcoming_maintenances'daki
                # AYNI genişletme — "Yaklaşıyor" durumu ay bazlı dar
                # önizleme penceresinden bağımsız olarak URGENT_WITHIN_DAYS
                # (14 gün) öncesinden başlamalı, "Bakım gecikti" durumu ise
                # iş emri hiç açılmamış olsa bile (ne kadar gecikmiş olursa
                # olsun) gösterilebilmeli.
                if signed_days > URGENT_WITHIN_DAYS:
                    continue
            else:
                lower_bound = target - governing.interval_months * PREVIEW_LOWER_PCT / 100
                upper_bound = target + governing.interval_months * PREVIEW_UPPER_PCT / 100
                if not (lower_bound <= elapsed <= upper_bound):
                    continue
            if not include_open and exists is None and signed_days <= 0:
                # Bkz. compute_upcoming_maintenances — aynı kural: Dashboard
                # kutusu yalnızca gerçekten yaklaşan (henüz tarihi gelmemiş)
                # adayları gösterir, başlatılmamış+tarihi çoktan geçmiş
                # adaylar burada değil "Başlatılmayı Bekleyenler"de görünür.
                continue
            estimated_days = max(0, signed_days)
            # Bkz. compute_upcoming_maintenances — aynı kural: henüz
            # "Başlat" denmemiş bir aday hiçbir zaman kritik/çok kritik
            # rozetiyle gösterilmez, yalnızca nötr "yaklaşıyor" olarak görünür.
            is_started = exists is not None
            # Bkz. compute_upcoming_maintenances'daki AYNI ekleme — Takvim'in
            # tek-takvim sürükle-bırak kısıtlaması için gerçek (planın kendi
            # threshold_type ayarına göre) tolerans penceresi burada da
            # takvim tarihine çevrilip döndürülür.
            lower_months, upper_months = time_plan_window(governing, target)
            # ÖNEMLİ yuvarlama kuralı: pencere GENİŞLİĞİ (gün cinsine çevrilen
            # ay farkı) burada da her zaman math.ceil ile yukarı yuvarlanır —
            # round() kullanılsaydı bazı durumlarda ~0.5 gün aşağı yuvarlayıp
            # penceremiz gerçek tolerans olduğundan DAR görünürdü (bkz.
            # compute_upcoming_maintenances'daki AYNI düzeltme).
            window_lower_date = target_date - timedelta(
                days=math.ceil((target - lower_months) * _AVG_DAYS_PER_MONTH)
            )
            window_upper_date = target_date + timedelta(
                days=math.ceil((upper_months - target) * _AVG_DAYS_PER_MONTH)
            )
            if window_upper_date < window_lower_date:
                window_upper_date = window_lower_date
            upcoming.append(
                {
                    "vehicle": v,
                    "plan_name": governing.name,
                    "target_km": sentinel_km,
                    "remaining_km": signed_days,
                    "target_date": target_date,
                    "covered_tiers": sorted(p.interval_months for p in applicable),
                    "estimated_days": estimated_days,
                    "urgent": is_started and signed_days <= URGENT_WITHIN_DAYS,
                    "critical": is_started and signed_days <= CRITICAL_WITHIN_DAYS,
                    # Yaklaşıyor/Planlanacak/Planlandı/Bakım gecikti — km
                    # tarafındaki APPROACHING_WITHIN_KM/pencere yerine burada
                    # gün bazlı mevcut sabitler (URGENT_WITHIN_DAYS=dış sınır,
                    # CRITICAL_WITHIN_DAYS=pencere yarı genişliği) kullanılır.
                    "status": _classify_upcoming_status(
                        signed_days, CRITICAL_WITHIN_DAYS, CRITICAL_WITHIN_DAYS,
                        URGENT_WITHIN_DAYS, is_started,
                    ),
                    "maintenance_id": exists.id if exists is not None else None,
                    "assigned_shift_id": exists.assigned_shift_id if exists is not None else None,
                    "assigned_shift_name": (
                        exists.assigned_shift.name if exists is not None and exists.assigned_shift else None
                    ),
                    "scheduled_start_at": pending_scheduled_start_at,
                    "window_lower_date": window_lower_date,
                    "window_upper_date": window_upper_date,
                }
            )

    best_open_target: dict[int, int] = {}
    for u in upcoming:
        if u["maintenance_id"] is not None:
            vid = u["vehicle"].id
            if abs(u["target_km"]) > abs(best_open_target.get(vid, 0)):
                best_open_target[vid] = u["target_km"]
    upcoming = [
        u for u in upcoming
        if u["maintenance_id"] is None or u["target_km"] == best_open_target[u["vehicle"].id]
    ]

    upcoming.sort(key=lambda u: u["estimated_days"])
    if limit is not None:
        upcoming = upcoming[:limit]
    return upcoming


def compute_maintenance_calendar_projections(
    db: Session,
    vehicles: list[models.Vehicle] | None = None,
    plans: list[models.MaintenancePlan] | None = None,
    daily_km: float = DEFAULT_CALENDAR_DAILY_KM,
) -> list[dict]:
    """Takvim görünümü için: her aracın bir sonraki bakım hedefine ait
    -%5/+%10 önizleme penceresini (km), aracın günde ortalama `daily_km`
    ilerlediği varsayımıyla takvim tarihlerine çevirir.

    Pencere [hedef - periyot*%5, hedef + periyot*%10] aralığındaki her gün
    "bu araç için bakım bekleniyor" olarak takvimde işaretlenebilir.

    Bir hedef için iş emri zaten TAMAMLANMIŞSA bu hedef bir daha hiç
    döndürülmez (geçmişte kapanmış periyot takvimde tekrar görünmez).
    Bekliyor/Devam Ediyor durumundaki iş emirleri için pencere, iş emri
    tamamlanana kadar görünmeye devam eder.

    Henüz hiç iş emri açılmamış bir hedef ise, yalnızca aracın GÜNCEL km'si
    zaten kendi önizleme penceresine girmişse (Canlı Alarm Merkezi ve
    Dashboard'daki "yaklaşıyor" ile birebir aynı tanım) döndürülür — aksi
    halde her aracın çok uzak gelecekteki bir sonraki periyodu da her zaman
    görünür ve takvim anlamsızca kalabalıklaşırdı.

    Dönüş: [{"vehicle", "plan_name", "target_km", "remaining_km",
             "covered_tiers", "window_start" (ISO tarih), "window_end"
             (ISO tarih), "status" (iş emri durumu ya da None), "maintenance_id"}]
    """
    if vehicles is None:
        vehicles = db.query(models.Vehicle).all()
    if plans is None:
        plans = db.query(models.MaintenancePlan).filter_by(is_active=True).all()

    today = date.today()
    rate = daily_km if daily_km > 0 else DEFAULT_CALENDAR_DAILY_KM
    out: list[dict] = []

    for v in vehicles:
        km = v.current_km
        # Yalnızca "genel" (vehicle_model="") planlar veya bu aracın
        # modeliyle birebir eşleşen planlar bu araç için değerlendirilir.
        active_plans = [
            p for p in plans
            if p.interval_km > 0 and _model_matches(p.vehicle_model, v.model)
        ]
        # Hem henüz geçilmemiş bir sonraki hedef, hem de (iş emri zaten
        # açılmış ama tamamlanmamış olabileceği için) az önce geçilmiş
        # hedef aday olarak değerlendirilir.
        targets: set[int] = set()
        for p in active_plans:
            targets.add((km // p.interval_km) * p.interval_km)
            targets.add(((km // p.interval_km) + 1) * p.interval_km)
        targets.discard(0)

        for target in sorted(targets):
            applicable = [p for p in active_plans if target % p.interval_km == 0]
            if not applicable:
                continue
            governing = max(applicable, key=lambda p: p.interval_km)

            existing = (
                db.query(models.Maintenance)
                .filter_by(vehicle_id=v.id, plan_id=governing.id, target_km=target)
                .first()
            )
            if existing is not None and existing.status == "completed":
                continue  # tamamlanmış periyot — bir daha gösterilmez
            if existing is not None and not existing.is_activated:
                # Faz 26: admin/mühendis henüz "Başlat" demediyse, takvimde de
                # HENÜZ açılmamış gibi davranılır — aksi halde bir aday, admin
                # onaylamadan Takvim'de gerçek bir iş emriymiş gibi görünürdü.
                existing = None

            lower_km = target - min(governing.interval_km * PREVIEW_LOWER_PCT / 100, MAX_TOLERANCE_KM)
            upper_km = target + min(governing.interval_km * PREVIEW_UPPER_PCT / 100, MAX_TOLERANCE_KM)

            if existing is None and not (lower_km <= km <= upper_km):
                # HENÜZ bir iş emri açılmamışsa, bu hedef yalnızca önizleme
                # penceresine (aynı Canlı Alarm Merkezi/Dashboard'daki
                # "yaklaşıyor" tanımı — bkz. compute_upcoming_maintenances)
                # girdiğinde takvimde gösterilir. Aksi halde her aracın çok
                # uzak gelecekteki bir sonraki periyodu da HER ZAMAN
                # görünürdü — kullanıcı talebi: "henüz açılmayanlar
                # gözükmesin, açılacakları zaman gözükmeye başlasın."
                continue
            days_to_lower = math.ceil((lower_km - km) / rate)
            days_to_upper = math.ceil((upper_km - km) / rate)
            window_start = today + timedelta(days=max(0, days_to_lower))
            window_end = today + timedelta(days=max(0, days_to_upper))
            if window_end < window_start:
                window_end = window_start

            out.append(
                {
                    "vehicle": v,
                    "plan_name": governing.name,
                    "target_km": target,
                    "target_date": None,
                    "remaining_km": target - km,
                    "covered_tiers": sorted(p.interval_km for p in applicable),
                    "window_start": window_start.isoformat(),
                    "window_end": window_end.isoformat(),
                    "status": existing.status if existing else None,
                    "maintenance_id": existing.id if existing else None,
                }
            )

    out.sort(key=lambda d: d["window_start"])
    return out


# Ay -> gün yaklaşık dönüşümü, yalnızca Takvim'deki süre bazlı önizleme
# penceresinin (ay cinsinden lower/upper toleransı) takvim gününe
# çevrilmesi için kullanılır — gerçek hedef tarih (target_date) her zaman
# add_months ile TAKVİM-DOĞRU hesaplanır, bu sabit yalnızca pencere
# GENİŞLİĞİ (±tolerans) için kabaca kullanılır.
_AVG_DAYS_PER_MONTH = 30.44


def compute_time_maintenance_calendar_projections(
    db: Session,
    vehicles: list[models.Vehicle] | None = None,
    plans: list[models.MaintenancePlan] | None = None,
) -> list[dict]:
    """compute_maintenance_calendar_projections'ın süre bazlı (aylık/yıllık)
    planlar için karşılığı — compute_upcoming_time_maintenances'ın km bazlı
    compute_upcoming_maintenances'a karşılığı olmasıyla AYNI ilişki.

    DÜZELTME: Takvim (GET /maintenances/projections) önceden YALNIZCA km
    bazlı planları hesaplıyordu (compute_maintenance_calendar_projections,
    yalnızca interval_km>0 filtreler) — süre bazlı bir planı olan araçlar
    Takvim'de HİÇBİR ZAMAN görünmüyordu, oysa Dashboard/Bakım Akışı
    (_compute_upcoming_merged) ikisini de birleştiriyordu. Bu fonksiyon o
    eksikliği kapatır; main.py get_maintenance_projections ikisini birleştirir.

    Dönüş, km sürümüyle AYNI anahtarları taşır (target_km burada dahili
    negatif sentineldir, target_date gerçek hedeftir — bkz.
    compute_upcoming_time_maintenances docstring'i) ki çağıran taraf iki
    listeyi doğrudan birleştirip tek takvimde gösterebilsin."""
    if vehicles is None:
        vehicles = db.query(models.Vehicle).all()
    if plans is None:
        plans = db.query(models.MaintenancePlan).filter_by(is_active=True).all()

    today = date.today()
    out: list[dict] = []

    for v in vehicles:
        if not v.commissioning_date:
            continue
        elapsed = elapsed_months(v.commissioning_date, today)
        active_plans = [
            p for p in plans
            if p.plan_type == "time" and p.interval_months
            and _model_matches(p.vehicle_model, v.model)
        ]
        if not active_plans:
            continue

        base_unit = active_plans[0].interval_months
        for p in active_plans[1:]:
            base_unit = math.gcd(base_unit, p.interval_months)
        if base_unit <= 0:
            continue

        targets: set[int] = set()
        targets.add((elapsed // base_unit) * base_unit)
        targets.add(((elapsed // base_unit) + 1) * base_unit)
        targets.discard(0)

        for target in sorted(targets):
            applicable = [p for p in active_plans if target % p.interval_months == 0]
            if not applicable:
                continue
            governing = max(applicable, key=lambda p: p.interval_months)
            sentinel_km = -target

            existing = (
                db.query(models.Maintenance)
                .filter_by(vehicle_id=v.id, plan_id=governing.id, target_km=sentinel_km)
                .first()
            )
            if existing is not None and existing.status == "completed":
                continue  # tamamlanmış periyot — bir daha gösterilmez
            if existing is not None and not existing.is_activated:
                # Faz 26 — km sürümüyle AYNI kural: henüz "Başlat" denmediyse
                # Takvim'de de henüz açılmamış gibi davranılır.
                existing = None

            lower_months, upper_months = time_plan_window(governing, target)

            if existing is None and not (lower_months <= elapsed <= upper_months):
                # Km sürümüyle AYNI kural: henüz iş emri açılmamışsa yalnızca
                # önizleme penceresine girildiğinde takvimde gösterilir.
                continue

            target_date = add_months(v.commissioning_date, target)
            window_start = target_date - timedelta(days=round((target - lower_months) * _AVG_DAYS_PER_MONTH))
            window_end = target_date + timedelta(days=round((upper_months - target) * _AVG_DAYS_PER_MONTH))
            if window_end < window_start:
                window_end = window_start

            out.append(
                {
                    "vehicle": v,
                    "plan_name": governing.name,
                    "target_km": sentinel_km,
                    "target_date": target_date,
                    "remaining_km": (target_date - today).days,
                    "covered_tiers": sorted(p.interval_months for p in applicable),
                    "window_start": window_start.isoformat(),
                    "window_end": window_end.isoformat(),
                    "status": existing.status if existing else None,
                    "maintenance_id": existing.id if existing else None,
                }
            )

    out.sort(key=lambda d: d["window_start"])
    return out


# ------------------------------------------------------- tamamlama / kilit
def complete_item(
    db: Session,
    item: models.MaintenanceItem,
    user: models.User,
    completed_by: models.User | None = None,
) -> None:
    """En alt seviye maddeyi işaretler; üst düğümleri otomatik tamamlar.

    Paylaşılan tablet senaryosunda (tek oturum, çoklu usta) `completed_by`
    verilirse tamamlama bu kullanıcıya atfedilir; verilmezse oturum sahibi
    (`user`) kullanılır. Madde tamamlanınca kilitlendiği için bu atıf
    sonradan değiştirilemez."""
    if item.is_locked:
        raise ValueError("Bu madde kilitli, değiştirilemez")
    if _has_children(db, item):
        raise ValueError("Sadece en alt seviye maddeler işaretlenebilir")

    item.is_completed = True
    item.is_locked = True
    item.completed_by_id = (completed_by or user).id
    item.completed_at = datetime.now()
    _adjust_stock_for_node(db, item.source_node_id, -1)

    m = item.maintenance
    if m.status == "pending":
        m.status = "in_progress"
        m.started_at = m.started_at or datetime.now()

    _cascade_up(db, item)
    _check_maintenance_done(db, m)
    db.commit()


def _adjust_stock_for_node(db: Session, source_node_id: int | None, sign: int) -> None:
    """Bir bakım ağacı maddesine (TreeNode) tanımlı parça-adet eşleşmelerine
    (TreeNodePartUsage) göre bağlı parçaların stoğunu günceller: sign=-1
    madde tamamlanınca düşer, sign=+1 madde kilidi açılıp sıfırlanınca geri
    ekler. source_node_id yoksa (ör. kademeli bakımlardaki sarmalayıcı grup
    düğümleri, ya da şablonda hiç parça eşleşmesi tanımlanmamışsa) hiçbir
    şey yapmaz. Stok eksiye düşebilir (kasıtlı — sayım/kayıt tutarsızlığını
    gizlemek yerine görünür kılar); düşük stok kontrolü buna göre yapılır."""
    if not source_node_id:
        return
    usages = (
        db.query(models.TreeNodePartUsage)
        .filter_by(tree_node_id=source_node_id)
        .all()
    )
    for u in usages:
        part = db.get(models.PartCatalogItem, u.part_id)
        if part:
            part.stock_qty += sign * u.qty


def add_maintenance_note(
    db: Session,
    maintenance: models.Maintenance,
    text: str,
    author: models.User | None,
) -> models.MaintenanceNote:
    """Usta ekranından, checklist dışında bulunan ek arıza/bakım notu ekler.
    Paylaşılan tablet senaryosunda yazar oturum sahibinden farklı olabilir."""
    note = models.MaintenanceNote(
        maintenance_id=maintenance.id,
        author_id=author.id if author else None,
        text=text.strip(),
    )
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


def compute_covered_tiers(m: "models.Maintenance") -> list[int]:
    """Bir iş emrinin (kademeli/kümülatif kopyalamadan miras kalan
    MaintenanceItem.tiers alanlarından) kapsadığı TÜM periyotları (km
    planlarında interval_km, süre planlarında interval_months) döner,
    artan sıralı. Tek-planlı (kademeli olmayan) bir bakımda hiçbir
    maddede `tiers` set edilmediği için boş liste döner — çağıran taraf
    bunu "yalnızca kendi planı" olarak yorumlamalı (bkz. _copy_tree_
    snapshot'ın tek-plan `copy()` dalı vs. çok-plan `ensure()` dalı)."""
    covered: set[int] = set()
    for it in m.items:
        if it.tiers:
            for part in it.tiers.split(","):
                if part.strip().isdigit():
                    covered.add(int(part))
    return sorted(covered)


def compute_km_deviation_pct(
    completed_km: int | None,
    target_km: int | None,
    interval_km: int | None,
    plan_type: str | None,
) -> float | None:
    """Bakımın GERÇEK yapıldığı km'nin (usta/teknisyenin bakım sırasında
    girdiği completed_km — bkz. models.Maintenance.completed_km), hedef
    km'den (target_km) sapmasını, planın periyoduna (interval_km) ORANLI
    yüzde olarak hesaplar. Negatif: hedeften ÖNCE (erken) yapılmış; pozitif:
    hedeften SONRA (geç) yapılmış.

    Neden interval_km (target_km değil): sistemin açılma/tolerans penceresi
    de (-%5/+%10 vb., bkz. plan_window) periyoda ORANLI tanımlıdır — aynı
    birimde olduğu için bu sapma yüzdesi doğrudan o pencereyle karşılaştırı-
    labilir (ör. "-%0,41 sapma, -%5/+%10 penceresinin çok içinde").

    Yalnızca km bazlı planlarda VE completed_km girilmişse bir değer döner;
    aksi halde (süre bazlı plan, henüz girilmemiş, ya da interval_km=0/None)
    None döner — çağıran taraf (schemas.py MaintenanceOut, excel_utils/
    export_utils dışa aktarımları) bunu "—" ya da boş olarak göstermeli.
    Pydantic modelleri (PlanOut) İLE SQLAlchemy ORM (models.MaintenancePlan)
    nesneleri ARASINDA ortak kullanılabilsin diye kasıtlı olarak ilkel
    (primitive) parametreler alır, bir ORM/şema nesnesi DEĞİL."""
    if completed_km is None or target_km is None or plan_type != "km" or not interval_km:
        return None
    return round((completed_km - target_km) / interval_km * 100, 2)


def _auto_capture_completed_km(m: models.Maintenance) -> None:
    """Kullanıcı isteği: "şuan tamamlananlar için de tamamlandığı km
    üzerinden yine hesapla ve yaz" — bir iş emri checklist (tüm maddeler
    tiklenir) veya "Doğrudan Tamamlandı Yap" (force-complete) ile
    tamamlanınca, usta/teknisyen PATCH completed-km ile bakım sırasında
    ELLE bir değer GİRMEDİYSE (m.completed_km hâlâ None — girilmişse asla
    ezilmez, tıpkı completed_by_id gibi "gerçek bir teknisyen girişi"
    kuralına uyar), aracın o an takip edilen km'si otomatik olarak
    "tamamlandığı km" sayılır. Böylece Bakım Geçmişi/Excel'deki "Sapma %"
    sütunu yalnızca elle km girilen iş emirlerinde değil, TÜM tamamlanan km
    bazlı iş emirlerinde dolu görünür. Yalnızca km bazlı planlarda anlamlı
    (süre bazlı planlarda dokunulmaz)."""
    if m.completed_km is not None:
        return
    if m.plan and m.plan.plan_type == "km" and m.vehicle:
        m.completed_km = m.vehicle.current_km


def backfill_completed_km_for_finished_maintenances(db: Session) -> int:
    """Açılışta BİR KEZ çalışan, idempotent geriye dönük doldurma: bu
    özellik eklenmeden ÖNCE tamamlanmış km bazlı iş emirlerinde
    completed_km hâlâ boştur (yalnızca completed_km IS NULL satırlar
    işlenir — zaten dolu olanlara asla dokunulmaz).

    Tamamlandığı andaki gerçek km için en iyi bilgi kaynağı sırasıyla:
      1) audit_logs'ta o aracın tamamlanma anına (completed_at) kadarki EN
         SON "KM güncellendi" kaydı (PUT /vehicles/{id}/km veya PATCH
         completed-km ile elle yapılmış güncellemeler burada iz bırakır;
         günlük toplu KM simülasyonu araç bazında ayrı log TUTMADIĞI için
         bu kayıt sıkça bulunamayabilir — o durumda 2'ye düşülür).
      2) İş emrinin açılış km'si (km_at_open) — kesin olmasa da elimizdeki
         en yakın bilinen değerdir."""
    q = (
        db.query(models.Maintenance)
        .join(models.MaintenancePlan, models.Maintenance.plan_id == models.MaintenancePlan.id)
        .filter(
            models.Maintenance.status == "completed",
            models.Maintenance.completed_km.is_(None),
            models.MaintenancePlan.plan_type == "km",
        )
    )
    updated = 0
    for m in q.all():
        km = None
        if m.completed_at is not None:
            row = (
                db.query(models.AuditLog)
                .filter(
                    models.AuditLog.entity_type == "vehicle",
                    models.AuditLog.entity_id == m.vehicle_id,
                    models.AuditLog.action == "update",
                    models.AuditLog.new_value != "",
                    models.AuditLog.created_at <= m.completed_at,
                )
                .order_by(models.AuditLog.created_at.desc())
                .first()
            )
            if row:
                try:
                    km = int(row.new_value)
                except (TypeError, ValueError):
                    km = None
        if km is None:
            km = m.km_at_open
        if km is None:
            continue
        m.completed_km = km
        updated += 1
    if updated:
        db.commit()
    return updated


def _has_children(db: Session, item: models.MaintenanceItem) -> bool:
    return (
        db.query(models.MaintenanceItem).filter_by(parent_id=item.id).first()
        is not None
    )


def _cascade_up(db: Session, item: models.MaintenanceItem) -> None:
    """Alt maddelerin tamamı bittiyse üst düğümü tamamlar ve kilitler."""
    parent_id = item.parent_id
    while parent_id is not None:
        parent = db.get(models.MaintenanceItem, parent_id)
        siblings = db.query(models.MaintenanceItem).filter_by(parent_id=parent.id).all()
        if all(s.is_completed for s in siblings):
            parent.is_completed = True
            parent.is_locked = True
            parent.completed_at = datetime.now()
            parent_id = parent.parent_id
        else:
            break


def _uncascade_up(db: Session, item: models.MaintenanceItem) -> None:
    """`_cascade_up`'ın tersi: bir madde (zorunlu değilken otomatik tiklenmiş
    olup sonradan tekrar zorunlu yapıldığı için) tamamlanmamış hale
    getirildiğinde, bu yüzden yanlışlıkla 'tamamlanmış/kilitli' görünen üst
    düğümleri de geri açar. Kardeş maddelerin GERÇEK tamamlanma durumuna
    dokunmaz — yalnızca 'tüm çocuklar tamam' koşulu artık sağlanmadığı için
    üst düğümün durumunu düzeltir; bir üst düğüm zaten tamamlanmamışsa
    (ki bu durumda daha yukarısı da tamamlanmamış olmalı) hemen durur."""
    parent_id = item.parent_id
    while parent_id is not None:
        parent = db.get(models.MaintenanceItem, parent_id)
        if not parent.is_completed:
            break
        parent.is_completed = False
        parent.is_locked = False
        parent.completed_at = None
        parent_id = parent.parent_id


def resync_open_items_mandatory_state(
    db: Session, node_id: int, vehicle_id: int | None = None
) -> int:
    """Bir bakım ağacı maddesinin zorunlu/opsiyonel ayarı (genel varsayılan
    ya da belirli bir araç istisnası) DEĞİŞTİRİLDİĞİNDE, bu maddeye karşılık
    gelen ve HÂLÂ AÇIK (tamamlanmamış iş emri) olan MaintenanceItem
    kayıtlarını da hemen yeni duruma göre günceller — usta, yeni bir iş
    emri açılmasını beklemeden, üzerinde çalıştığı bakımda da değişikliği
    anında görür (bkz. kullanıcı isteği: "zorunlu değil yapılınca direkt
    olarak tikli olacak, açık olan bakımlarda da").

    Kurallar:
      * Yalnızca en alt seviye (çocuğu olmayan) maddeler etkilenir — üst
        düğümlerin tamamlanma durumu her zaman alt maddelerden türetilir
        (bkz. _cascade_up/_uncascade_up), doğrudan set edilmez.
      * Bir teknisyen tarafından GERÇEKTEN tamamlanmış (completed_by_id
        dolu) maddelere ASLA dokunulmaz — yalnızca otomatik (opsiyonel
        olduğu için önceden tiklenmiş, completed_by_id boş) ya da hâlâ
        işaretlenmemiş maddeler güncellenir.
      * Tamamlanmış (status='completed') iş emirleri hiç taranmaz — artık
        kalıcı bir kayıttır, geriye dönük değiştirilmez.
      * Stok (TreeNodePartUsage) bu otomatik geçişlerde DOKUNULMAZ — ilk
        açılıştaki otomatik tikleme de stoğu etkilemiyordu (bkz.
        _copy_tree_snapshot), simetri korunur.

    vehicle_id verilirse yalnızca o aracın açık iş emirleri taranır (araç
    istisnası eklenirken/kaldırılırken kullanılır); verilmezse genel
    varsayılan değiştiğinde bu maddeye sahip TÜM açık iş emirleri taranır
    — her biri için o aracın KENDİ istisnası (varsa) yine önceliklidir,
    bu da _node_is_mandatory_for_vehicle üzerinden otomatik doğru hesaplanır.

    Dönüş: değiştirilen madde sayısı (bilgi amaçlı)."""
    node = db.get(models.TreeNode, node_id)
    if not node:
        return 0

    q = (
        db.query(models.MaintenanceItem)
        .join(models.Maintenance, models.MaintenanceItem.maintenance_id == models.Maintenance.id)
        .filter(
            models.MaintenanceItem.source_node_id == node_id,
            models.Maintenance.status != "completed",
        )
    )
    if vehicle_id is not None:
        q = q.filter(models.Maintenance.vehicle_id == vehicle_id)
    items = q.all()

    changed = 0
    exceptions_cache: dict[int, dict[int, bool]] = {}
    for item in items:
        if _has_children(db, item):
            continue  # yalnızca yaprak (checklist) maddeleri etkilenir

        m = item.maintenance
        exceptions = exceptions_cache.get(m.vehicle_id)
        if exceptions is None:
            exceptions = _load_vehicle_mandatory_exceptions(db, m.vehicle_id)
            exceptions_cache[m.vehicle_id] = exceptions
        desired_completed = not _node_is_mandatory_for_vehicle(node, exceptions)

        if item.is_completed == desired_completed:
            continue
        if item.completed_by_id is not None:
            continue  # gerçek bir teknisyen tamamlaması — asla ezilmez

        if desired_completed:
            item.is_completed = True
            _cascade_up(db, item)
            _check_maintenance_done(db, m)
        else:
            item.is_completed = False
            _uncascade_up(db, item)
        changed += 1

    if changed:
        db.commit()
    return changed


def _check_maintenance_done(db: Session, m: models.Maintenance) -> None:
    roots = (
        db.query(models.MaintenanceItem)
        .filter_by(maintenance_id=m.id, parent_id=None)
        .all()
    )
    if roots and all(r.is_completed for r in roots):
        m.status = "completed"
        m.completed_at = datetime.now()
        _auto_capture_completed_km(m)


def unlock_item(db: Session, item: models.MaintenanceItem) -> None:
    """Admin kilidi kaldırır: madde + alt ağacı sıfırlanır, üst düğümler açılır."""
    _reset_subtree(db, item)
    # Üst düğümleri geri aç
    parent_id = item.parent_id
    while parent_id is not None:
        parent = db.get(models.MaintenanceItem, parent_id)
        parent.is_completed = False
        parent.is_locked = False
        parent.completed_at = None
        parent.completed_by_id = None
        parent_id = parent.parent_id
    m = item.maintenance
    if m.status == "completed":
        m.status = "in_progress"
        m.completed_at = None
    db.commit()


def _reset_subtree(db: Session, item: models.MaintenanceItem) -> None:
    if item.is_completed:
        # Bu madde tamamlanmışken stoktan düşülmüştü (complete_item /
        # force_complete_maintenance) — kilidi açılıp sıfırlanınca aynı
        # miktar geri eklenir.
        _adjust_stock_for_node(db, item.source_node_id, +1)
    item.is_completed = False
    item.is_locked = False
    item.completed_at = None
    item.completed_by_id = None
    for child in db.query(models.MaintenanceItem).filter_by(parent_id=item.id).all():
        _reset_subtree(db, child)


def force_complete_maintenance(
    db: Session, m: models.Maintenance, actor: models.User
) -> None:
    """Admin kısayolu: tek tek madde işaretlemeye gerek kalmadan tüm iş
    emrini doğrudan tamamlandı yapar. Ağaçtaki her maddeyi (dal + yaprak)
    tamamlanmış/kilitli işaretler ve tamamlayan kişi olarak admin'i yazar."""
    items = db.query(models.MaintenanceItem).filter_by(maintenance_id=m.id).all()
    now = datetime.now()
    for it in items:
        if not it.is_completed:
            it.is_completed = True
            it.completed_at = now
            it.completed_by_id = actor.id
            _adjust_stock_for_node(db, it.source_node_id, -1)
        it.is_locked = True
    m.status = "completed"
    m.started_at = m.started_at or now
    m.completed_at = now
    _auto_capture_completed_km(m)
    db.commit()


def reopen_maintenance(db: Session, m: models.Maintenance) -> None:
    """Admin kısayolu: yanlışlıkla ya da erken tamamlandı işaretlenmiş bir
    iş emrini 'Tamamlandı' durumundan çıkarır. Madde işaretleri/kilitleri
    olduğu gibi kalır — gerekirse tek tek 'Kilidi Aç' ile düzeltilebilir;
    bu yalnızca iş emrinin genel durumunu geri alır."""
    m.status = "in_progress"
    m.completed_at = None
    db.commit()


# ---------------------------------------------------- Günlük KM simülasyonu
# Araçlar sahada günde ortalama ~300-330 km ilerler. Bu değerler her gün her
# aktif araca rastgele eklenir; km güncellemesi otomatik bakım kontrolünü
# tetikler (aralığa giren araçlarda bakım kendiliğinden açılır).
_KM_SIM_DEFAULTS = {
    "km_sim_enabled": "1",
    "km_sim_min": "300",
    "km_sim_max": "330",
    "km_sim_last_date": "",
}
_KM_SIM_MAX_CATCHUP_DAYS = 60  # açılışta telafi edilecek azami gün (aşırı sıçramayı önler)


def get_setting(db: Session, key: str, default: str | None = None) -> str | None:
    row = db.get(models.AppSetting, key)
    return row.value if row is not None else default


def set_setting(db: Session, key: str, value) -> None:
    row = db.get(models.AppSetting, key)
    if row is not None:
        row.value = str(value)
    else:
        db.add(models.AppSetting(key=key, value=str(value)))
    db.flush()


# --------------------------------------------------------------- Vardiyalar
# Vardiyalı çalışma modu KAPALIYKEN madde tamamlama/not ekleme her zaman o an
# oturum açmış kullanıcıya otomatik atanır (seçici gösterilmez) — paylaşılan
# tabletlerde tek kişi çalışıyorsa isim seçmeye gerek yoktur. AÇIKKEN ise
# eskisi gibi bir seçici gösterilir, ancak yalnızca seçili vardiyadaki
# kişiler arasından (bkz. main.py /users/assignable ?shift_id=).
DEFAULT_SHIFT_NAMES = ["Vardiya 1", "Vardiya 2", "Vardiya 3"]


def ensure_default_shifts(db: Session) -> None:
    """Sunucu her açıldığında çağrılır; hiç vardiya yoksa 3 varsayılan
    vardiyayı oluşturur. Var olanlara dokunmaz (admin isim değiştirmiş
    olabilir)."""
    if db.query(models.Shift).first() is not None:
        return
    for i, name in enumerate(DEFAULT_SHIFT_NAMES):
        db.add(models.Shift(name=name, order_index=i))
    db.commit()


def get_shift_mode(db: Session) -> bool:
    return (get_setting(db, "shift_mode_enabled", "0") or "0") == "1"


def set_shift_mode(db: Session, enabled: bool) -> None:
    set_setting(db, "shift_mode_enabled", "1" if enabled else "0")
    db.commit()


def get_km_sim_config(db: Session) -> dict:
    def _int(key: str) -> int:
        try:
            return int(get_setting(db, key, _KM_SIM_DEFAULTS[key]) or 0)
        except (TypeError, ValueError):
            return int(_KM_SIM_DEFAULTS[key])

    return {
        "enabled": (get_setting(db, "km_sim_enabled", "1") or "1") == "1",
        "min_km": _int("km_sim_min"),
        "max_km": _int("km_sim_max"),
        "last_date": get_setting(db, "km_sim_last_date", "") or None,
    }


def simulate_km_advance(
    db: Session, days: int = 1, actor: "models.User | None" = None, note: str = "manuel"
) -> dict:
    """Her aktif aracın kilometresini `days` gün boyunca, günde rastgele
    (min_km..max_km) artırır ve her gün için otomatik bakım kontrolünü
    çalıştırır. Dönüş: özet sözlüğü."""
    cfg = get_km_sim_config(db)
    lo, hi = cfg["min_km"], cfg["max_km"]
    if hi < lo:
        lo, hi = hi, lo
    lo = max(0, lo)
    hi = max(lo, hi)

    vehicles = db.query(models.Vehicle).all()
    total_added = 0
    opened_total = 0
    now = datetime.now()

    for _ in range(max(1, days)):
        for v in vehicles:
            add = random.randint(lo, hi)
            v.current_km += add
            v.last_km_update_at = now
            total_added += add
        db.flush()
        for v in vehicles:
            opened = check_and_open_maintenances(db, v)  # gerekirse commit eder
            for m in opened:
                log_action(
                    db, actor, "create", "maintenance", m.id,
                    f"Otomatik bakım açıldı (KM simülasyonu): {v.code} — {m.plan.name}",
                    detail=f"Hedef {m.target_km} km",
                    commit=False,
                )
            opened_total += len(opened)

    log_action(
        db, actor, "update", "vehicle", None,
        f"KM simülasyonu: {len(vehicles)} araç {max(1, days)} gün ilerletildi "
        f"(+{total_added} km toplam)",
        detail=f"{opened_total} bakım açıldı · {note}",
        commit=False,
    )
    db.commit()
    return {
        "days": max(1, days),
        "vehicles_updated": len(vehicles),
        "total_km_added": total_added,
        "maintenances_opened": opened_total,
    }


def run_km_sim_catchup(db: Session) -> int:
    """Takvim gününe göre kaçırılan günleri telafi eder. Sunucu her açıldığında
    ve arka plan döngüsünde çağrılır. Devre dışıysa yalnızca tarihi bugüne
    çeker (duraklatma = dondurma; tekrar açınca geçmişe dönük patlama olmaz).
    Dönüş: ilerletilen gün sayısı."""
    cfg = get_km_sim_config(db)
    today = date.today()

    if not cfg["last_date"]:
        set_setting(db, "km_sim_last_date", today.isoformat())
        db.commit()
        return 0

    if not cfg["enabled"]:
        set_setting(db, "km_sim_last_date", today.isoformat())
        db.commit()
        return 0

    try:
        last_d = date.fromisoformat(cfg["last_date"])
    except ValueError:
        set_setting(db, "km_sim_last_date", today.isoformat())
        db.commit()
        return 0

    days = (today - last_d).days
    if days <= 0:
        return 0
    days = min(days, _KM_SIM_MAX_CATCHUP_DAYS)

    simulate_km_advance(db, days, actor=None, note="günlük otomatik telafi")
    set_setting(db, "km_sim_last_date", today.isoformat())
    db.commit()
    return days


def run_daily_time_maintenance_check(db: Session) -> int:
    """Süre bazlı (aylık/yıllık) bakımlar için günlük kontrol — KM
    simülasyonundaki gün-değişikliği tespiti ile AYNI desen (AppSetting'de
    son çalıştığı gün saklanır), ama "kaçırılan günü telafi etme" kavramı
    yok — süre motoru zaten HER ÇALIŞTIĞINDA aracın o günkü gerçek "geçen ay"
    sayısını hesaplıyor (km_at_open gibi ara adım biriktirmiyor), o yüzden
    burada sadece "bugün için kontrol edildi mi?" işaretlenir. Sunucu birkaç
    gün kapalı kalsa bile bir sonraki açılışta kaçırılan hiçbir bakım
    kaybolmaz — sadece o güne kadar geçen ay sayısı üzerinden normal şekilde
    değerlendirilir.

    Dönüş: bugün için kontrol yapıldıysa (henüz yapılmamışsa) açılan iş emri
    sayısı; bugün için zaten kontrol edilmişse -1."""
    today = date.today()
    last = get_setting(db, "time_maintenance_last_date", "")
    if last == today.isoformat():
        return -1
    opened = check_and_open_time_maintenances_all_vehicles(db)
    set_setting(db, "time_maintenance_last_date", today.isoformat())
    db.commit()
    return len(opened)


# ------------------------------------------------------------ Yetki Yönetimi
# Yetki matrisinde yönetilen roller. Admin bilerek dışarıda bırakılır:
# admin her zaman tüm izinlere sahiptir, buradan kısıtlanamaz (kilitlenmeyi
# önlemek için).
PERMISSION_ROLES = ["engineer", "technician", "amir"]

# İzin anahtarı -> Türkçe etiket (frontend'de de kullanılabilir ama admin
# panelinde sıralamayı ve varsayılanları tek yerde tutmak için burada).
PERMISSION_KEYS = [
    "view_maintenance",
    "edit_maintenance",
    "delete_maintenance",
    "approve_maintenance",
    "export_excel",
    "manage_users",
    # Faz 26: otomatik açılan aday bakımları "Başlat" diyerek devreye alma
    # yetkisi — teknisyene verilmez (o yalnızca zaten başlatılmış bir iş
    # emrinde çalışır), varsayılan olarak mühendis/amir'e açıktır.
    "start_maintenance",
    # Faz 27: sistemdeki TÜM bakım iş emirlerini (tamamlananlar/geçmiş dahil)
    # kalıcı olarak silme — normal delete_maintenance'tan (tek iş emri, geçmişi
    # olmayan) çok daha yıkıcı bir işlem olduğu için ayrı bir izin anahtarıdır.
    "delete_all_maintenance",
    # Faz 28: otomatik tespiti beklemeden araç+periyot+hedef km seçerek elle
    # bir bakım iş emri açma — start_maintenance ile aynı yetki seviyesindeki
    # roller (mühendis/amir) için varsayılan olarak açık, teknisyene kapalı.
    "create_maintenance",
    # Faz 29: Dashboard'daki "Analiz" bölümünün (periyoda göre bakım grafiği +
    # genel tamamlanma oranı) görünürlüğü — Yetki Yönetimi sayfasının, ileride
    # başka arayüz öğelerini de aynı desenle açıp kapatabileceğini gösteren
    # ilk "görünürlük" tipi izin. Mevcut davranışı bozmamak için üç rolde de
    # varsayılan olarak açıktır.
    "view_dashboard_analytics",
]

# Yetki Yönetimi sayfasında gruplama için: izin anahtarı -> kategori adı.
# Yeni bir izin eklenirse buraya da eklenmesi gerekir (eklenmezse sayfada
# "Diğer" kategorisi altında görünür, bkz. get_permission_defs).
PERMISSION_CATEGORIES: dict[str, str] = {
    "view_maintenance": "Bakım İşlemleri",
    "edit_maintenance": "Bakım İşlemleri",
    "delete_maintenance": "Bakım İşlemleri",
    "approve_maintenance": "Bakım İşlemleri",
    "start_maintenance": "Bakım İşlemleri",
    "create_maintenance": "Bakım İşlemleri",
    "delete_all_maintenance": "Bakım İşlemleri",
    "export_excel": "Veri ve Dışa Aktarma",
    "manage_users": "Kullanıcı Yönetimi",
    "view_dashboard_analytics": "Dashboard Görünürlüğü",
}

# İzin anahtarı -> kısa açıklama (Yetki Yönetimi sayfasında etiketin altında
# gösterilir — hangi rolün tam olarak neyi yapabildiğini netleştirir).
PERMISSION_DESCRIPTIONS: dict[str, str] = {
    "view_maintenance": "Bakım iş emirlerini ve checklist'lerini görüntüleyebilir.",
    "edit_maintenance": "Checklist maddelerini işaretleyip yürütme notu ekleyebilir.",
    "delete_maintenance": "Tek bir bakım iş emrini kalıcı olarak silebilir.",
    "approve_maintenance": "Kilitlenmiş bir maddenin kilidini açıp yeniden düzenlenebilir hâle getirebilir.",
    "start_maintenance": "Otomatik açılan aday iş emirlerini 'Başlat' diyerek devreye alabilir.",
    "create_maintenance": "Otomatik tespiti beklemeden araç + periyot seçerek elle bakım iş emri açabilir.",
    "delete_all_maintenance": "Tamamlanmış/geçmiş dahil TÜM bakım kayıtlarını kalıcı olarak silebilir (çok yıkıcı).",
    "export_excel": "Bakım listelerini Excel, PDF, CSV veya Word olarak dışa aktarabilir.",
    "manage_users": "Kullanıcı ekleyebilir, onaylayabilir, rolünü/durumunu değiştirebilir.",
    "view_dashboard_analytics": "Dashboard'daki Analiz bölümünü (periyoda göre grafik, tamamlanma oranı) görebilir.",
}


def get_default_role_permissions(role: str) -> dict[str, bool]:
    """Bir rolün fabrika varsayılanlarını döner (admin panelinden yapılmış
    özelleştirmeleri YOK sayar) — "varsayılana sıfırla" aksiyonu için."""
    return dict(_DEFAULT_ROLE_PERMISSIONS.get(role, {}))


def get_permission_defs() -> list[dict]:
    """Yetki Yönetimi sayfasında kategorilere göre gruplamak için, sabit
    PERMISSION_KEYS sırasına göre {key, category, description} listesi
    döner."""
    return [
        {
            "key": key,
            "category": PERMISSION_CATEGORIES.get(key, "Diğer"),
            "description": PERMISSION_DESCRIPTIONS.get(key, ""),
        }
        for key in PERMISSION_KEYS
    ]

# Mevcut (yetki matrisi eklenmeden önceki) davranışla birebir aynı olacak
# şekilde seçilmiş varsayılanlar. Sonradan admin panelinden değiştirilebilir.
_DEFAULT_ROLE_PERMISSIONS: dict[str, dict[str, bool]] = {
    "engineer": {
        "view_maintenance": True,
        "edit_maintenance": True,
        "delete_maintenance": False,
        # Mühendis, ustayı denetleyen teknik personel olduğu için varsayılan
        # olarak kilit açma/onaylama yetkisine sahiptir (usta bunu yapamaz).
        "approve_maintenance": True,
        "export_excel": True,
        "manage_users": False,
        "start_maintenance": True,
        "delete_all_maintenance": True,
        "create_maintenance": True,
        "view_dashboard_analytics": True,
    },
    "technician": {
        "view_maintenance": True,
        "edit_maintenance": True,
        "delete_maintenance": False,
        "approve_maintenance": False,
        "export_excel": True,
        "manage_users": False,
        "start_maintenance": False,
        "delete_all_maintenance": False,
        "create_maintenance": False,
        "view_dashboard_analytics": True,
    },
    # Amir (saha amiri/denetçi): mühendisle aynı denetim seviyesinde —
    # bakımları görüntüler, gerekirse düzenler, kilit açıp onaylayabilir ve
    # Excel'e aktarabilir. Kullanıcı yönetimi varsayılan olarak kapalıdır,
    # admin panelinden açılabilir.
    "amir": {
        "view_maintenance": True,
        "edit_maintenance": True,
        "delete_maintenance": False,
        "approve_maintenance": True,
        "export_excel": True,
        "manage_users": False,
        "start_maintenance": True,
        "delete_all_maintenance": False,
        "create_maintenance": True,
        "view_dashboard_analytics": True,
    },
}


def ensure_default_role_permissions(db: Session) -> None:
    """Eksik olan (role, permission) satırlarını varsayılan değerle oluşturur.
    Var olan satırlara dokunmaz — admin panelinden yapılmış değişiklikler
    korunur. Sunucu her açılışında çağrılır (yeni bir izin anahtarı eklenirse
    otomatik tamamlanır)."""
    existing = {
        (row.role, row.permission)
        for row in db.query(models.RolePermission.role, models.RolePermission.permission).all()
    }
    changed = False
    for role in PERMISSION_ROLES:
        for key in PERMISSION_KEYS:
            if (role, key) not in existing:
                default = _DEFAULT_ROLE_PERMISSIONS.get(role, {}).get(key, False)
                db.add(models.RolePermission(role=role, permission=key, allowed=default))
                changed = True
    if changed:
        db.commit()


def get_permission_matrix(db: Session) -> dict[str, dict[str, bool]]:
    """Mühendis/usta için tam izin matrisini döner: {rol: {izin: bool}}."""
    matrix: dict[str, dict[str, bool]] = {
        role: dict(_DEFAULT_ROLE_PERMISSIONS.get(role, {})) for role in PERMISSION_ROLES
    }
    rows = db.query(models.RolePermission).filter(
        models.RolePermission.role.in_(PERMISSION_ROLES)
    ).all()
    for row in rows:
        matrix.setdefault(row.role, {})[row.permission] = row.allowed
    return matrix


def get_role_permissions(db: Session, role: str) -> dict[str, bool]:
    if role == "admin":
        return {key: True for key in PERMISSION_KEYS}
    perms = dict(_DEFAULT_ROLE_PERMISSIONS.get(role, {key: False for key in PERMISSION_KEYS}))
    rows = db.query(models.RolePermission).filter_by(role=role).all()
    for row in rows:
        perms[row.permission] = row.allowed
    return perms


def has_permission(db: Session, user: "models.User", key: str) -> bool:
    """Admin her zaman True. Diğer roller için DB'deki (yoksa varsayılan)
    değere bakar."""
    if user.role == "admin":
        return True
    return get_role_permissions(db, user.role).get(key, False)


# ---------------------------------------------------------------- İyileştirme Talepleri — tek aşamalı onay
# Faz 30'daki iki aşamalı (amir -> mühendis) hiyerarşik zincir KALDIRILDI:
# öneriyi yetkili rollerden (mühendis / amir / admin) HERHANGİ BİRİ doğrudan
# onaylar ya da reddeder ve bu karar nihaidir. Eski kayıtların amir_*/
# engineer_* alanları geçmiş görünürlüğü için yerinde duruyor ama yeni
# kararlarda kullanılmıyor.

# Talebi değerlendirebilecek roller — bkz. main.review_suggestion.
SUGGESTION_REVIEWER_ROLES = ("admin", "engineer", "amir")


def can_review_suggestion(user: "models.User") -> bool:
    return user.role in SUGGESTION_REVIEWER_ROLES


def suggestion_is_open(s: "models.Suggestion") -> bool:
    """Talep hâlâ karar bekliyor mu? Zincir kalktığı için tek bir bekleme
    durumu var; 'pending_engineer' yalnızca eski kayıtlarda görülebilir ve
    o da normal bir bekleyen öneri gibi ele alınır (bkz. migrate.py — yeni
    kayıtlarda hiç üretilmez)."""
    return s.status in ("pending", "pending_engineer")


def apply_suggestion_review(
    db: Session,
    s: "models.Suggestion",
    actor: "models.User",
    decision: str,
    note: str,
) -> None:
    """Talebi tek kararda sonuçlandırır (approved | rejected). Yetki
    kontrolü çağıran tarafta (main.py) yapılır ve burada TEKRARLANMAZ."""
    now = datetime.now()
    note = (note or "").strip()
    s.status = decision
    s.reviewed_by_id = actor.id
    s.reviewed_at = now
    s.admin_note = note
    # Değerlendiren rolün aşama alanı da doldurulur ki eski kayıtlarla aynı
    # ekranda tutarlı görünsün ("kim karar verdi" bilgisi kaybolmasın).
    if actor.role == "amir":
        s.amir_status = decision
        s.amir_reviewed_by_id = actor.id
        s.amir_reviewed_at = now
        s.amir_note = note
    else:
        s.engineer_status = decision
        s.engineer_reviewed_by_id = actor.id
        s.engineer_reviewed_at = now
        s.engineer_note = note
    db.commit()


def delete_user_permanently(db: Session, target: "models.User") -> None:
    """Kullanıcıyı veritabanından kalıcı olarak siler (pasife almaktan farklı
    olarak hesap tamamen kaldırılır).

    Geçmiş kayıtların (tamamlanan bakım maddeleri, notlar, işlem kayıtları,
    öneriler, alarm atamaları) okunabilirliği bozulmasın diye önce bu
    kullanıcıya olan referanslar NULL'a çekilir — bu tablolardaki metinler
    (ör. denetim kaydı özeti, madde adı) zaten o anki tam adla ayrıca
    saklandığı için bilgi kaybı olmaz, yalnızca "kim yaptı" bağlantısı
    kalkar. Yalnızca oturum açma ile ilgili geçici kayıtlar (e-posta
    doğrulama kodu, yenileme jetonu) doğrudan silinir."""
    uid = target.id

    db.query(models.VerificationCode).filter_by(user_id=uid).delete(synchronize_session=False)
    db.query(models.RefreshToken).filter_by(user_id=uid).delete(synchronize_session=False)

    db.query(models.Maintenance).filter_by(assigned_to_id=uid).update(
        {"assigned_to_id": None}, synchronize_session=False
    )
    db.query(models.Maintenance).filter_by(activated_by_id=uid).update(
        {"activated_by_id": None}, synchronize_session=False
    )
    db.query(models.MaintenanceItem).filter_by(completed_by_id=uid).update(
        {"completed_by_id": None}, synchronize_session=False
    )
    db.query(models.MaintenanceItem).filter_by(exec_note_by_id=uid).update(
        {"exec_note_by_id": None}, synchronize_session=False
    )
    db.query(models.MaintenanceNote).filter_by(author_id=uid).update(
        {"author_id": None}, synchronize_session=False
    )
    db.query(models.AuditLog).filter_by(actor_id=uid).update(
        {"actor_id": None}, synchronize_session=False
    )
    db.query(models.Suggestion).filter_by(created_by_id=uid).update(
        {"created_by_id": None}, synchronize_session=False
    )
    db.query(models.Suggestion).filter_by(reviewed_by_id=uid).update(
        {"reviewed_by_id": None}, synchronize_session=False
    )
    # Faz 30 — hiyerarşik onay: amir/mühendis aşama alanları da aynı şekilde
    # temizlenmeli (aksi halde silinen bir amir/mühendisin referansı geride
    # kalırdı — PostgreSQL'de SQLite'daki gibi ON DELETE pragma'sı yok, bu
    # yüzden elle temizlemek şart).
    db.query(models.Suggestion).filter_by(amir_reviewed_by_id=uid).update(
        {"amir_reviewed_by_id": None}, synchronize_session=False
    )
    db.query(models.Suggestion).filter_by(engineer_reviewed_by_id=uid).update(
        {"engineer_reviewed_by_id": None}, synchronize_session=False
    )
    # Bu kullanıcıyı vardiya sorumlusu/üyesi olarak gösteren başka
    # kullanıcı kaydı yok (shift_id kendi satırında) — ayrı bir işlem
    # gerekmez, satır silinince kendiliğinden kalkar.

    db.delete(target)
    db.commit()


def set_permission(db: Session, role: str, permission: str, allowed: bool, actor=None) -> None:
    if role not in PERMISSION_ROLES:
        raise ValueError("Geçersiz rol")
    if permission not in PERMISSION_KEYS:
        raise ValueError("Geçersiz izin anahtarı")
    row = (
        db.query(models.RolePermission)
        .filter_by(role=role, permission=permission)
        .first()
    )
    old_allowed = row.allowed if row is not None else _DEFAULT_ROLE_PERMISSIONS.get(role, {}).get(permission, False)
    if row is None:
        row = models.RolePermission(role=role, permission=permission, allowed=allowed)
        db.add(row)
    else:
        row.allowed = allowed
    db.commit()
    log_action(
        db, actor, "update", "permission", None,
        f"Yetki güncellendi: {role} — {permission} = {'açık' if allowed else 'kapalı'}",
        old_value="açık" if old_allowed else "kapalı",
        new_value="açık" if allowed else "kapalı",
    )


# --------------------------------------------------- E-posta doğrulama kodu
# Kayıt/profil e-posta doğrulaması ve şifremi unuttum akışlarının ortak
# altyapısı. Kod düz metin saklanmaz; çağıran taraf (main.py) auth.py'deki
# hash/verify fonksiyonlarını parametre olarak geçer (services.py, döngüsel
# import oluşmasın diye auth.py'yi doğrudan import etmez).
CODE_TTL_MINUTES = 15
CODE_MAX_ATTEMPTS = 5
CODE_RESEND_COOLDOWN_SECONDS = 45


def generate_verification_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def validate_password_strength(password: str) -> str | None:
    """Parola politikası (Faz 23 — Güvenlik sertleştirme). En az 8 karakter,
    en az bir harf ve en az bir rakam ister — hatırlanması kolay ama boş/zayıf
    ('123456', '12345678' gibi) parolaları eler. Geçerliyse None, değilse
    kullanıcıya gösterilecek Türkçe hata mesajını döner."""
    if len(password) < 8:
        return "Şifre en az 8 karakter olmalı"
    if not any(c.isalpha() for c in password):
        return "Şifre en az bir harf içermeli"
    if not any(c.isdigit() for c in password):
        return "Şifre en az bir rakam içermeli"
    return None


def is_valid_email(value: str) -> bool:
    """Basit biçim kontrolü — tam RFC doğrulaması yerine, kullanıcı
    hatalarını (boş, @ yok, boşluk içeriyor vb.) yakalamak yeterlidir."""
    value = (value or "").strip()
    if len(value) < 5 or len(value) > 200 or " " in value:
        return False
    if value.count("@") != 1:
        return False
    local, _, domain = value.partition("@")
    return bool(local) and "." in domain and not domain.startswith(".") and not domain.endswith(".")


def latest_active_code(db: Session, user_id: int, purpose: str) -> "models.VerificationCode | None":
    return (
        db.query(models.VerificationCode)
        .filter_by(user_id=user_id, purpose=purpose, used_at=None)
        .order_by(models.VerificationCode.created_at.desc())
        .first()
    )


def create_verification_code(
    db: Session, user: "models.User", purpose: str, email: str, hash_fn
) -> str:
    """Yeni bir kod üretir, hash'lenmiş halini kaydeder ve DÜZ METİN kodu
    döner (yalnızca e-postayla gönderilmek üzere; DB'de tutulmaz). Aynı
    amaç için önceki kullanılmamış kodları geçersiz kılar (used_at=now)
    ki eski bir kod hâlâ denenebilir kalmasın."""
    now = datetime.now()
    old = (
        db.query(models.VerificationCode)
        .filter_by(user_id=user.id, purpose=purpose, used_at=None)
        .all()
    )
    for row in old:
        row.used_at = now
    code = generate_verification_code()
    row = models.VerificationCode(
        user_id=user.id,
        purpose=purpose,
        email=email,
        code_hash=hash_fn(code),
        expires_at=now + timedelta(minutes=CODE_TTL_MINUTES),
    )
    db.add(row)
    db.commit()
    return code


def verify_verification_code(
    db: Session, user: "models.User", purpose: str, code: str, verify_fn
) -> tuple[bool, str]:
    """Dönüş: (başarılı mı, hata mesajı). Başarılıysa kodu 'used' işaretler."""
    row = latest_active_code(db, user.id, purpose)
    if row is None:
        return False, "Aktif bir doğrulama kodu bulunamadı. Yeni kod isteyin."
    if row.expires_at < datetime.now():
        return False, "Kodun süresi doldu. Yeni kod isteyin."
    if row.attempts >= CODE_MAX_ATTEMPTS:
        return False, "Çok fazla hatalı deneme. Yeni kod isteyin."
    row.attempts += 1
    db.commit()
    if not verify_fn(code.strip(), row.code_hash):
        return False, "Kod hatalı."
    row.used_at = datetime.now()
    db.commit()
    return True, ""


# ================================================================ Faz 18 — KPI
def compute_kpi_report(
    db: Session, date_from: datetime | None, date_to: datetime | None
) -> dict:
    """KPI raporu (in-app sayfa + Excel dışa aktarma tarafından ortak
    kullanılır). date_from/date_to None ise ilgili yönde sınırsızdır (tüm
    zamanlar); ikisi de doluysa dahil aralık [date_from, date_to] uygulanır.

    Dört kategori:
      1) Tamamlanma oranı/süresi: created_at aralığa giren iş emirlerinin
         kaçının şu an tamamlanmış olduğu (oran) + completed_at aralığa giren
         iş emirlerinin ortalama tamamlanma süresi (gün, completed_at -
         created_at). Ayrıca ZAMANINDA BAKIM ORANI: yalnızca TAMAMLANMIŞ
         iş emirleri üzerinden, bakımın gerçekten yapıldığı km'nin
         (completed_km) hedeften sapmasının planın tolerans penceresinde
         kalıp kalmadığına göre (süre bazlı planlarda completed_at <=
         target_date) — ayrıntı için aşağıdaki "1b" bloğuna bakın.
      2) Geciken/kritik bakım sayıları: bunlar doğası gereği "şu anki durum"
         göstergesidir (geçmişte hangi gün kaç kritik iş emri vardı diye bir
         kayıt tutulmuyor) — bu yüzden tarih aralığından BAĞIMSIZ, her zaman
         GÜNCEL açık (Bekliyor/Devam Ediyor, is_activated) iş emirlerine göre
         hesaplanır. KM planlarında KRITIK_PCT_OF_INTERVAL/COK_KRITIK_PCT_
         OF_INTERVAL, süre planlarında URGENT_WITHIN_DAYS/CRITICAL_WITHIN_
         DAYS eşikleri (Dashboard'daki _due_item ile birebir aynı mantık)
         kullanılır.
      3) Araç/model bazlı performans: Vehicle.model'e göre gruplanmış ve
         araç bazında (ilk 'vehicle_limit' varsa kırpılır, yoksa TÜM
         araçlar) açılan/tamamlanan/şu an açık sayıları.
      4) Personel/vardiya performansı: MaintenanceItem.completed_by_id'ye
         göre tamamlanan madde sayısı + o kişiye atanmış (assigned_to_id)
         iş emirlerinin ortalama tamamlanma süresi; vardiya kırılımı
         kişinin GÜNCEL vardiyasına göredir (geçmişe dönük vardiya kaydı
         tutulmadığından tarihsel olarak %100 doğru olmayabilir)."""
    # ---- 1) Tamamlanma oranı ve süresi ----
    opened_q = db.query(models.Maintenance).filter(models.Maintenance.is_activated == True)  # noqa: E712
    if date_from is not None:
        opened_q = opened_q.filter(models.Maintenance.created_at >= date_from)
    if date_to is not None:
        opened_q = opened_q.filter(models.Maintenance.created_at <= date_to)
    opened = opened_q.all()
    opened_in_range = len(opened)
    completed_of_opened = sum(1 for m in opened if m.status == "completed")
    completion_rate_pct = (
        round(100 * completed_of_opened / opened_in_range, 1) if opened_in_range else 0.0
    )

    completed_q = db.query(models.Maintenance).filter(models.Maintenance.status == "completed")
    if date_from is not None:
        completed_q = completed_q.filter(models.Maintenance.completed_at >= date_from)
    if date_to is not None:
        completed_q = completed_q.filter(models.Maintenance.completed_at <= date_to)
    completed_in_range_rows = completed_q.all()
    durations_days = [
        (m.completed_at - m.created_at).total_seconds() / 86400
        for m in completed_in_range_rows
        if m.completed_at and m.created_at
    ]
    avg_completion_days = (
        round(sum(durations_days) / len(durations_days), 1) if durations_days else None
    )

    # ---- 1b) Zamanında tamamlanan bakım oranı ----
    # Yalnızca TAMAMLANMIŞ iş emirleri üzerinden hesaplanır (payda =
    # seçilen aralıkta tamamlananlar; açılıp bitmemiş olanlar sayılmaz).
    #
    # "Zamanında" tanımı (kullanıcının onayladığı, güncellenmiş kural):
    #   - Km bazlı planlarda: bakımın GERÇEKTEN YAPILDIĞI km (completed_km —
    #     usta bakım sırasında girer, girmezse tamamlanma anındaki araç km'si
    #     otomatik yazılır; bkz. _auto_capture_completed_km / backfill_
    #     completed_km_for_finished_maintenances) planın KENDİ tolerans
    #     penceresi İÇİNDE ise zamanında sayılır. Pencere plan_window ile
    #     hesaplanır (varsayılan -%5/+%10, ör. hedef 200.000 için
    #     [190.000, 220.000]) — yani ölçüt, tamamlananın hedeften SAPMASININ
    #     tolerans aralığında kalıp kalmadığıdır (bkz. compute_km_deviation_
    #     pct, aynı pencereyle karşılaştırılabilir yüzde).
    #
    #     ÖNCEKİ kural iş emrinin AÇILDIĞI andaki km'ye (km_at_open) bakıyordu;
    #     bu, "bakım vaktinde fark edildi mi" sorusunu ölçüyordu, "bakım
    #     vaktinde YAPILDI mı" sorusunu değil. Zamanında iş emri açılıp çok
    #     geç tamamlanan (ör. araç 30.000 km daha yol yaptıktan sonra
    #     bitirilen) bir bakım eskiden "zamanında" görünüyordu. Artık gerçek
    #     yapılma km'si esas alınır.
    #
    #     completed_km hâlâ boşsa (çok eski, geriye dönük doldurmanın da bir
    #     kaynak bulamadığı kayıtlar) elimizdeki en iyi bilgi olan km_at_open'a
    #     düşülür — bu satırlar paydadan DÜŞÜRÜLMEZ, aksi halde oran birkaç
    #     kayıt üzerinden hesaplanıp yanıltıcı olurdu.
    #   - Süre bazlı planlarda: tamamlanma tarihi hedef tarihte veya
    #     ÖNCESİNDE ise zamanında sayılır (completed_at.date() <= target_date).
    #   - Periyodu belirsiz ("unknown") planlar değerlendirmeye alınmaz
    #     (ne zamanında ne geç sayılır — payda dışında tutulur).
    on_time_count = 0
    on_time_evaluated = 0
    for m in completed_in_range_rows:
        if m.plan.plan_type == "time":
            if not m.target_date or not m.completed_at:
                continue
            on_time_evaluated += 1
            if m.completed_at.date() <= m.target_date:
                on_time_count += 1
        elif m.plan.plan_type == "km":
            if m.target_km is None:
                continue
            on_time_evaluated += 1
            window_lower, window_upper = plan_window(m.plan, m.target_km)
            actual_km = m.completed_km if m.completed_km is not None else m.km_at_open
            if window_lower <= actual_km <= window_upper:
                on_time_count += 1
        # plan_type == "unknown": payda dışında bırakılır
    on_time_rate_pct = (
        round(100 * on_time_count / on_time_evaluated, 1) if on_time_evaluated else None
    )

    # ---- 2) Kritik/geciken (GÜNCEL durum, tarih aralığından bağımsız) ----
    open_maintenances = (
        db.query(models.Maintenance)
        .filter(
            models.Maintenance.is_activated == True,  # noqa: E712
            models.Maintenance.status.in_(["pending", "in_progress"]),
        )
        .all()
    )
    urgent_count = 0
    very_critical_count = 0
    overdue_count = 0
    today = date.today()
    for m in open_maintenances:
        if m.plan.plan_type == "time":
            if not m.target_date:
                continue
            remaining_days = (m.target_date - today).days
            if remaining_days <= 0:
                overdue_count += 1
            if remaining_days <= CRITICAL_WITHIN_DAYS:
                very_critical_count += 1
            elif remaining_days <= URGENT_WITHIN_DAYS:
                urgent_count += 1
        else:
            remaining_km = m.target_km - m.vehicle.current_km
            if remaining_km <= 0:
                overdue_count += 1
            interval_km = m.plan.interval_km or 0
            if remaining_km <= interval_km * COK_KRITIK_PCT_OF_INTERVAL / 100:
                very_critical_count += 1
            elif remaining_km <= interval_km * KRITIK_PCT_OF_INTERVAL / 100:
                urgent_count += 1

    # ---- 3) Araç/model bazlı performans ----
    vehicles = db.query(models.Vehicle).all()
    by_vehicle_stats: dict[int, dict] = {
        v.id: {"opened": 0, "completed": 0, "open_now": 0} for v in vehicles
    }
    for m in opened:
        st = by_vehicle_stats.setdefault(m.vehicle_id, {"opened": 0, "completed": 0, "open_now": 0})
        st["opened"] += 1
        if m.status == "completed":
            st["completed"] += 1
    for m in open_maintenances:
        st = by_vehicle_stats.setdefault(m.vehicle_id, {"opened": 0, "completed": 0, "open_now": 0})
        st["open_now"] += 1

    by_model: dict[str, dict] = {}
    for v in vehicles:
        model_key = v.model.strip() if v.model and v.model.strip() else "Tanımsız"
        agg = by_model.setdefault(model_key, {"opened": 0, "completed": 0, "open_now": 0})
        st = by_vehicle_stats.get(v.id, {"opened": 0, "completed": 0, "open_now": 0})
        agg["opened"] += st["opened"]
        agg["completed"] += st["completed"]
        agg["open_now"] += st["open_now"]
    by_vehicle_model = [
        {"vehicle_model": k, **v}
        for k, v in sorted(by_model.items(), key=lambda kv: -kv[1]["opened"])
    ]

    vehicles_sorted = sorted(
        (
            {"vehicle": v, **by_vehicle_stats.get(v.id, {"opened": 0, "completed": 0, "open_now": 0})}
            for v in vehicles
        ),
        key=lambda r: -(r["opened"] + r["open_now"]),
    )

    # ---- 4) Personel/vardiya performansı ----
    item_q = db.query(models.MaintenanceItem).filter(
        models.MaintenanceItem.is_completed == True,  # noqa: E712
        models.MaintenanceItem.completed_by_id.isnot(None),
    )
    if date_from is not None:
        item_q = item_q.filter(models.MaintenanceItem.completed_at >= date_from)
    if date_to is not None:
        item_q = item_q.filter(models.MaintenanceItem.completed_at <= date_to)
    completed_items = item_q.all()

    person_item_counts: dict[int, int] = {}
    for it in completed_items:
        person_item_counts[it.completed_by_id] = person_item_counts.get(it.completed_by_id, 0) + 1

    person_maint_stats: dict[int, dict] = {}
    for m in completed_in_range_rows:
        if m.assigned_to_id is None:
            continue
        st = person_maint_stats.setdefault(m.assigned_to_id, {"count": 0, "days_sum": 0.0})
        st["count"] += 1
        if m.completed_at and m.created_at:
            st["days_sum"] += (m.completed_at - m.created_at).total_seconds() / 86400

    all_person_ids = set(person_item_counts) | set(person_maint_stats)
    users_by_id = (
        {u.id: u for u in db.query(models.User).filter(models.User.id.in_(all_person_ids)).all()}
        if all_person_ids
        else {}
    )
    shifts_by_id = {s.id: s.name for s in db.query(models.Shift).all()}

    by_person = []
    for uid in all_person_ids:
        u = users_by_id.get(uid)
        if not u:
            continue
        mstat = person_maint_stats.get(uid, {"count": 0, "days_sum": 0.0})
        avg_days = round(mstat["days_sum"] / mstat["count"], 1) if mstat["count"] else None
        by_person.append(
            {
                "user": u,
                "items_completed_in_range": person_item_counts.get(uid, 0),
                "maintenances_completed_in_range": mstat["count"],
                "avg_completion_days": avg_days,
                "shift_name": shifts_by_id.get(u.shift_id) if u.shift_id else None,
            }
        )
    by_person.sort(key=lambda r: -r["items_completed_in_range"])

    shift_counts: dict[str, int] = {}
    for uid, cnt in person_item_counts.items():
        u = users_by_id.get(uid)
        shift_name = shifts_by_id.get(u.shift_id) if u and u.shift_id else "Vardiyasız"
        shift_counts[shift_name] = shift_counts.get(shift_name, 0) + cnt
    by_shift = [
        {"shift_name": k, "items_completed_in_range": v}
        for k, v in sorted(shift_counts.items(), key=lambda kv: -kv[1])
    ]

    return {
        "date_from": date_from.date() if date_from else None,
        "date_to": date_to.date() if date_to else None,
        "completion": {
            "opened_in_range": opened_in_range,
            "completed_of_opened": completed_of_opened,
            "completion_rate_pct": completion_rate_pct,
            "completed_in_range": len(completed_in_range_rows),
            "avg_completion_days": avg_completion_days,
            "on_time_count": on_time_count,
            "on_time_evaluated": on_time_evaluated,
            "on_time_rate_pct": on_time_rate_pct,
        },
        "critical": {
            "open_total": len(open_maintenances),
            "urgent_count": urgent_count,
            "very_critical_count": very_critical_count,
            "overdue_count": overdue_count,
        },
        "by_vehicle_model": by_vehicle_model,
        "vehicles": vehicles_sorted,
        "by_person": by_person,
        "by_shift": by_shift,
    }
