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
}

export interface ShiftModeConfig {
  enabled: boolean
}

export interface Vehicle {
  id: number
  code: string
  name: string
  current_km: number
  is_active: boolean
  manufacturer: string
  model: string
  chassis_no: string
  notes: string
  last_km_update_at: string | null
  production_year: number | null
  photo_filename: string
}

export interface Plan {
  id: number
  name: string
  interval_km: number
  lower_pct: number
  upper_pct: number
  is_active: boolean
  vehicle_model?: string
}

export interface NodeAttachment {
  id: number
  node_id: number
  kind: 'image' | 'pdf' | 'file' | 'link'
  label: string
  filename: string
  original_name: string
  url: string
  created_at: string
}

export interface TreeNode {
  id: number
  parent_id: number | null
  plan_id: number | null
  name: string
  order_index: number
  note: string
  children: TreeNode[]
  attachments: NodeAttachment[]
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
  note: string
  attachments: NodeAttachment[]
  tier_kms: number[]
  exec_note: string
  exec_note_by: User | null
  exec_note_at: string | null
  children: Item[]
}

export interface MaintenanceNote {
  id: number
  text: string
  created_at: string
  author: User | null
}

export interface Maintenance {
  id: number
  target_km: number
  km_at_open: number
  status: 'pending' | 'in_progress' | 'completed'
  created_at: string
  started_at: string | null
  completed_at: string | null
  vehicle: Vehicle
  plan: Plan
  assigned_to: User | null
  items?: Item[]
  covered_tiers?: number[]
  notes?: MaintenanceNote[]
}

export interface UpcomingItem {
  vehicle: Vehicle
  plan_name: string
  target_km: number
  remaining_km: number
  covered_tiers?: number[]
  estimated_days?: number | null
  urgent?: boolean
  critical?: boolean
  maintenance_id?: number | null
}

export interface CalendarProjection {
  vehicle: Vehicle
  plan_name: string
  target_km: number
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
  active_vehicle_count: number
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
}

export interface ImportTreeResult {
  plans: Plan[]
  nodes_created: number
  maintenances_reset: number
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
  active_vehicles: number
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
  | 'export_excel'
  | 'manage_users'

export const PERMISSION_KEYS: PermissionKey[] = [
  'view_maintenance',
  'edit_maintenance',
  'delete_maintenance',
  'approve_maintenance',
  'export_excel',
  'manage_users',
]

export const PERMISSION_TR: Record<PermissionKey, string> = {
  view_maintenance: 'Bakımı görüntüleyebilir',
  edit_maintenance: 'Düzenleyebilir',
  delete_maintenance: 'Silebilir',
  approve_maintenance: 'Onaylayabilir',
  export_excel: 'Excel aktarabilir',
  manage_users: 'Kullanıcı ekleyebilir',
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

// ---------- Canlı Alarm Merkezi ----------
export type AlarmPriority = 'low' | 'normal' | 'high' | 'critical'

export interface AlarmState {
  id: number
  priority: AlarmPriority
  acknowledged: boolean
  acknowledged_by: User | null
  acknowledged_at: string | null
  assigned_to: User | null
  resolved: boolean
  resolved_at: string | null
  closed: boolean
  closed_at: string | null
}

export interface AlarmItem {
  level: 'critical' | 'warning' | 'normal'
  vehicle: Vehicle
  plan_name: string
  target_km: number
  remaining_km: number
  estimated_days: number | null
  overdue: boolean
  maintenance_id: number | null
  age_days: number | null
  message: string
  state: AlarmState | null
}

export interface AlarmCenter {
  critical: number
  warning: number
  normal: number
  items: AlarmItem[]
}

export const ALARM_LEVEL_TR: Record<AlarmItem['level'], string> = {
  critical: 'Kritik',
  warning: 'Uyarı',
  normal: 'Normal',
}

export const ALARM_PRIORITY_TR: Record<AlarmPriority, string> = {
  low: 'Düşük',
  normal: 'Normal',
  high: 'Yüksek',
  critical: 'Kritik',
}

// ---------- Bildirim Merkezi ----------
export interface NotificationItem {
  id: number
  category: 'work_order' | 'completed' | 'unlock' | 'user' | 'overdue' | 'suggestion'
  title: string
  detail: string
  created_at: string
  maintenance_id: number | null
}

// ---------- Öneriler ----------
export interface Suggestion {
  id: number
  vehicle: Vehicle
  plan: Plan | null
  text: string
  status: 'pending' | 'approved' | 'rejected' | 'added'
  admin_note: string
  created_by: User | null
  created_at: string
  reviewed_by: User | null
  reviewed_at: string | null
}

export const SUGGESTION_STATUS_TR: Record<Suggestion['status'], string> = {
  pending: 'Beklemede',
  approved: 'Onaylandı',
  rejected: 'Reddedildi',
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
