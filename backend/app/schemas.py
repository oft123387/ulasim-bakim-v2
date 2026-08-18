from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- Auth / Users ----------
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    # Faz 23 — Güvenlik sertleştirme: uzun ömürlü (30 gün), iptal edilebilir
    # yenileme jetonu. Ön yüz bunu erişim jetonunu sessizce yenilemek için
    # kullanır (bkz. /api/auth/refresh).
    refresh_token: str = ""


class RefreshTokenBody(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=200)


class UserOut(ORM):
    id: int
    username: str
    full_name: str
    role: str
    is_active: bool
    email: str = ""
    email_verified: bool = False
    shift_id: int | None = None


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    full_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=128)
    role: str = "technician"


class UserUpdate(BaseModel):
    full_name: str | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    role: str | None = None
    is_active: bool | None = None
    # None hem "değiştirme" hem "vardiyayı kaldır" anlamına gelebileceğinden
    # (Pydantic tek başına ayıramaz), main.py bu alanın gövdede GEÇİP
    # GEÇMEDİĞİNİ (model_fields_set) kontrol ederek ayırt eder.
    shift_id: int | None = None


# ---------- Vardiyalar ----------
class ShiftOut(ORM):
    id: int
    name: str
    order_index: int
    # Vardiya sorumlusu (bkz. models.Shift.supervisor_id). Seçilmemişse None —
    # o vardiyada kimse başkası adına madde tikleyemez.
    supervisor: UserOut | None = None


class ShiftCreate(BaseModel):
    name: str


class ShiftUpdate(BaseModel):
    # Yalnızca gönderilen alanlar değişir (model_fields_set ile ayırt edilir):
    # adı değiştirmeden sorumlu atamak ya da tersi mümkün olsun.
    name: str | None = None
    # Vardiya sorumlusu — o vardiyanın ÜYESİ olan bir kullanıcının id'si.
    # None gönderilirse sorumluluk kaldırılır.
    supervisor_id: int | None = None


class ShiftModeConfig(BaseModel):
    enabled: bool


class ShiftModeUpdate(BaseModel):
    enabled: bool


class RegisterBody(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    full_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=8, max_length=128)
    # Zorunlu — kayıt sırasında bu adrese doğrulama kodu gönderilir.
    email: str = Field(min_length=5, max_length=200)


class RegisterResult(BaseModel):
    message: str
    # Kayıt sırasında e-posta verildiyse ve gönderim denendiyse True/False;
    # e-posta verilmediyse None.
    email_sent: bool | None = None


# ---------- E-posta doğrulama / şifremi unuttum ----------
class EmailSendCodeBody(BaseModel):
    email: str = Field(min_length=5, max_length=200)


class EmailVerifyBody(BaseModel):
    code: str = Field(min_length=4, max_length=8)


class UsernameOnlyBody(BaseModel):
    username: str = Field(min_length=1, max_length=50)


class RegisterVerifyBody(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    code: str = Field(min_length=4, max_length=8)


class ForgotPasswordResult(BaseModel):
    message: str


class ResetPasswordBody(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    code: str = Field(min_length=4, max_length=8)
    new_password: str = Field(min_length=8, max_length=128)


# ---------- Vehicles ----------
class VehicleOut(ORM):
    id: int
    code: str
    name: str
    current_km: int
    manufacturer: str
    model: str
    chassis_no: str
    notes: str
    last_km_update_at: datetime | None
    production_year: int | None = None
    photo_filename: str = ""
    # Süre bazlı (aylık/yıllık) bakım planlarının "kaç ay geçti" hesabı için
    # referans tarih. Belirtilmezse araç oluşturulurken bugüne ayarlanır.
    commissioning_date: date | None = None


class VehicleCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(default="", max_length=100)
    current_km: int = Field(default=0, ge=0)
    manufacturer: str = ""
    model: str = ""
    chassis_no: str = ""
    notes: str = ""
    production_year: int | None = Field(default=None, ge=1900, le=2100)
    commissioning_date: date | None = None


class VehicleUpdate(BaseModel):
    name: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    chassis_no: str | None = None
    notes: str | None = None
    production_year: int | None = Field(default=None, ge=1900, le=2100)
    commissioning_date: date | None = None


class VehicleKmUpdate(BaseModel):
    current_km: int = Field(ge=0)


# ---------- Araç Belgeleri (PDF/manuel/foto/CAD-STEP/şema) ----------
class VehicleAttachmentOut(ORM):
    id: int
    vehicle_id: int
    kind: str
    label: str
    filename: str
    original_name: str
    url: str
    created_at: datetime


class ImportResult(BaseModel):
    created: int
    updated: int
    errors: list[str] = []


# ---------- Plans ----------
class PlanOut(ORM):
    id: int
    name: str
    interval_km: int
    threshold_type: str = "percent"
    lower_pct: float
    upper_pct: float
    lower_km: float = 0.0
    upper_km: float = 0.0
    is_active: bool
    vehicle_model: str = ""
    # "km" (varsayılan) veya "time" (süre bazlı — aylık/yıllık).
    plan_type: str = "km"
    interval_months: int | None = None
    # True ise: bu plan bir temel periyodun katıdır (ör. 800.000 km, 200.000
    # km'nin katı) — Bakım Planları sayfasında gizlenir, kendi başına bakım
    # tetiklemez, ama Bakım Ağacı sayfasında (katalog içeriği kaybolmasın diye)
    # görünür/düzenlenebilir kalır.
    is_catalog_only: bool = False


class PlanCreate(BaseModel):
    name: str = Field(default="", max_length=100)
    # "km" planlarında zorunlu (>0); "time" planlarında kullanılmaz (0 kalır).
    interval_km: int = Field(default=0, ge=0)
    # "percent" (varsayılan) veya "km" — açılma penceresinin yüzde mi yoksa
    # sabit kalan kilometre ile mi tanımlanacağını belirler. Yalnızca
    # plan_type="km" için geçerlidir.
    threshold_type: Literal["percent", "km"] = "percent"
    lower_pct: float = Field(default=5.0, ge=0, le=100)
    upper_pct: float = Field(default=10.0, ge=0, le=100)
    lower_km: float = Field(default=0.0, ge=0)
    upper_km: float = Field(default=0.0, ge=0)
    vehicle_model: str = ""
    plan_type: Literal["km", "time"] = "km"
    # "time" planlarında zorunlu (>0), ay cinsinden (1, 3, 6, 12, 24, 72...).
    interval_months: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_type(self):
        if self.plan_type == "km" and self.interval_km <= 0:
            raise ValueError("km planı için interval_km > 0 olmalı")
        if self.plan_type == "time" and not self.interval_months:
            raise ValueError("time planı için interval_months > 0 olmalı")
        return self


class PlanUpdate(BaseModel):
    name: str | None = None
    interval_km: int | None = Field(default=None, gt=0)
    threshold_type: Literal["percent", "km"] | None = None
    lower_pct: float | None = Field(default=None, ge=0, le=100)
    upper_pct: float | None = Field(default=None, ge=0, le=100)
    lower_km: float | None = Field(default=None, ge=0)
    upper_km: float | None = Field(default=None, ge=0)
    is_active: bool | None = None
    vehicle_model: str | None = None
    interval_months: int | None = Field(default=None, gt=0)


class ImportTreeResult(BaseModel):
    plans: list[PlanOut]
    nodes_created: int
    maintenances_reset: int = 0


# ---------- Araç modeli birleştirme (admin) ----------
class VehicleModelMergeIn(BaseModel):
    from_model: str = Field(min_length=1, max_length=100)
    to_model: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _validate(self):
        if self.from_model.strip() == self.to_model.strip():
            raise ValueError("Kaynak ve hedef model aynı olamaz")
        return self


class VehicleModelMergeResult(BaseModel):
    vehicles_moved: int
    plans_renamed: list[dict] = []
    plans_merged: list[dict] = []


# ---------- Toplu Bakım Kataloğu İçe Aktarma (ARAÇ/BÖLGE/SİSTEM/EKİPMAN/BAKIM/BAKIM KALEMİ/KART KODU) ----------
class BulkCatalogSkippedRow(BaseModel):
    row: int
    reason: str
    data: list[str] = []


class BulkCatalogImportResult(BaseModel):
    vehicle_models: list[str]
    plans_created: int
    plans_updated: int
    nodes_created: int
    leaves_created: int
    skipped: list[BulkCatalogSkippedRow] = []
    # BAKIM hücresi yorumlanamayan (ör. "?") ama boş olmayan satırlar —
    # atlanmadı, "Periyodu Belirsiz (?)" planı altında ağaca eklendi.
    unresolved_period: list[BulkCatalogSkippedRow] = []
    # Bir temel periyodun (ör. 200.000 km) TAM KATI olan periyotlar (ör.
    # 800.000 km = 200.000'in 4 katı): kendi bakım ağacı/planı yine
    # oluşturulur (Bakım Ağacı sayfasında görünür), ama "katalog-amaçlı"
    # (is_catalog_only=True) işaretlenir — Bakım Planları sayfasında
    # gösterilmez ve kendi başına bakım açmaz, çünkü temel periyodun (ör.
    # 200.000 km) planı zaten onun katlarında da otomatik tetiklenir (bkz.
    # excel_utils.canonical_base_interval). Serbest biçimli (heterojen
    # alanlar) olduğu için düz dict olarak taşınır.
    catalog_only_periods: list[dict] = []
    # True ise bu bir ÖNİZLEMEYDİ — veritabanına hiçbir şey YAZILMADI, yalnızca
    # gerçek yüklemede ne olacağının bir raporu üretildi (bkz. main.py
    # import_bulk_catalog `dry_run` parametresi).
    dry_run: bool = False


# ---------- Node attachments (Faz 10) ----------
class NodeAttachmentOut(ORM):
    id: int
    node_id: int
    kind: str
    label: str
    filename: str
    original_name: str
    url: str
    created_at: datetime


class AttachmentLinkBody(BaseModel):
    label: str = Field(default="", max_length=200)
    url: str = Field(min_length=1, max_length=600)


# ---------- Bakım Bilgi Bankası (Faz 27) ----------
class NodeKbNoteBody(BaseModel):
    """Bir bakım ağacı maddesinin bilgi/talimat metni (yalnızca açıklama —
    maddenin adı/konumu bu uçtan değiştirilemez)."""

    note: str = Field(default="", max_length=20000)


class KnowledgeHitOut(BaseModel):
    """Bilgi bankası arama sonucu — maddenin kendisi + ağaçtaki yolu."""

    id: int
    name: str
    card_code: str = ""
    note: str = ""
    # Kökten maddeye kadar isim listesi, ör. ["Gövde", "Fren", "Balata"].
    path: list[str] = []
    plan_id: int | None = None
    plan_name: str = ""
    attachment_count: int = 0
    has_video: bool = False


# ---------- Parça Bilgi Bankası ----------
class PartCatalogAttachmentOut(ORM):
    id: int
    part_id: int
    kind: str
    label: str
    filename: str
    original_name: str
    url: str
    created_at: datetime


class PartCatalogItemOut(ORM):
    id: int
    name: str
    description: str = ""
    image_filename: str = ""
    order_index: int = 0
    created_at: datetime
    parent_id: int | None = None
    attachments: list[PartCatalogAttachmentOut] = []
    children: list["PartCatalogItemOut"] = []
    # Parça stok takibi — bkz. models.PartCatalogItem.stock_qty. min_stock=0
    # "stok takibi tanımlanmadı" demektir. float: "1 kg'lık üründen 0.2 kg
    # kullanımı" gibi kesirli miktarları desteklemek için (Integer değil).
    stock_qty: float = 0
    min_stock: float = 0
    unit: str = "adet"


class PartCatalogItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=500)
    # Belirtilirse yeni parça bu parçanın ALT parçası olarak oluşturulur
    # (ör. "Motorlu Boji" içine "Motor" eklemek gibi).
    parent_id: int | None = None
    stock_qty: float = Field(default=0, ge=0)
    min_stock: float = Field(default=0, ge=0)
    unit: str = Field(default="adet", max_length=20)


class PartCatalogItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    order_index: int | None = None
    # None hem "değiştirme" hem "üst parçadan çıkar / en üst seviyeye taşı"
    # anlamına gelebileceğinden (Pydantic tek başına ayıramaz), main.py bu
    # alanın gövdede GEÇİP GEÇMEDİĞİNİ (model_fields_set) kontrol ederek
    # ayırt eder — vardiya (shift_id) alanındaki ile aynı desen.
    parent_id: int | None = None
    min_stock: float | None = Field(default=None, ge=0)
    unit: str | None = Field(default=None, max_length=20)


class PartReorderBody(BaseModel):
    """Sürükle-bırak ile yeniden sıralama: aynı üst parçaya (veya en üst
    seviyeye) ait parça id'lerinin YENİ sırayla listesi. order_index bu
    sıraya göre 0'dan itibaren yeniden atanır."""

    parent_id: int | None = None
    ordered_ids: list[int] = Field(min_length=1)


class PartRepairMaterialOut(BaseModel):
    id: int
    part_id: int
    material_part_id: int | None = None
    # Katalogdan seçilmişse malzemenin adı/stoğu (görüntüleme için) — serbest
    # metin satırlarında material_name boş, free_text dolu olur.
    material_name: str = ""
    material_stock_qty: float | None = None
    material_unit: str = ""
    free_text: str = ""
    qty: float = 1


class PartRepairMaterialIn(BaseModel):
    # İkisinden biri verilmeli: material_part_id (katalogdan) YA DA free_text
    # (serbest metin, stok bağlantısı olmaz).
    material_part_id: int | None = None
    free_text: str = Field(default="", max_length=200)
    # float: "körüğün tamirinde 1 kg yapıştırıcının 0.2 kg'ı kullanılır" gibi
    # kesirli miktarları desteklemek için (Integer değil).
    qty: float = Field(default=1, gt=0)


class PartRepairIn(BaseModel):
    note: str = Field(default="", max_length=300)


class PartRepairResult(BaseModel):
    ok: bool = True
    materials_deducted: list[str] = []  # ör. ["Vida M6 x4"] — stoktan düşülenler
    materials_skipped: list[str] = []  # serbest metin satırlar — stok bağlantısı yok


class PartStockAdjustIn(BaseModel):
    """Manuel stok hareketi — yeni parça geldiğinde (+) veya sayım/fire
    düzeltmesinde (-) kullanılır. Otomatik bakım-bazlı düşüm bunu KULLANMAZ
    (bkz. services._adjust_stock_for_node); bu yalnızca elle giriş içindir."""

    delta: float
    note: str = Field(default="", max_length=300)


class TreeNodePartUsageOut(BaseModel):
    id: int
    tree_node_id: int
    part_id: int
    part_name: str = ""
    qty: float = 1


class TreeNodePartUsageIn(BaseModel):
    part_id: int
    # float: "bu maddede 1 kg'lık üründen 0.2 kg kullanılır" gibi kesirli
    # miktarları desteklemek için (Integer değil).
    qty: float = Field(default=1, gt=0)


# ---------- Tree template ----------
class TreeNodeOut(ORM):
    id: int
    parent_id: int | None
    plan_id: int | None
    name: str
    order_index: int
    note: str = ""
    card_code: str = ""
    # Genel (tüm araçlar için) varsayılan: False ise bu madde bundan sonra
    # açılan iş emirlerinde otomatik tiklenmiş gelir (bkz. PATCH
    # /api/tree/{id}/mandatory). Belirli araçlar için bu değeri geçersiz
    # kılan istisnalar GET /api/tree/{id}/vehicle-exceptions ile ayrıca sorgulanır.
    is_mandatory: bool = True
    children: list["TreeNodeOut"] = []
    attachments: list[NodeAttachmentOut] = []


class TreeNodeCreate(BaseModel):
    parent_id: int | None = None
    plan_id: int | None = None  # yalnızca kök düğüm (parent_id=None) için anlamlıdır
    name: str = Field(min_length=1, max_length=200)
    order_index: int = 0
    card_code: str = Field(default="", max_length=50)


class TreeNodeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: int | None = None
    order_index: int | None = None
    note: str | None = None
    card_code: str | None = Field(default=None, max_length=50)


class TreeNodeMandatoryUpdate(BaseModel):
    """PATCH /api/tree/{id}/mandatory — madde için GENEL (tüm araçlar)
    zorunluluk varsayılanını değiştirir ("hepsine uygula")."""
    is_mandatory: bool


class TreeNodeVehicleExceptionIn(BaseModel):
    """POST /api/tree/{id}/vehicle-exceptions — tek bir araç için genel
    varsayılanı geçersiz kılan istisna ekler/günceller."""
    vehicle_id: int
    is_mandatory: bool = False


class TreeNodeVehicleExceptionOut(ORM):
    id: int
    node_id: int
    vehicle: VehicleOut
    is_mandatory: bool
    created_at: datetime


class TreeNodeFlatOut(ORM):
    """3D model editöründe parça atarken kullanılan düz (children'sız) liste."""

    id: int
    parent_id: int | None
    name: str


class MergedTreeNodeOut(BaseModel):
    """"Tümü" (birleşik) görünüm — bir araç modelindeki TÜM planların (temel
    + katalog-amaçlı/is_catalog_only) ağaçları, ortak Bölge/Sistem/Ekipman
    dallarında birleştirilmiş, salt-okunur bir görünüm. Gerçek TreeNode
    kayıtlarına birebir karşılık gelmez (yapay/sentetik id'ler taşır);
    yalnızca görüntüleme içindir, düzenlenemez. Yaprak (kalem) düğümlerde
    hangi periyottan geldiği `period_label`/`plan_id` ile işaretlenir."""

    id: int
    name: str
    card_code: str = ""
    note: str = ""
    period_label: str | None = None
    plan_id: int | None = None
    is_catalog_only: bool = False
    # Yalnızca "tüm modeller" (all_models=true) görünümünde doludur — yaprağın
    # hangi araç modeline (ör. "Sirio", "Bozankaya Tip-1") ait olduğunu, ya da
    # "Genel" planlarda boş string olarak gösterir.
    vehicle_model: str | None = None
    children: list["MergedTreeNodeOut"] = []


class ItemExecAttachmentOut(ORM):
    """Faz 21 — bakım sırasında (tikleme/inceleme anında) bir maddeye
    eklenen fotoğraf. Şablonun NodeAttachmentOut'undan (admin ekleri) farklı,
    ayrı bir alanda (ItemOut.exec_attachments) taşınır."""
    id: int
    filename: str
    original_name: str
    uploaded_by: UserOut | None = None
    created_at: datetime


# ---------- Maintenance ----------
class ItemOut(ORM):
    id: int
    parent_id: int | None
    name: str
    order_index: int
    is_completed: bool
    is_locked: bool
    completed_at: datetime | None
    completed_by: UserOut | None
    # Maddede FİİLEN çalışan kişiler (çoklu, bkz. models.maintenance_item_workers).
    # Eski kayıtlarda boştur; o durumda arayüz completed_by'ı tek başına gösterir.
    workers: list[UserOut] = []
    note: str = ""  # Faz 10: bağlı şablon maddesinin açıklaması
    card_code: str = ""  # bağlı şablon maddesinin "KART KODU"su (varsa)
    # Faz 27: bu maddenin kopyalandığı şablon düğümü (TreeNode) — bakım
    # sırasındaki "Bilgi" düğmesi Bakım Bilgi Bankası'nda bu düğümü açar.
    source_node_id: int | None = None
    attachments: list[NodeAttachmentOut] = []  # Faz 10: bağlı ekler
    # Faz 21 — usta/mühendis/amirin bakım SIRASINDA bu maddeye eklediği
    # fotoğraflar (şablon ekinden bağımsız, bkz. models.ItemExecAttachment).
    exec_attachments: list[ItemExecAttachmentOut] = []
    # Kademeli bakımda bu maddenin ait olduğu periyotlar (km). ORM'deki
    # 'tiers' (str) ile çakışmasın diye farklı ada sahip; elle doldurulur.
    tier_kms: list[int] = []
    # Usta/mühendisin bakım sırasında bu maddeye eklediği not (şablon
    # notundan bağımsız — bkz. models.MaintenanceItem.exec_note).
    exec_note: str = ""
    exec_note_by: UserOut | None = None
    exec_note_at: datetime | None = None
    children: list["ItemOut"] = []


class MaintenanceOut(ORM):
    id: int
    target_km: int | None
    # Yalnızca süre bazlı (plan.plan_type=="time") iş emirlerinde dolu.
    target_date: date | None = None
    km_at_open: int
    status: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    vehicle: VehicleOut
    plan: PlanOut
    assigned_to: UserOut | None
    # İş emrinin atandığı vardiya (kişiden bağımsız) — bkz. models.Maintenance
    # assigned_shift_id yorumu.
    assigned_shift: ShiftOut | None = None
    # Faz 26 — Bakım başlatma onayı
    is_activated: bool = True
    activated_at: datetime | None = None
    activated_by: UserOut | None = None
    # "Başlat" derken ileri bir tarih seçildiyse dolu — o tarihte otomatik
    # başlatılır (bkz. services.run_scheduled_maintenance_starts).
    scheduled_start_at: date | None = None
    # Kullanıcı Takvim'de iş emrini elle bir güne sürüklediyse o gün; boşsa
    # takvim aracın hedef km'ye ulaşacağı tahmini günü kullanır.
    planned_date: date | None = None
    # Bu bakımın kapsadığı tüm periyotlar (km planlarında interval_km, süre
    # planlarında interval_months), artan sıralı. Kademeli/kümülatif bir
    # bakımda birden fazla eleman olur (ör. [10000, 30000] — 1M+3M birleşik);
    # tekil periyotlu bir bakımda boş kalır (yalnızca plan.name'e bakılır) —
    # bkz. services.compute_covered_tiers. Arayüz, yalnızca "yöneten" (en
    # büyük) planın adını (plan.name, ör. "3M") göstermenin, bakımın aslında
    # daha küçük periyotları da kapsadığını gizlediği bir hatayı düzeltmek
    # için bunu kullanır (frontend format.ts:mergedPeriodLabel).
    covered_tiers: list[int] = []
    # Bakım yapılırken usta/teknisyenin girdiği GÜNCEL km — bkz.
    # models.Maintenance.completed_km yorumu ve PATCH
    # /maintenances/{id}/completed-km.
    completed_km: int | None = None
    # completed_km'nin target_km'den, planın periyoduna oranlı sapma yüzdesi
    # (bkz. services.compute_km_deviation_pct) — Bakım Geçmişi sayfası ve
    # Excel/CSV/PDF/Word dışa aktarımlarında "Sapma %" olarak gösterilir.
    # Yalnızca km bazlı planlarda VE completed_km girilmişse doludur.
    km_deviation_pct: float | None = None

    @model_validator(mode="after")
    def _hide_time_sentinel(self):
        """Süre bazlı bakımlarda target_km, ORM'de dahili benzersizlik amaçlı
        NEGATİF bir sentinel değer tutar (bkz. models.Maintenance yorumu) —
        bu asla API üzerinden dışa sızmamalı, istemci her zaman target_date'e
        bakmalı. Burada tek yerde temizlenir, her endpoint'te tekrarlanmaz."""
        if self.target_km is not None and self.target_km < 0:
            self.target_km = None
        return self

    @model_validator(mode="after")
    def _compute_km_deviation(self):
        """Yukarıdaki _hide_time_sentinel'den SONRA çalışır ama bu, km bazlı
        planlar için sonucu etkilemez (target_km yalnızca süre bazlı
        planlarda temizlenir, orada zaten plan.plan_type != "km" olduğu için
        services.compute_km_deviation_pct erkenden None döner)."""
        # Döngüsel import'u önlemek için burada import edilir (bkz.
        # excel_utils.py'deki aynı desen — services modülü şemaları import
        # etmez ama main.py hem services hem schemas'ı içe aktardığından
        # modül seviyesinde import etmek yerine burada, ihtiyaç anında
        # yapılır).
        from . import services

        self.km_deviation_pct = services.compute_km_deviation_pct(
            self.completed_km, self.target_km, self.plan.interval_km, self.plan.plan_type,
        )
        return self


class MaintenanceStartIn(BaseModel):
    """POST /api/maintenances/{id}/start gövdesi. scheduled_start_at boş
    bırakılırsa (veya bugüne eşit/öncesiyse) davranış eskisi gibi HEMEN
    başlatmadır; bugünden ileri bir tarihse iş emri o tarihe kadar
    zamanlanır, otomatik olarak o gün başlatılır."""
    scheduled_start_at: date | None = None


class MaintenancePlannedDateIn(BaseModel):
    """PATCH /api/maintenances/{id}/planned-date gövdesi. Takvim'de iş emri
    elle bir güne sürüklendiğinde o gün gönderilir; null gönderilirse elle
    planlama kaldırılır ve iş emri yeniden tahmini (hedef km) gününe döner."""
    planned_date: date | None = None


class MaintenancePlanUpcomingIn(BaseModel):
    """Araçlar/Bakımlar akışındaki bir yaklaşan bakımı planlama kuyruğuna alır.

    İstemci yalnızca araç ve hedef kilometreyi gönderir. Sunucu, o hedefe
    bölünen planlar arasından yönetici periyodu seçerek kademeli checklist'i
    tek iş emrinde oluşturur.
    """
    vehicle_id: int
    target_km: int = Field(gt=0)


class NotePhotoOut(ORM):
    """Bir bakım notuna eklenen fotoğraf (bkz. models.MaintenanceNotePhoto).
    ItemExecAttachmentOut ile aynı desen — o madde bazlı, bu not bazlıdır."""
    id: int
    filename: str
    original_name: str
    uploaded_by: UserOut | None = None
    created_at: datetime


class NoteOut(ORM):
    id: int
    text: str
    created_at: datetime
    author: UserOut | None
    photos: list[NotePhotoOut] = []


class NoteCreateBody(BaseModel):
    # min_length YOK (bilerek): yalnızca fotoğraflı, metinsiz not
    # eklenebilmeli — usta sahada bir arızayı yazmak yerine doğrudan
    # fotoğraflamak isteyebilir. Fotoğraflar notun kendisine bağlandığı için
    # önce not oluşturulur, sonra fotoğraflar yüklenir (bkz. main.py
    # upload_note_photo); bu yüzden sunucu, not oluşturulurken fotoğraf gelip
    # gelmeyeceğini bilemez. Tamamen boş not oluşturulmasını arayüz engeller
    # (metin de fotoğraf da yoksa "Ekle" düğmesi pasif).
    text: str = Field(default="", max_length=2000)
    # Paylaşılan tablet senaryosu: notu giren kişi oturum sahibinden farklı
    # olabilir; belirtilirse bu kullanıcı yazar olarak kaydedilir.
    author_id: int | None = None


class MaintenanceDetail(MaintenanceOut):
    items: list[ItemOut] = []
    notes: list[NoteOut] = []


class MaintenanceManualCreate(BaseModel):
    """Faz 28 — Manuel bakım ekleme: admin/mühendis/amir'in otomatik tespiti
    beklemeden araç + periyot + hedef km seçerek elle bir iş emri açması."""

    vehicle_id: int
    plan_id: int
    # Plan bir "km" planıysa target_km, "time" planıysa target_date verilir.
    target_km: int | None = Field(default=None, gt=0)
    target_date: date | None = None


class AssignBody(BaseModel):
    assigned_to_id: int | None


class AssignShiftBody(BaseModel):
    """PATCH /api/maintenances/{id}/assign-shift gövdesi — Atanan (kişi)
    dropdown'undan bağımsız, iş emrini bir vardiyaya atar/vardiyadan çıkarır."""
    assigned_shift_id: int | None


class MaintenanceCompletedKmIn(BaseModel):
    """PATCH /api/maintenances/{id}/completed-km gövdesi — bakım yapılırken
    usta/teknisyenin girdiği GÜNCEL km (bkz. models.Maintenance.completed_km
    yorumu)."""
    completed_km: int = Field(gt=0)


class ItemNoteBody(BaseModel):
    text: str = Field(default="", max_length=2000)
    # Paylaşılan tablet senaryosu: notu giren kişi oturum sahibinden farklı
    # olabilir; belirtilirse bu kullanıcı not sahibi olarak kaydedilir.
    author_id: int | None = None


class CompleteItemBody(BaseModel):
    # Paylaşılan tablet senaryosu: madde tikleyen kişi oturum sahibinden
    # farklı olabilir; belirtilirse tamamlama bu kullanıcıya atfedilir.
    # Bir kez tamamlanan madde kilitlendiği için bu atıf sonradan
    # değiştirilemez (yalnızca yetkili biri kilidi açıp sıfırlarsa yeniden
    # seçilebilir).
    completed_by_id: int | None = None
    # Maddede fiilen çalışan kişiler (çoklu seçim). Vardiyada birden fazla
    # kişi aynı maddede çalışabildiği için tikleme sırasında hepsi
    # işaretlenebilir. Boş bırakılırsa yalnızca completed_by kaydedilir
    # (eski davranış). Liste verilirse ve completed_by_id boşsa, listedeki
    # ilk kişi "tikleyen" olarak da yazılır — mevcut KPI/rapor hesapları
    # completed_by_id üzerinden çalıştığı için bu alan hiç boş kalmaz.
    worker_ids: list[int] = Field(default_factory=list, max_length=50)


# ---------- Dashboard ----------
class UpcomingItem(BaseModel):
    vehicle: VehicleOut
    plan_name: str
    target_km: int = 0
    remaining_km: int = 0
    # Süre bazlı bir plandan geliyorsa dolu (target_km/remaining_km bu
    # durumda 0'dır, arayüz target_date'i gösterir).
    target_date: date | None = None
    covered_tiers: list[int] = []
    # Günlük ort. 300 km varsayımıyla tahmini gün sayısı ve bu bakımın
    # "acil" (<=14 gün) olup olmadığı — dashboard/Araçlar sayfasında daha
    # belirgin gösterilir.
    estimated_days: int | None = None
    urgent: bool = False
    # "Çok kritik": kalan km <=1000 VEYA tahmini gün <=7 — acil'in daha
    # belirgin gösterilen alt kümesi.
    critical: bool = False
    # Zaten açılmış (Bekliyor) gerçek bir İş Emri'ne karşılık geliyorsa o
    # bakımın id'si — dashboard'da satıra tıklayınca doğrudan açmak için.
    # Henüz açılmamış "yaklaşan" tahminler için None'dır.
    maintenance_id: int | None = None
    # Araçlar sayfasındaki 4 durumlu sınıflandırma (bkz.
    # services._classify_upcoming_status): "yaklasiyor" | "planlanacak" |
    # "planlandi" | "gecikti" | None (henüz hiçbir uyarı yok).
    status: str | None = None
    # Yalnızca maintenance_id doluysa (gerçekten açılmış bir iş emri) anlamlı
    # — Bakımlar sayfasının Bakım Akışı tablosunda vardiyaya göre filtreleme/
    # gösterim için (bkz. models.Maintenance.assigned_shift_id).
    assigned_shift_id: int | None = None
    assigned_shift_name: str | None = None
    # Henüz başlatılmamış (is_activated=False) ama admin/mühendis "Başlat"
    # derken ileri bir tarih seçtiyse dolu — o tarihte arka plan
    # zamanlayıcısı tarafından otomatik başlatılır (bkz.
    # services.schedule_maintenance_start/run_scheduled_maintenance_starts).
    # maintenance_id bu durumda hâlâ None'dır (bkz. yukarıdaki alan
    # açıklaması — henüz açılmamış gibi davranılır) ama arayüz bu alanla
    # "Zamanlandı: X" rozetini gösterip "Zamanlamayı İptal Et" seçeneği
    # sunabilir (POST /maintenances/plan-upcoming aynı adaya idempotent
    # şekilde döner, ardından POST /maintenances/{id}/cancel-schedule
    # çağrılabilir).
    scheduled_start_at: date | None = None
    # Takvim sayfasındaki tek-takvim sürükle-bırak yeniden zamanlama için:
    # bu hedefin planın kendi threshold_type ayarına göre GERÇEK tolerans
    # penceresi (günlük ortalama km/aylık takvim varsayımıyla takvim
    # tarihine çevrilmiş) — bkz. services.compute_upcoming_maintenances /
    # compute_upcoming_time_maintenances. Bir öğe bu iki tarihin DIŞINA
    # sürüklenemez.
    window_lower_date: date | None = None
    window_upper_date: date | None = None


class CalendarProjectionItem(BaseModel):
    """Takvim görünümü — bir aracın bir sonraki bakım hedefine ait -%5/+%10
    önizleme penceresinin, günlük ortalama km varsayımıyla hesaplanmış
    takvim tarihi aralığı. Süre bazlı (aylık/yıllık) planlarda target_km
    dahili bir sentinel değerdir (bkz. models.Maintenance yorumu) — gerçek
    hedef her zaman target_date'tedir, arayüz target_date doluysa onu
    kullanmalıdır (UpcomingItem ile aynı sözleşme, bkz. format.ts targetLabel)."""

    vehicle: VehicleOut
    plan_name: str
    target_km: int
    # Yalnızca süre bazlı (plan.plan_type=="time") projeksiyonlarda dolu.
    target_date: date | None = None
    remaining_km: int
    covered_tiers: list[int] = []
    window_start: str
    window_end: str
    status: str | None = None
    maintenance_id: int | None = None


class PlanSummary(BaseModel):
    plan: PlanOut
    pending: int
    in_progress: int
    completed: int


class VehicleSummary(BaseModel):
    vehicle: VehicleOut
    pending: int
    in_progress: int
    completed: int
    open_maintenances: int


class DashboardOut(BaseModel):
    vehicle_count: int
    pending_count: int
    in_progress_count: int
    completed_count: int
    completed_this_month: int
    pending_user_approvals: int
    due_now: list[UpcomingItem] = []  # açılmış ama henüz başlanmamış (Bekliyor) bakımlar
    upcoming: list[UpcomingItem]
    recent_completed: list[MaintenanceOut]
    by_plan: list[PlanSummary] = []
    by_vehicle: list[VehicleSummary] = []
    # Toplu katalog içe aktarmasından kalan, BAKIM periyodu hâlâ "?"
    # (yorumlanamamış) olan madde sayısı — yalnızca admin'e gösterilir (bkz.
    # main.py dashboard()), kaynak Excel netleşene kadar unutulmasın diye.
    unresolved_period_count: int = 0


# ---------- Sistem Durumu (admin) ----------
class SystemStatusOut(BaseModel):
    db_backend: str  # "sqlite" | "postgresql" | ...
    db_location: str  # sqlite: çözümlenmiş mutlak dosya yolu; diğer: şifresiz bağlantı özeti
    foreign_keys_enforced: bool | None = None  # yalnızca sqlite için anlamlı
    started_at: datetime
    uptime_seconds: float
    vehicle_count: int
    user_count: int
    plan_count: int
    catalog_only_plan_count: int
    unresolved_period_count: int
    tree_node_count: int
    maintenance_pending: int
    maintenance_in_progress: int
    maintenance_completed: int
    backup_dir: str
    upload_dir: str
    log_dir: str
    last_backup_at: datetime | None = None


# ---------- 3D Model / Parça Eşleşmesi ----------
class PartHotspotOut(ORM):
    id: int
    model_id: int
    plan_id: int
    tree_node_id: int
    mesh_name: str
    label: str


class PartHotspotCreate(BaseModel):
    plan_id: int
    tree_node_id: int
    mesh_name: str = Field(min_length=1, max_length=300)
    label: str = Field(default="", max_length=200)


class PartHotspotUpdate(BaseModel):
    tree_node_id: int | None = None
    mesh_name: str | None = Field(default=None, min_length=1, max_length=300)
    label: str | None = Field(default=None, max_length=200)


class Model3DOut(ORM):
    id: int
    label: str
    filename: str
    order_index: int


class PartHotspotStatus(BaseModel):
    """Bir bakım örneği için parça eşleşmesi + o parçanın bağlı olduğu
    maddenin tamamlanma durumu (usta ekranında 3D modeli işaretlemek için)."""

    hotspot: PartHotspotOut
    is_completed: bool
    item_id: int | None  # eşleşen MaintenanceItem id'si (varsa)


class Model3DWithStatus(BaseModel):
    id: int
    label: str
    filename: str
    hotspots: list[PartHotspotStatus]


# ---------- Audit Log (Faz 17) ----------
class AuditLogOut(ORM):
    id: int
    created_at: datetime
    actor_id: int | None
    actor_name: str
    action: str
    entity_type: str
    entity_id: int | None
    summary: str
    detail: str
    old_value: str = ""
    new_value: str = ""
    ip_address: str = ""
    user_agent: str = ""


class AuditPage(BaseModel):
    total: int
    items: list[AuditLogOut]


# ---------- Günlük KM Simülasyonu ----------
class KmSimConfig(BaseModel):
    enabled: bool
    min_km: int
    max_km: int
    last_date: str | None
    vehicle_count: int


class KmSimUpdate(BaseModel):
    enabled: bool | None = None
    min_km: int | None = Field(default=None, ge=0, le=100000)
    max_km: int | None = Field(default=None, ge=0, le=100000)


class KmSimRunBody(BaseModel):
    days: int = Field(default=1, ge=1, le=365)


class KmSimRunResult(BaseModel):
    days: int
    vehicles_updated: int
    total_km_added: int
    maintenances_opened: int


# ---------- Yetki Yönetimi ----------
class PermissionsMatrix(BaseModel):
    """Rol -> izin -> açık/kapalı. Yalnızca mühendis, amir ve usta rolleri
    için (admin her zaman tüm izinlere sahiptir, buradan yönetilmez)."""

    engineer: dict[str, bool]
    technician: dict[str, bool]
    amir: dict[str, bool]


class PermissionUpdateBody(BaseModel):
    role: str = Field(pattern="^(engineer|technician|amir)$")
    permission: str
    allowed: bool


class PermissionResetBody(BaseModel):
    role: str = Field(pattern="^(engineer|technician|amir)$")


class PermissionDefOut(BaseModel):
    """Yetki Yönetimi sayfasında gruplama/açıklama için — bkz.
    services.get_permission_defs."""

    key: str
    category: str
    description: str = ""


class BackupInfo(BaseModel):
    filename: str
    size_bytes: int
    created_at: str


class BackupRunResult(BaseModel):
    ok: bool
    filename: str | None = None
    message: str


class ChangePasswordBody(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)


class MyPermissions(BaseModel):
    """Oturum açmış kullanıcının etkin izinleri (frontend'de buton/aksiyon
    görünürlüğü için kullanılır)."""

    role: str
    permissions: dict[str, bool]


# ---------- Global Arama ----------
class SearchHit(BaseModel):
    """Tek bir global arama sonucu. `type` sonucu hangi sayfaya
    yönlendireceğini belirler (frontend); `ref_id` ilgili kaydın id'sidir
    (araç id, bakım id, plan id, kullanıcı id, parça id) — 'pdf' türünde
    `url` doldurulur."""

    type: str  # vehicle | maintenance | plan | user | part | pdf
    ref_id: int | None = None
    title: str
    subtitle: str = ""
    url: str = ""


class SearchResults(BaseModel):
    query: str
    total: int
    vehicles: list[SearchHit] = []
    maintenances: list[SearchHit] = []
    plans: list[SearchHit] = []
    parts: list[SearchHit] = []
    users: list[SearchHit] = []
    documents: list[SearchHit] = []


# ---------- Bildirim Merkezi ----------
class NotificationOut(BaseModel):
    """Bildirim akışı öğesi. Çoğu, değiştirilemez işlem kaydından (AuditLog)
    türetilir; 'overdue' türündekiler o anki geciken bakım durumundan
    hesaplanır (id negatiftir, bakım id'sinden türetilir)."""

    id: int
    category: str  # work_order | completed | unlock | user | overdue | suggestion | low_stock
    title: str
    detail: str = ""
    created_at: datetime
    maintenance_id: int | None = None


# ---------- Öneriler (Faz 30 — hiyerarşik onay) ----------
class SuggestionOut(ORM):
    id: int
    # Geriye dönük uyumluluk: bu özellikten önce belirli bir araca bağlı
    # oluşturulmuş eski öneriler için dolu; yeni (model bazlı) önerilerde None.
    vehicle: VehicleOut | None = None
    # Yeni önerilerde dolu — önerinin hedeflediği araç modeli (Bozankaya/Sirio vb.).
    vehicle_model: str = ""
    plan: PlanOut | None = None
    text: str
    # pending | rejected | approved. ('pending_engineer' ve 'added' yalnızca
    # Faz 30'daki iki aşamalı zincirden kalan ESKİ kayıtlarda görülebilir —
    # yeni önerilerde üretilmez, bkz. migrate.py.)
    status: str
    admin_note: str = ""
    created_by: UserOut | None = None
    created_at: datetime
    reviewed_by: UserOut | None = None
    reviewed_at: datetime | None = None
    # Faz 30'un iki aşamalı zincirinden kalan alanlar. Zincir kaldırıldı;
    # bunlar artık yalnızca "kim karar verdi" bilgisini ve eski kayıtların
    # geçmişini taşır (bkz. services.apply_suggestion_review).
    amir_status: str = "pending"
    amir_reviewed_by: UserOut | None = None
    amir_reviewed_at: datetime | None = None
    amir_note: str = ""
    engineer_status: str = "pending"
    engineer_reviewed_by: UserOut | None = None
    engineer_reviewed_at: datetime | None = None
    engineer_note: str = ""


class SuggestionCreate(BaseModel):
    # Öneri artık BELİRLİ bir araca değil bir ARAÇ MODELİNE bağlanır (bkz.
    # /api/vehicle-models) — o modeldeki TÜM araçları ilgilendirir.
    vehicle_model: str = Field(min_length=1, max_length=100)
    plan_id: int | None = None
    text: str = Field(min_length=1, max_length=2000)


class SuggestionReviewBody(BaseModel):
    # Faz 31: onay tek aşamalı — yalnızca onayla/reddet. Eski 'added'
    # ("Bakım ağacına eklendi") kararı kaldırıldı; ağaç düzenlemesi
    # önerinin onaylanmasından ayrı, bilinçli bir işlemdir.
    status: str = Field(pattern="^(approved|rejected)$")
    admin_note: str = Field(default="", max_length=2000)


# ---------- Araç Profil Sayfası ----------
class VehicleProfile(BaseModel):
    vehicle: VehicleOut
    total_maintenances: int
    completed_count: int
    open_count: int
    pending_count: int
    in_progress_count: int
    last_completed: MaintenanceOut | None = None
    next_upcoming: UpcomingItem | None = None
    last_note: NoteOut | None = None
    recent: list[MaintenanceOut] = []
    attachments: list[VehicleAttachmentOut] = []


# ---------- Faz 18: KPI ----------
class KPICompletionOut(BaseModel):
    opened_in_range: int
    completed_of_opened: int
    completion_rate_pct: float
    completed_in_range: int
    avg_completion_days: float | None = None
    # Zamanında tamamlanan bakım oranı — yalnızca TAMAMLANMIŞ iş emirleri
    # üzerinden. Km bazlı planlarda bakımın gerçekten yapıldığı km
    # (completed_km) planın tolerans penceresi içinde mi, süre bazlı
    # planlarda completed_at <= target_date mi diye bakılır; periyodu
    # belirsiz planlar payda dışında bırakılır (bkz. services.
    # compute_kpi_report).
    on_time_count: int = 0
    on_time_evaluated: int = 0
    on_time_rate_pct: float | None = None


class KPICriticalOut(BaseModel):
    # Not: bu bölüm her zaman GÜNCEL açık iş emirlerine göredir, tarih
    # aralığından bağımsızdır (bkz. services.compute_kpi_report docstring).
    open_total: int
    urgent_count: int
    very_critical_count: int
    overdue_count: int


class KPIVehicleModelRow(BaseModel):
    vehicle_model: str
    opened: int
    completed: int
    open_now: int


class KPIVehicleRow(BaseModel):
    vehicle: VehicleOut
    opened: int
    completed: int
    open_now: int


class KPIPersonRow(BaseModel):
    user: UserOut
    items_completed_in_range: int
    maintenances_completed_in_range: int
    avg_completion_days: float | None = None
    shift_name: str | None = None


class KPIShiftRow(BaseModel):
    shift_name: str
    items_completed_in_range: int


class KPIOut(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    completion: KPICompletionOut
    critical: KPICriticalOut
    by_vehicle_model: list[KPIVehicleModelRow]
    top_vehicles: list[KPIVehicleRow]
    by_person: list[KPIPersonRow]
    by_shift: list[KPIShiftRow]
