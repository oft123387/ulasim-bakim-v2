from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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
    is_active: bool
    manufacturer: str
    model: str
    chassis_no: str
    notes: str
    last_km_update_at: datetime | None
    production_year: int | None = None
    photo_filename: str = ""


class VehicleCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(default="", max_length=100)
    current_km: int = Field(default=0, ge=0)
    manufacturer: str = ""
    model: str = ""
    chassis_no: str = ""
    notes: str = ""
    production_year: int | None = Field(default=None, ge=1900, le=2100)


class VehicleUpdate(BaseModel):
    name: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    chassis_no: str | None = None
    notes: str | None = None
    production_year: int | None = Field(default=None, ge=1900, le=2100)


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
    lower_pct: float
    upper_pct: float
    is_active: bool
    vehicle_model: str = ""


class PlanCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    interval_km: int = Field(gt=0)
    lower_pct: float = Field(default=50.0, ge=0, le=100)
    upper_pct: float = Field(default=10.0, ge=0, le=100)
    vehicle_model: str = ""


class PlanUpdate(BaseModel):
    name: str | None = None
    interval_km: int | None = Field(default=None, gt=0)
    lower_pct: float | None = Field(default=None, ge=0, le=100)
    upper_pct: float | None = Field(default=None, ge=0, le=100)
    is_active: bool | None = None
    vehicle_model: str | None = None


class ImportTreeResult(BaseModel):
    plans: list[PlanOut]
    nodes_created: int
    maintenances_reset: int = 0


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


class PartCatalogItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=500)
    # Belirtilirse yeni parça bu parçanın ALT parçası olarak oluşturulur
    # (ör. "Motorlu Boji" içine "Motor" eklemek gibi).
    parent_id: int | None = None


class PartCatalogItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    order_index: int | None = None
    # None hem "değiştirme" hem "üst parçadan çıkar / en üst seviyeye taşı"
    # anlamına gelebileceğinden (Pydantic tek başına ayıramaz), main.py bu
    # alanın gövdede GEÇİP GEÇMEDİĞİNİ (model_fields_set) kontrol ederek
    # ayırt eder — vardiya (shift_id) alanındaki ile aynı desen.
    parent_id: int | None = None


class PartReorderBody(BaseModel):
    """Sürükle-bırak ile yeniden sıralama: aynı üst parçaya (veya en üst
    seviyeye) ait parça id'lerinin YENİ sırayla listesi. order_index bu
    sıraya göre 0'dan itibaren yeniden atanır."""

    parent_id: int | None = None
    ordered_ids: list[int] = Field(min_length=1)


# ---------- Tree template ----------
class TreeNodeOut(ORM):
    id: int
    parent_id: int | None
    plan_id: int | None
    name: str
    order_index: int
    note: str = ""
    children: list["TreeNodeOut"] = []
    attachments: list[NodeAttachmentOut] = []


class TreeNodeCreate(BaseModel):
    parent_id: int | None = None
    plan_id: int | None = None  # yalnızca kök düğüm (parent_id=None) için anlamlıdır
    name: str = Field(min_length=1, max_length=200)
    order_index: int = 0


class TreeNodeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: int | None = None
    order_index: int | None = None
    note: str | None = None


class TreeNodeFlatOut(ORM):
    """3D model editöründe parça atarken kullanılan düz (children'sız) liste."""

    id: int
    parent_id: int | None
    name: str


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
    note: str = ""  # Faz 10: bağlı şablon maddesinin açıklaması
    attachments: list[NodeAttachmentOut] = []  # Faz 10: bağlı ekler
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
    target_km: int
    km_at_open: int
    status: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    vehicle: VehicleOut
    plan: PlanOut
    assigned_to: UserOut | None


class NoteOut(ORM):
    id: int
    text: str
    created_at: datetime
    author: UserOut | None


class NoteCreateBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    # Paylaşılan tablet senaryosu: notu giren kişi oturum sahibinden farklı
    # olabilir; belirtilirse bu kullanıcı yazar olarak kaydedilir.
    author_id: int | None = None


class MaintenanceDetail(MaintenanceOut):
    items: list[ItemOut] = []
    # Bu bakımın kapsadığı tüm periyotlar (km), artan sıralı. Kademeli
    # bakımda birden fazla; tek periyotlu bakımda tek eleman.
    covered_tiers: list[int] = []
    notes: list[NoteOut] = []


class AssignBody(BaseModel):
    assigned_to_id: int | None


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


# ---------- Dashboard ----------
class UpcomingItem(BaseModel):
    vehicle: VehicleOut
    plan_name: str
    target_km: int
    remaining_km: int
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


class CalendarProjectionItem(BaseModel):
    """Takvim görünümü — bir aracın bir sonraki bakım hedefine ait -%5/+%10
    önizleme penceresinin, günlük ortalama km varsayımıyla hesaplanmış
    takvim tarihi aralığı."""

    vehicle: VehicleOut
    plan_name: str
    target_km: int
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
    active_vehicle_count: int
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
    active_vehicles: int


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


# ---------- Canlı Alarm Merkezi ----------
class AlarmStateOut(ORM):
    id: int
    priority: str
    acknowledged: bool = False
    acknowledged_by: UserOut | None = None
    acknowledged_at: datetime | None = None
    assigned_to: UserOut | None = None
    resolved: bool = False
    resolved_at: datetime | None = None
    closed: bool = False
    closed_at: datetime | None = None


class AlarmStateUpdate(BaseModel):
    """Kısmi güncelleme — yalnızca gönderilen alanlar değiştirilir."""

    priority: str | None = None  # low | normal | high | critical
    assigned_to_id: int | None = None
    acknowledged: bool | None = None
    resolved: bool | None = None
    closed: bool | None = None


class AlarmItem(BaseModel):
    """Bir aracın yaklaşan/geciken bakımı. level:
    * 'critical' (Kritik/Kırmızı) — hedef km geçilmiş (bakım gecikti) veya
      kalan km/gün çok kritik eşiğin altında.
    * 'warning' (Uyarı/Turuncu) — ~14 günden az kaldı.
    * 'normal' (Normal/Yeşil) — bakım önizleme penceresinde, yaklaşıyor."""

    level: str
    vehicle: VehicleOut
    plan_name: str
    target_km: int
    remaining_km: int
    estimated_days: int | None = None
    overdue: bool = False
    # İş emri zaten açıldıysa: bakım id'si ve açılışından bu yana geçen gün.
    maintenance_id: int | None = None
    age_days: int | None = None
    message: str = ""
    # Faz 22 — Kritiklik/Severity/Priority + Acknowledged/Assigned/Resolved/Closed
    state: AlarmStateOut | None = None


class AlarmCenter(BaseModel):
    critical: int
    warning: int
    normal: int
    items: list[AlarmItem] = []


# ---------- Bildirim Merkezi ----------
class NotificationOut(BaseModel):
    """Bildirim akışı öğesi. Çoğu, değiştirilemez işlem kaydından (AuditLog)
    türetilir; 'overdue' türündekiler o anki geciken bakım durumundan
    hesaplanır (id negatiftir, bakım id'sinden türetilir)."""

    id: int
    category: str  # work_order | completed | unlock | user | overdue
    title: str
    detail: str = ""
    created_at: datetime
    maintenance_id: int | None = None


# ---------- Öneriler ----------
class SuggestionOut(ORM):
    id: int
    vehicle: VehicleOut
    plan: PlanOut | None = None
    text: str
    status: str
    admin_note: str = ""
    created_by: UserOut | None = None
    created_at: datetime
    reviewed_by: UserOut | None = None
    reviewed_at: datetime | None = None


class SuggestionCreate(BaseModel):
    vehicle_id: int
    plan_id: int | None = None
    text: str = Field(min_length=1, max_length=2000)


class SuggestionReviewBody(BaseModel):
    status: str = Field(pattern="^(pending|approved|rejected|added)$")
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
