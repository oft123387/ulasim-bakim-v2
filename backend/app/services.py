"""Çekirdek iş kuralları: otomatik bakım açma, otomatik tamamlanma, kilit."""

import math
import random
import secrets
from contextvars import ContextVar
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from . import models

# main.py'deki _capture_request_context middleware'i her istekte bu iki
# değişkeni doldurur; log_action bunları otomatik okuyarak denetim kaydına
# IP/tarayıcı bilgisini ekler — her çağıran fonksiyonun `request` parametresi
# alıp elle geçirmesine gerek kalmaz.
REQUEST_IP: ContextVar[str] = ContextVar("REQUEST_IP", default="")
REQUEST_UA: ContextVar[str] = ContextVar("REQUEST_UA", default="")


# ------------------------------------------------------ açık bakım tekilleştirme
def dedupe_open_by_vehicle(open_maintenances: list["models.Maintenance"]) -> list["models.Maintenance"]:
    """Aynı araç için birden fazla açık (Bekliyor/Devam Ediyor) iş emri
    varsa yalnızca hedef km'si en büyük olanı döner — küçük periyotlar
    (ör. 25.000/50.000 km) zaten o büyük bakımın kombine checklist'ine
    dahildir, ayrı sayılmaz/gösterilmez. Yalnızca TAMAMLANMAMIŞ kayıtlar
    verilmeli (tamamlananlar geçmiş kayıt olduğu için bu tekilleştirmeye
    tabi değildir — ayrıca sayılır).

    Dashboard'daki "Bekleyen Bakım" sayısı ile Bakımlar sayfasındaki
    listenin (aynı mantığı istemci tarafında uygulayan) tutarlı olması
    için bu tek yerde tutulur."""
    best_by_vehicle: dict[int, models.Maintenance] = {}
    for m in open_maintenances:
        cur = best_by_vehicle.get(m.vehicle_id)
        if cur is None or m.target_km > cur.target_km:
            best_by_vehicle[m.vehicle_id] = m
    return list(best_by_vehicle.values())


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
    return entry

# Dashboard/Araçlar listesinde "yaklaşan bakım" olarak gösterilecek sabit
# önizleme penceresi. Planın kendi (çok daha geniş olabilen) otomatik-açma
# toleransından (lower_pct/upper_pct) bağımsızdır — yalnızca "gerçekten
# yaklaşmış" olanları göstermek için kullanılır.
PREVIEW_LOWER_PCT = 5.0
PREVIEW_UPPER_PCT = 10.0

# Günlük ortalama km varsayımı (takvim projeksiyonu ve "kalan gün" tahmini
# aynı sabiti kullanır, tutarlı kalsın diye tek yerde tanımlanır).
DEFAULT_CALENDAR_DAILY_KM = 300.0

# Bir bakıma tahminen bu kadar gün veya daha az kaldıysa "acil / kritik"
# olarak işaretlenir (dashboard ve Araçlar sayfasında daha belirgin gösterilir).
URGENT_WITHIN_DAYS = 14

# Bunun daha da altında (kalan km VEYA tahmini gün bazında) ise "çok kritik"
# — acil kümesinin en üst/en belirgin alt kümesi. Dashboard ve Araçlar
# sayfasında ayrıca vurgulanır (ör. kırmızı yerine daha koyu/parlayan ton).
CRITICAL_WITHIN_DAYS = 7
CRITICAL_WITHIN_KM = 1000


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
    # yüklenmiş bakım ağaçları birbirine karışmaz.
    active_plans = [
        p for p in plans
        if p.interval_km > 0 and (not p.vehicle_model or p.vehicle_model == vehicle.model)
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

    for target in sorted(candidates):
        if skip_passed_targets and target <= km:
            continue
        applicable = [p for p in active_plans if target % p.interval_km == 0]
        if not applicable:
            continue
        governing = max(applicable, key=lambda p: p.interval_km)

        lower = target - governing.interval_km * governing.lower_pct / 100
        upper = target + governing.interval_km * governing.upper_pct / 100
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
        )
        db.add(m)
        db.flush()
        _copy_tree_snapshot(db, m, applicable)
        opened.append(m)

    if opened:
        db.commit()
    return opened


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
    ordered = sorted(applicable_plans, key=lambda p: p.interval_km)

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
    for plan in sorted(ordered, key=lambda p: -p.interval_km):
        for r in _plan_roots(db, plan):
            walk(r, None, tuple(), plan.interval_km)

    for key, item in by_path.items():
        item.tiers = ",".join(str(k) for k in sorted(tiers[key]))


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
        vehicles = db.query(models.Vehicle).filter_by(is_active=True).all()
    if plans is None:
        plans = db.query(models.MaintenancePlan).filter_by(is_active=True).all()

    upcoming: list[dict] = []
    for v in vehicles:
        km = v.current_km
        # Yalnızca "genel" (vehicle_model="") planlar veya bu aracın
        # modeliyle birebir eşleşen planlar bu araç için değerlendirilir.
        active_plans = [
            p for p in plans
            if p.interval_km > 0 and (not p.vehicle_model or p.vehicle_model == v.model)
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
            if exists is not None:
                # Tamamlanmış bakım her zaman listeden düşer. Açık (Bekliyor/
                # Devam Ediyor) iş emri ise include_open=True çağrılarında
                # (Araçlar sayfası uyarıları) listede kalır — bakım henüz
                # yapılmadıysa araç "yaklaşan/zamanı gelen" görünmeye devam
                # etmeli. Dashboard (include_open=False) bunları ayrıca
                # due_now listesinde gösterdiği için burada atlar.
                if not include_open or exists.status == "completed":
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
                # gösterilmesine yol açar.
                lower_bound = target - governing.interval_km * PREVIEW_LOWER_PCT / 100
                upper_bound = target + governing.interval_km * PREVIEW_UPPER_PCT / 100
                if not (lower_bound <= km <= upper_bound):
                    continue
            remaining_km = target - km
            # Günlük ortalama km varsayımıyla kaç güne kaldığı tahmini.
            # Hedef zaten geçilmişse (remaining_km <= 0) 0 gün — o da acil sayılır.
            estimated_days = (
                math.ceil(remaining_km / DEFAULT_CALENDAR_DAILY_KM) if remaining_km > 0 else 0
            )
            upcoming.append(
                {
                    "vehicle": v,
                    "plan_name": governing.name,
                    "target_km": target,
                    "remaining_km": remaining_km,
                    "covered_tiers": sorted(p.interval_km for p in applicable),
                    "estimated_days": estimated_days,
                    "urgent": estimated_days <= URGENT_WITHIN_DAYS,
                    # "Çok kritik": kalan km 1000'in altında VEYA tahmini 7
                    # günden az kaldıysa — acil'in en belirgin alt kümesi.
                    "critical": remaining_km <= CRITICAL_WITHIN_KM or estimated_days <= CRITICAL_WITHIN_DAYS,
                    # Zaten açılmış (Bekliyor/Devam Ediyor) gerçek bir iş
                    # emrine karşılık geliyorsa o kaydın id'si; henüz
                    # açılmamış (yalnızca yaklaşan önizleme) bir aday ise None.
                    "maintenance_id": exists.id if exists is not None else None,
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
        vehicles = db.query(models.Vehicle).filter_by(is_active=True).all()
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
            if p.interval_km > 0 and (not p.vehicle_model or p.vehicle_model == v.model)
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

            lower_km = target - governing.interval_km * PREVIEW_LOWER_PCT / 100
            upper_km = target + governing.interval_km * PREVIEW_UPPER_PCT / 100

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

    m = item.maintenance
    if m.status == "pending":
        m.status = "in_progress"
        m.started_at = m.started_at or datetime.now()

    _cascade_up(db, item)
    _check_maintenance_done(db, m)
    db.commit()


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


def _check_maintenance_done(db: Session, m: models.Maintenance) -> None:
    roots = (
        db.query(models.MaintenanceItem)
        .filter_by(maintenance_id=m.id, parent_id=None)
        .all()
    )
    if roots and all(r.is_completed for r in roots):
        m.status = "completed"
        m.completed_at = datetime.now()


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
    item.is_completed = False
    item.is_locked = False
    item.completed_at = None
    item.completed_by_id = None
    for child in db.query(models.MaintenanceItem).filter_by(parent_id=item.id).all():
        _reset_subtree(db, child)


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

    vehicles = db.query(models.Vehicle).filter_by(is_active=True).all()
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
    },
    "technician": {
        "view_maintenance": True,
        "edit_maintenance": True,
        "delete_maintenance": False,
        "approve_maintenance": False,
        "export_excel": True,
        "manage_users": False,
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
