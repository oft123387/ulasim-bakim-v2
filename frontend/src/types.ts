export interface User {
  id: number
  username: string
  full_name: string
  role: 'admin' | 'engineer' | 'technician' | 'amir'
  is_active: boolean
  email: string
  email_verified: boolean
  shift_id: number | null
}

export interface Shift {
  id: number
  name: string
  order_index: number
  // Vardiya sorumlusu — yalnızca bu kişi (ve yönetici/mühendis/amir) bir
  // maddeyi BAŞKALARI adına tikleyebilir. Seçilmemişse null.
  supervisor: User | null
}

export interface ShiftModeConfig {
  enabled: boolean
}

export interface Vehicle {
  id: number
  code: string
  name: string
  current_km: number
  manufacturer: string
  model: string
  chassis_no: string
  notes: string
  last_km_update_at: string | null
  production_year: number | null
  photo_filename: string
  // Süre bazlı (aylık/yıllık) bakımların "geçen ay" hesabı için baz tarih —
  // verilmezse araç oluşturulduğu tarih kullanılır.
  commissioning_date: string | null
}

export interface Plan {
  id: number
  name: string
  interval_km: number
  threshold_type: 'percent' | 'km'
  lower_pct: number
  upper_pct: number
  lower_km: number
  upper_km: number
  is_active: boolean
  vehicle_model?: string
  // 'km' (varsayılan) ya da 'time' — time ise interval_months dolu, interval_km 0'dır.
  // 'unknown': toplu katalogda BAKIM periyodu yorumlanamamış (ör. "?") satırlar için —
  // interval_km=0 ve interval_months=null, otomatik motorlardan hariç tutulur.
  plan_type: 'km' | 'time' | 'unknown'
  interval_months: number | null
  // True ise bu plan bir temel periyodun tam katıdır (ör. 800.000 km,
  // 200.000 km'nin katı) — Bakım Planları sayfasında (varsayılan
  // GET /api/plans) gösterilmez, kendi başına bakım tetiklemez, ama Bakım
  // Ağacı sayfasında (?include_catalog_only=true) görünür/düzenlenebilir.
  is_catalog_only?: boolean
}

export interface NodeAttachment {
  id: number
  node_id: number
  // Faz 27 (Bakım Bilgi Bankası): 'video' ve 'cad' de eklendi — video
  // dosyaları ve tanınan video bağlantıları (YouTube/Vimeo) artık gömülü
  // oynatıcıda açılır, eskiden olduğu gibi 'file'/'link' olarak kalmaz.
  kind: 'image' | 'pdf' | 'video' | 'cad' | 'file' | 'link'
  label: string
  filename: string
  original_name: string
  url: string
  created_at: string
}

// Faz 21 — bakım SIRASINDA (bir maddeyi tikleyip incelerken) tablet
// kamerasıyla/galeriden eklenen fotoğraf. Şablonun NodeAttachment'ından
// (admin'in bakım ağacına eklediği referans görseli) farklıdır.
export interface ItemExecAttachment {
  id: number
  filename: string
  original_name: string
  uploaded_by: User | null
  created_at: string
}

// ---------- Bakım Bilgi Bankası (Faz 27) ----------
// GET /api/knowledge/search sonucu — madde + ağaçtaki yolu.
export interface KnowledgeHit {
  id: number
  name: string
  card_code: string
  note: string
  // Kökten maddeye kadar isim listesi, ör. ["Gövde", "Fren", "Balata"].
  path: string[]
  plan_id: number | null
  plan_name: string
  attachment_count: number
  has_video: boolean
}

export interface TreeNode {
  id: number
  parent_id: number | null
  plan_id: number | null
  name: string
  order_index: number
  note: string
  // Toplu bakım kataloğu içe aktarmasından gelen üretici kart/talimat kodu
  // (ör. "SKA-MRM-07-A05"). Boşsa rozet gösterilmez.
  card_code: string
  // False ise bu madde "zorunlu değil" işaretlenmiştir — yeni açılan iş
  // emirlerinde otomatik tikli gelir (usta elle dokunmasına gerek kalmaz).
  // Varsayılan (ve mevcut tüm eski maddeler) true'dur — davranış değişmez.
  is_mandatory: boolean
  children: TreeNode[]
  attachments: NodeAttachment[]
}

// Bir madde belirli bir ARAÇ için genel is_mandatory varsayılanından farklı
// davranmasını sağlayan istisna (ör. genelde zorunlu bir madde, yalnızca
// 3805 nolu araçta zorunlu değil).
export interface TreeNodeVehicleException {
  id: number
  node_id: number
  vehicle: Vehicle
  is_mandatory: boolean
  created_at: string
}

// Bir bakım ağacı maddesi tamamlanınca hangi parçadan kaç adet otomatik
// düşüleceğini tanımlar — bkz. PartCatalogItem.stock_qty.
export interface TreeNodePartUsage {
  id: number
  tree_node_id: number
  part_id: number
  part_name: string
  qty: number
}

export interface Item {
  id: number
  parent_id: number | null
  name: string
  order_index: number
  is_completed: boolean
  is_locked: boolean
  completed_at: string | null
  completed_by: User | null
  // Maddede fiilen çalışan kişiler (çoklu). Eski kayıtlarda boş dizidir; o
  // durumda arayüz completed_by'ı tek başına gösterir.
  workers: User[]
  note: string
  card_code: string
  // Faz 27: bu maddenin kopyalandığı bakım ağacı düğümü — "Bilgi" düğmesi
  // Bakım Bilgi Bankası'nda bu düğümü açar (eski kayıtlarda null olabilir).
  source_node_id: number | null
  attachments: NodeAttachment[]
  tier_kms: number[]
  exec_note: string
  exec_note_by: User | null
  exec_note_at: string | null
  // Faz 21 — bakım sırasında bu maddeye eklenen fotoğraflar.
  exec_attachments: ItemExecAttachment[]
  children: Item[]
}

/** Bir bakım notuna eklenen fotoğraf (backend: MaintenanceNotePhoto).
 * /uploads/notes/<filename> altında servis edilir. */
export interface NotePhoto {
  id: number
  filename: string
  original_name: string
  uploaded_by: User | null
  created_at: string
}

export interface MaintenanceNote {
  id: number
  text: string
  created_at: string
  author: User | null
  // Notla birlikte yüklenen fotoğraflar. Eski kayıtlarda boş dizidir.
  photos?: NotePhoto[]
}

export interface Maintenance {
  id: number
  // Süre bazlı bakımlarda backend bu alanı gizler (null döner) — o durumda
  // gerçek hedef target_date'dedir.
  target_km: number | null
  target_date: string | null
  km_at_open: number
  status: 'pending' | 'in_progress' | 'completed'
  created_at: string
  started_at: string | null
  completed_at: string | null
  vehicle: Vehicle
  plan: Plan
  assigned_to: User | null
  // İş emrinin (kişiden bağımsız) atandığı vardiya — bkz. PATCH
  // /maintenances/{id}/assign-shift.
  assigned_shift?: Shift | null
  items?: Item[]
  covered_tiers?: number[]
  notes?: MaintenanceNote[]
  // Faz 26 — Bakım başlatma onayı
  is_activated: boolean
  activated_at: string | null
  activated_by: User | null
  // "Başlat" derken ileri bir tarih seçildiyse dolu — o tarihte otomatik başlatılır.
  scheduled_start_at: string | null
  // Takvim'de elle sürüklenip bir güne taşındıysa o gün (YYYY-MM-DD); boşsa
  // takvim aracın hedef km'ye ulaşacağı tahmini günü kullanır.
  planned_date?: string | null
  // Bakımı yapan kişinin girdiği, bakımın gerçekte yapıldığı güncel km
  // (yalnızca km bazlı planlarda anlamlıdır) — bkz. PATCH
  // /maintenances/{id}/completed-km.
  completed_km?: number | null
  // completed_km girildiyse hedef km'ye göre sapma yüzdesi (negatif=erken,
  // pozitif=geç yapıldı); backend'de hesaplanır, salt okunurdur.
  km_deviation_pct?: number | null
}

export interface UpcomingItem {
  vehicle: Vehicle
  plan_name: string
  // Süre bazlı maddelerde target_km=0'dır — gerçek hedef target_date'dedir;
  // remaining_km de o zaman km değil gün sayısıdır (negatif=gecikti).
  target_km: number
  target_date?: string | null
  remaining_km: number
  covered_tiers?: number[]
  estimated_days?: number | null
  urgent?: boolean
  critical?: boolean
  maintenance_id?: number | null
  // 4 durumlu sınıflandırma (bkz. backend services._classify_upcoming_status):
  // "yaklasiyor" | "planlanacak" | "planlandi" | "gecikti" | null.
  status?: 'yaklasiyor' | 'planlanacak' | 'planlandi' | 'gecikti' | null
  // Yalnızca maintenance_id doluysa anlamlı — bkz. Maintenance.assigned_shift.
  assigned_shift_id?: number | null
  assigned_shift_name?: string | null
  // Henüz başlatılmamış ama "Başlat" derken ileri bir tarihe zamanlandıysa
  // dolu (bkz. Maintenance.scheduled_start_at) — maintenance_id bu durumda
  // hâlâ null olabilir (henüz açılmamış gibi davranılır).
  scheduled_start_at?: string | null
  // Takvim'deki tek-takvim sürükle-bırak kısıtlaması için: planın kendi
  // threshold_type ayarına göre GERÇEK tolerans penceresi (takvim tarihine
  // çevrilmiş) — bkz. backend services.compute_upcoming_maintenances /
  // compute_upcoming_time_maintenances. Bu öğe bu iki tarihin DIŞINA
  // sürüklenemez.
  window_lower_date?: string | null
  window_upper_date?: string | null
}

export interface CalendarProjection {
  vehicle: Vehicle
  plan_name: string
  target_km: number
  target_date?: string | null
  remaining_km: number
  covered_tiers?: number[]
  window_start: string
  window_end: string
  status: 'pending' | 'in_progress' | 'completed' | null
  maintenance_id: number | null
}

export interface PlanSummary {
  plan: Plan
  pending: number
  in_progress: number
  completed: number
}

export interface VehicleSummary {
  vehicle: Vehicle
  pending: number
  in_progress: number
  completed: number
  open_maintenances: number
}

export interface Dashboard {
  vehicle_count: number
  pending_count: number
  in_progress_count: number
  completed_count: number
  completed_this_month: number
  pending_user_approvals: number
  due_now?: UpcomingItem[]
  upcoming: UpcomingItem[]
  recent_completed: Maintenance[]
  by_plan: PlanSummary[]
  by_vehicle: VehicleSummary[]
  // Toplu katalog içe aktarmasından kalan, periyodu hâlâ "?" olan madde
  // sayısı (yalnızca admin/mühendis/amir için hesaplanır, diğerlerinde 0).
  unresolved_period_count?: number
}

// ---------- Sistem Durumu (admin) ----------
export interface SystemStatus {
  db_backend: string
  db_location: string
  foreign_keys_enforced: boolean | null
  started_at: string
  uptime_seconds: number
  vehicle_count: number
  user_count: number
  plan_count: number
  catalog_only_plan_count: number
  unresolved_period_count: number
  tree_node_count: number
  maintenance_pending: number
  maintenance_in_progress: number
  maintenance_completed: number
  backup_dir: string
  upload_dir: string
  log_dir: string
  last_backup_at: string | null
}

export interface ImportTreeResult {
  plans: Plan[]
  nodes_created: number
  maintenances_reset: number
}

// ---------- Araç modeli birleştirme (admin) ----------
export interface VehicleModelMergeResult {
  vehicles_moved: number
  plans_renamed: { plan_id: number; name: string; interval_km: number }[]
  plans_merged: {
    plan_id: number
    name: string
    interval_km: number
    merged_into_plan_id: number
    action: string
    moved_completed: number
    removed_open: number
  }[]
}

// ---------- Toplu Bakım Kataloğu İçe Aktarma (KB TOPLU LİSTE formatı) ----------
export interface BulkCatalogSkippedRow {
  row: number
  reason: string
  data: string[]
}

export interface BulkCatalogImportResult {
  vehicle_models: string[]
  plans_created: number
  plans_updated: number
  nodes_created: number
  leaves_created: number
  skipped: BulkCatalogSkippedRow[]
  // BAKIM hücresi yorumlanamayan (ör. "?") ama boş olmayan satırlar —
  // atlanmadı, "Periyodu Belirsiz (?)" planı altında ağaca eklendi.
  unresolved_period: BulkCatalogSkippedRow[]
  // Bir temel periyodun (ör. 200.000 km) tam katı olan periyotlar (ör.
  // 800.000 km = 4×200.000). Kendi ayrı planı/ağacı yine oluşur (Bakım
  // Ağacı'nda görünür), ama is_catalog_only=True işaretlenir — Bakım
  // Planları sayfasında gösterilmez, kendi başına bakım tetiklemez.
  catalog_only_periods?: BulkCatalogCatalogOnlyRow[]
  // true ise bu bir önizlemedir (dry_run=true ile gönderildi) — hiçbir şey
  // veritabanına kaydedilmedi, sayaçlar yalnızca "eğer yüklenseydi ne
  // olurdu" bilgisini gösterir.
  dry_run?: boolean
}

export interface BulkCatalogCatalogOnlyRow {
  reason: string
  km?: number
  base_km?: number
  vehicle_model?: string
}

// "Tümü" (birleşik) görünüm — GET /api/tree/merged. Salt-okunur; gerçek
// TreeNode kayıtlarına birebir karşılık gelmez, yaprak maddelerde hangi
// periyottan geldiği period_label/plan_id ile işaretlenir.
export interface MergedTreeNode {
  id: number
  name: string
  card_code: string
  note: string
  period_label: string | null
  plan_id: number | null
  is_catalog_only: boolean
  // Yalnızca "tüm modeller" (Genel/mevcut filo) birleşik görünümünde
  // doludur — yaprağın hangi araç modeline ait olduğunu gösterir (boş
  // string = Genel plan).
  vehicle_model: string | null
  children: MergedTreeNode[]
}

export interface TreeNodeFlat {
  id: number
  parent_id: number | null
  name: string
}

export interface PartHotspot {
  id: number
  model_id: number
  plan_id: number
  tree_node_id: number
  mesh_name: string
  label: string
}

export interface Model3D {
  id: number
  label: string
  filename: string
  order_index: number
}

export interface PartHotspotStatus {
  hotspot: PartHotspot
  is_completed: boolean
  item_id: number | null
}

export interface Model3DWithStatus {
  id: number
  label: string
  filename: string
  hotspots: PartHotspotStatus[]
}

export const STATUS_TR: Record<string, string> = {
  pending: 'Bekliyor',
  in_progress: 'Devam Ediyor',
  completed: 'Tamamlandı',
}

export const ROLE_TR: Record<string, string> = {
  admin: 'Admin',
  engineer: 'Mühendis',
  technician: 'Usta',
  amir: 'Amir',
}

export interface AuditLog {
  id: number
  created_at: string
  actor_id: number | null
  actor_name: string
  action: string
  entity_type: string
  entity_id: number | null
  summary: string
  detail: string
  old_value: string
  new_value: string
  ip_address: string
  user_agent: string
}

export interface AuditPage {
  total: number
  items: AuditLog[]
}

// ---------- Dışa Aktarma (Faz 22) ----------
export type ExportFormat = 'xlsx' | 'pdf' | 'csv' | 'docx'

export const EXPORT_FORMAT_TR: Record<ExportFormat, string> = {
  xlsx: 'Excel',
  pdf: 'PDF',
  csv: 'CSV',
  docx: 'Word',
}

export interface KmSimConfig {
  enabled: boolean
  min_km: number
  max_km: number
  last_date: string | null
  vehicle_count: number
}

export interface KmSimRunResult {
  days: number
  vehicles_updated: number
  total_km_added: number
  maintenances_opened: number
}

export const AUDIT_ACTION_TR: Record<string, string> = {
  create: 'Oluşturma',
  update: 'Güncelleme',
  delete: 'Silme',
  complete: 'Tamamlama',
  unlock: 'Kilit Açma',
  import: 'İçe Aktarma',
  export: 'Dışa Aktarma',
  login: 'Giriş',
  backup: 'Yedekleme',
}

export const AUDIT_ENTITY_TR: Record<string, string> = {
  vehicle: 'Araç',
  plan: 'Bakım Planı',
  user: 'Kullanıcı',
  tree_node: 'Ağaç Maddesi',
  maintenance: 'Bakım',
  item: 'Kontrol Maddesi',
  attachment: 'Ek',
  model3d: '3D Model',
  permission: 'Yetki',
  system: 'Sistem',
  part: 'Parça',
  part_attachment: 'Parça Eki',
  alarm: 'Alarm',
  suggestion: 'İyileştirme Talebi',
}

// ---------- Parça Bilgi Bankası ----------
export interface PartCatalogAttachment {
  id: number
  part_id: number
  kind: 'image' | 'pdf' | 'video' | 'cad' | 'file' | 'link'
  label: string
  filename: string
  original_name: string
  url: string
  created_at: string
}

// Bir parçanın (ör. "Körük") tamirinde/bakımında kullanılan malzeme satırı.
// material_part_id doluysa katalogdan seçilmiş, stok takipli bir malzeme —
// "Tamir Edildi" denince stoğu otomatik düşer. Boşsa free_text (serbest
// metin) doludur — yalnızca bilgi amaçlıdır, stok bağlantısı yoktur.
export interface PartRepairMaterial {
  id: number
  part_id: number
  material_part_id: number | null
  material_name: string
  material_stock_qty: number | null
  material_unit: string
  free_text: string
  qty: number
}

export interface PartRepairResult {
  ok: boolean
  materials_deducted: string[]
  materials_skipped: string[]
}

export interface PartCatalogItem {
  id: number
  name: string
  description: string
  image_filename: string
  order_index: number
  created_at: string
  parent_id: number | null
  attachments: PartCatalogAttachment[]
  children: PartCatalogItem[]
  // Parça stok takibi — min_stock=0 stok takibi tanımlanmadığı anlamına
  // gelir (Bildirim Merkezi'nde düşük stok uyarısı üretilmez).
  stock_qty: number
  min_stock: number
  unit: string
}

export interface BackupInfo {
  filename: string
  size_bytes: number
  created_at: string
}

export interface BackupRunResult {
  ok: boolean
  filename: string | null
  message: string
}

// ---------- Yetki Yönetimi ----------
export type PermissionKey =
  | 'view_maintenance'
  | 'edit_maintenance'
  | 'delete_maintenance'
  | 'approve_maintenance'
  | 'start_maintenance'
  | 'create_maintenance'
  | 'delete_all_maintenance'
  | 'export_excel'
  | 'manage_users'
  | 'view_dashboard_analytics'

export const PERMISSION_KEYS: PermissionKey[] = [
  'view_maintenance',
  'edit_maintenance',
  'delete_maintenance',
  'approve_maintenance',
  'start_maintenance',
  'create_maintenance',
  'delete_all_maintenance',
  'export_excel',
  'manage_users',
  'view_dashboard_analytics',
]

export const PERMISSION_TR: Record<PermissionKey, string> = {
  view_maintenance: 'Bakımı görüntüleyebilir',
  edit_maintenance: 'Düzenleyebilir',
  delete_maintenance: 'Silebilir',
  approve_maintenance: 'Onaylayabilir',
  start_maintenance: 'Aday iş emrini başlatabilir',
  create_maintenance: 'Elle bakım iş emri açabilir',
  delete_all_maintenance: 'Tüm bakım geçmişini silebilir',
  export_excel: 'Excel aktarabilir',
  manage_users: 'Kullanıcı ekleyebilir',
  view_dashboard_analytics: 'Dashboard analiz bölümünü görebilir',
}

// Yetki Yönetimi sayfasında kategorilere göre gruplamak/açıklama göstermek
// için — bkz. backend services.get_permission_defs.
export interface PermissionDef {
  key: string
  category: string
  description: string
}

export interface PermissionsMatrix {
  engineer: Record<string, boolean>
  technician: Record<string, boolean>
  amir: Record<string, boolean>
}

export interface MyPermissions {
  role: string
  permissions: Record<string, boolean>
}

// ---------- Global Arama ----------
export interface SearchHit {
  type: 'vehicle' | 'maintenance' | 'plan' | 'part' | 'user' | 'pdf'
  ref_id: number | null
  title: string
  subtitle: string
  url: string
}

export interface SearchResults {
  query: string
  total: number
  vehicles: SearchHit[]
  maintenances: SearchHit[]
  plans: SearchHit[]
  parts: SearchHit[]
  users: SearchHit[]
  documents: SearchHit[]
}

// ---------- Bildirim Merkezi ----------
export interface NotificationItem {
  id: number
  category: 'work_order' | 'completed' | 'unlock' | 'user' | 'overdue' | 'suggestion' | 'low_stock'
  title: string
  detail: string
  created_at: string
  maintenance_id: number | null
}

// ---------- İyileştirme Talepleri (araç modeli + tek aşamalı onay) ----------
// Kurumsal adlandırma "İyileştirme Talebi"dir; API yolu, tablo ve tip adları
// geriye dönük uyumluluk için 'suggestion' olarak kalmıştır.
export interface Suggestion {
  id: number
  // Geriye dönük uyumluluk: bu özellikten önce belirli bir araca bağlı
  // oluşturulmuş eski öneriler için dolu; yeni (model bazlı) önerilerde null.
  vehicle: Vehicle | null
  // Yeni önerilerde dolu — hedeflenen araç modeli (Bozankaya/Sirio vb.).
  vehicle_model: string
  plan: Plan | null
  text: string
  // Faz 31 — onay TEK aşamalı: pending (karar bekliyor) · approved · rejected.
  // 'pending_engineer' ve 'added' yalnızca Faz 30'daki iki aşamalı zincirden
  // kalan ESKİ kayıtlarda görülebilir; yeni önerilerde hiç üretilmez.
  status: 'pending' | 'pending_engineer' | 'rejected' | 'approved' | 'added'
  admin_note: string
  created_by: User | null
  created_at: string
  reviewed_by: User | null
  reviewed_at: string | null
  // 1. aşama — amir
  amir_status: 'pending' | 'approved' | 'rejected'
  amir_reviewed_by: User | null
  amir_reviewed_at: string | null
  amir_note: string
  // 2. aşama — mühendis
  engineer_status: 'pending' | 'approved' | 'rejected' | 'added'
  engineer_reviewed_by: User | null
  engineer_reviewed_at: string | null
  engineer_note: string
}

export const SUGGESTION_STATUS_TR: Record<Suggestion['status'], string> = {
  pending: 'Beklemede',
  // Eski kayıtlar da tek bir "Beklemede" olarak görünür (zincir kalktı).
  pending_engineer: 'Beklemede',
  rejected: 'Reddedildi',
  approved: 'Onaylandı',
  // Artık işaretlenemez — yalnızca eski kayıtların etiketi.
  added: 'Ağaca Eklendi',
}

// ---------- Araç Profil Sayfası ----------
export interface VehicleAttachment {
  id: number
  vehicle_id: number
  kind: 'image' | 'pdf' | 'video' | 'cad' | 'file' | 'link'
  label: string
  filename: string
  original_name: string
  url: string
  created_at: string
}

export interface VehicleProfile {
  vehicle: Vehicle
  total_maintenances: number
  completed_count: number
  open_count: number
  pending_count: number
  in_progress_count: number
  last_completed: Maintenance | null
  next_upcoming: UpcomingItem | null
  last_note: MaintenanceNote | null
  recent: Maintenance[]
  attachments: VehicleAttachment[]
}

// ---------- Faz 18: KPI ----------
export interface KPICompletion {
  opened_in_range: number
  completed_of_opened: number
  completion_rate_pct: number
  completed_in_range: number
  avg_completion_days: number | null
  // Zamanında tamamlanan bakım oranı: (100 * toleransta tamamlanan) /
  // (periyodu belirsiz olmayan, değerlendirilen TAMAMLANMIŞ bakım). Km
  // bazlı planlarda bakımın gerçekten yapıldığı km (completed_km) planın
  // tolerans penceresi içinde mi, süre bazlı planlarda completed_at <=
  // target_date mi diye bakılır — bkz. backend services.compute_kpi_report.
  on_time_count: number
  on_time_evaluated: number
  on_time_rate_pct: number | null
}

export interface KPICritical {
  // Her zaman GÜNCEL açık iş emirlerine göredir, tarih aralığından bağımsız.
  open_total: number
  urgent_count: number
  very_critical_count: number
  overdue_count: number
}

export interface KPIVehicleModelRow {
  vehicle_model: string
  opened: number
  completed: number
  open_now: number
}

export interface KPIVehicleRow {
  vehicle: Vehicle
  opened: number
  completed: number
  open_now: number
}

export interface KPIPersonRow {
  user: User
  items_completed_in_range: number
  maintenances_completed_in_range: number
  avg_completion_days: number | null
  shift_name: string | null
}

export interface KPIShiftRow {
  shift_name: string
  items_completed_in_range: number
}

export interface KPIReport {
  date_from: string | null
  date_to: string | null
  completion: KPICompletion
  critical: KPICritical
  by_vehicle_model: KPIVehicleModelRow[]
  top_vehicles: KPIVehicleRow[]
  by_person: KPIPersonRow[]
  by_shift: KPIShiftRow[]
}
