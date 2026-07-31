import { useEffect, useMemo, useRef, useState } from 'react'
import { api, apiDownload, apiUpload, apiUploadWithFields } from '../api'
import type { KmSimConfig, KmSimRunResult, Maintenance, UpcomingItem, User, Vehicle, VehicleAttachment, VehicleProfile } from '../types'
import { STATUS_TR } from '../types'
import { PageHeader, Badge, SearchInput, EmptyState, SkeletonTableRows, StatCard } from '../components/ui'
import { showToast } from '../components/toast'
import { useLiveEvent } from '../liveEvents'
import { targetLabel } from '../format'
import {
  ArrowLeft,
  Truck,
  Upload,
  Download,
  PlusCircle,
  AlertTriangle,
  Gauge,
  Play,
  Camera,
  Trash2,
  Wrench,
  CheckCircle2,
  Clock,
  StickyNote,
  Image as ImageIcon,
  FileText,
  Box,
  Video,
  Link as LinkIcon,
  FolderOpen,
  QrCode,
  Star,
  Printer,
} from 'lucide-react'

function vehicleAttIcon(kind: string) {
  if (kind === 'image') return <ImageIcon size={14} />
  if (kind === 'pdf') return <FileText size={14} />
  if (kind === 'video') return <Video size={14} />
  if (kind === 'cad') return <Box size={14} />
  if (kind === 'link') return <LinkIcon size={14} />
  return <Download size={14} />
}

// ------------------------------------------------- Favori / son açılan araçlar
const FAV_VEHICLES_KEY = 'ulasim_bakim_fav_vehicle_ids'
const RECENT_VEHICLES_KEY = 'ulasim_bakim_recent_vehicle_ids'
const RECENT_VEHICLES_MAX = 6

function readIdList(key: string): number[] {
  try {
    const raw = localStorage.getItem(key)
    const arr = raw ? JSON.parse(raw) : []
    return Array.isArray(arr) ? arr.filter((n) => typeof n === 'number') : []
  } catch {
    return []
  }
}
function writeIdList(key: string, ids: number[]) {
  localStorage.setItem(key, JSON.stringify(ids))
}

const fmtKmN = (n: number) => n.toLocaleString('tr-TR')
// Son otomatik günden (cfg.last_date, YYYY-MM-DD) bugüne kadar kaç takvim
// otomatik güncelleme tam olarak kaçırılan gün kadar ilerlesin.
function daysSinceLast(dateStr: string | null): number {
  if (!dateStr) return 0
  const last = new Date(`${dateStr}T00:00:00`)
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const diff = Math.round((today.getTime() - last.getTime()) / 86400000)
  return Math.max(0, diff)
}
// Hedef km zaten geçildiyse (remaining <= 0) "X km geçti" göster —
// negatif/garip büyük bir sayı yerine anlaşılır bir ifade.
const kmDisplayV = (remaining: number) =>
  remaining <= 0 ? `${fmtKmN(Math.abs(remaining))} km geçti` : `${fmtKmN(remaining)} km kaldı`

// ---------------------------------------------------------- Günlük KM simülasyonu paneli (Admin)
function KmSimCard({ onChanged }: { onChanged: () => void }) {
  const [cfg, setCfg] = useState<KmSimConfig | null>(null)
  const [minKm, setMinKm] = useState('300')
  const [maxKm, setMaxKm] = useState('330')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [error, setError] = useState('')

  const load = () => {
    api<KmSimConfig>('/km-sim')
      .then((c) => {
        setCfg(c)
        setMinKm(String(c.min_km))
        setMaxKm(String(c.max_km))
      })
      .catch(() => setCfg(null))
  }
  useEffect(load, [])

  async function saveSettings(next: Partial<{ enabled: boolean; min_km: number; max_km: number }>) {
    setError('')
    try {
      const c = await api<KmSimConfig>('/km-sim', { method: 'PATCH', body: next })
      setCfg(c)
      setMinKm(String(c.min_km))
      setMaxKm(String(c.max_km))
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function run(days: number) {
    setBusy(true)
    setError('')
    setMsg('')
    try {
      const r = await api<KmSimRunResult>('/km-sim/run', { method: 'POST', body: { days } })
      setMsg(
        `${r.days} gün ilerletildi: ${r.vehicles_updated} araç, toplam +${fmtKmN(r.total_km_added)} km, ` +
          `${r.maintenances_opened} bakım açıldı.`,
      )
      load()
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (!cfg) return null

  const pendingDays = daysSinceLast(cfg.last_date)

  return (
    <div className="card">
      <div className="card-head">
        <div>
          <div className="card-title">
            <Gauge size={15} style={{ verticalAlign: -2, marginRight: 6 }} />
            Otomatik KM Simülasyonu
          </div>
          <div className="card-subtitle">Aktif araç kilometreleri günlük otomatik güncellenir.</div>
        </div>
      </div>

      <div className="row" style={{ flexWrap: 'wrap', gap: 10 }}>
        <label className="row" style={{ gap: 6 }}>
          <input
            type="checkbox"
            checked={cfg.enabled}
            onChange={(e) => saveSettings({ enabled: e.target.checked })}
          />
          <span>{cfg.enabled ? 'Otomatik güncelleme açık' : 'Otomatik güncelleme kapalı'}</span>
        </label>
        <span className="filter-inline">
          <span>Günlük km</span>
          <input type="number" value={minKm} onChange={(e) => setMinKm(e.target.value)} style={{ width: 80 }} />
          <span>–</span>
          <input type="number" value={maxKm} onChange={(e) => setMaxKm(e.target.value)} style={{ width: 80 }} />
          <button
            className="small"
            onClick={() => saveSettings({ min_km: Number(minKm) || 0, max_km: Number(maxKm) || 0 })}
          >
            Kaydet
          </button>
        </span>
      </div>

      <div className="row" style={{ marginTop: 12, flexWrap: 'wrap', gap: 8 }}>
        <button
          className="small primary"
          disabled={busy || pendingDays <= 0}
          onClick={() => run(pendingDays)}
          title={
            pendingDays > 0
              ? `Son güncellemeden bugüne ${pendingDays} gün geçti; bu kadar ilerletilecek.`
              : 'Son güncelleme bugüne ait, ilerletilecek gün yok.'
          }
        >
          <Play size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
          {pendingDays > 0 ? `Otomatik Güncelle (+${pendingDays} gün)` : 'Güncel'}
        </button>
        <span className="muted" style={{ marginLeft: 'auto' }}>
          {cfg.vehicle_count} araç
          {cfg.last_date && <> · Son otomatik gün: {cfg.last_date}</>}
        </span>
      </div>

      {msg && <div className="muted" style={{ marginTop: 8 }}>{msg}</div>}
      {error && <div className="error" style={{ marginTop: 8 }}>{error}</div>}
    </div>
  )
}

const fmtKm = (n: number) => n.toLocaleString('tr-TR')
const fmt = (s: string | null) => (s ? new Date(s).toLocaleString('tr-TR') : '—')
// Süre bazlı (aylık/yıllık) maddelerde remaining_km alanı gün sayısıdır ve
// target_km backend'de gizlenmiş (null) olabilir — target_date varsa buna göre göster.
const remainingDisplayV = (u: UpcomingItem) =>
  u.target_date
    ? u.remaining_km <= 0
      ? `${Math.abs(u.remaining_km)} gün geçti`
      : `${u.remaining_km} gün kaldı`
    : kmDisplayV(u.remaining_km)
const hedefLabelV = (m: Maintenance) => targetLabel(m.target_km, m.target_date, m.vehicle.model)

// ---------------------------------------------------------- Araç Profil Sayfası
function InfoField({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="vm-label">{label}</div>
      <div className="vm-value">{value ?? '—'}</div>
    </div>
  )
}

function VehicleDetail({
  vehicle,
  onBack,
  onOpenMaintenance,
  onChanged,
}: {
  vehicle: Vehicle
  onBack: () => void
  onOpenMaintenance: (id: number) => void
  onChanged: () => void
}) {
  const [profile, setProfile] = useState<VehicleProfile | null>(null)
  const [history, setHistory] = useState<Maintenance[] | null>(null)
  const [photoBusy, setPhotoBusy] = useState(false)
  const [error, setError] = useState('')
  const photoRef = useRef<HTMLInputElement>(null)

  // Araç belgeleri: PDF, servis manueli, fotoğraf, CAD/STEP, elektrik şeması
  // veya harici bağlantı — Parça Bilgi Bankası'ndaki ek sistemiyle aynı desen.
  const [docFile, setDocFile] = useState<File | null>(null)
  const [docLabel, setDocLabel] = useState('')
  const [docLinkLabel, setDocLinkLabel] = useState('')
  const [docLinkUrl, setDocLinkUrl] = useState('')
  const [docBusy, setDocBusy] = useState(false)
  const docFileRef = useRef<HTMLInputElement>(null)

  const load = () => {
    api<VehicleProfile>(`/vehicles/${vehicle.id}/profile`)
      .then(setProfile)
      .catch(() => setProfile(null))
    api<Maintenance[]>(`/maintenances?vehicle_id=${vehicle.id}`)
      .then(setHistory)
      .catch(() => setHistory([]))
  }
  useEffect(load, [vehicle.id])
  // Bu araca ait bir bakım başka bir sayfada (Bakımlar) tamamlanır/açılırsa
  // profil burada açıkken de otomatik tazelensin — aksi halde "Yaklaşan
  // Bakım"/geçmiş, kullanıcı sayfayı yeniden açana kadar bayat kalır.
  useLiveEvent(['maintenance', 'vehicle'], load)

  const v = profile?.vehicle ?? vehicle

  async function uploadPhoto(file: File) {
    setPhotoBusy(true)
    setError('')
    try {
      await apiUpload(`/vehicles/${vehicle.id}/photo`, file)
      load()
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setPhotoBusy(false)
      if (photoRef.current) photoRef.current.value = ''
    }
  }

  async function removePhoto() {
    if (!confirm('Araç fotoğrafı kaldırılsın mı?')) return
    setPhotoBusy(true)
    setError('')
    try {
      await api(`/vehicles/${vehicle.id}/photo`, { method: 'DELETE' })
      load()
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setPhotoBusy(false)
    }
  }

  async function uploadDoc(e: React.FormEvent) {
    e.preventDefault()
    if (!docFile) return
    setDocBusy(true)
    setError('')
    try {
      await apiUploadWithFields(`/vehicles/${vehicle.id}/attachments`, docFile, { label: docLabel })
      setDocFile(null)
      setDocLabel('')
      if (docFileRef.current) docFileRef.current.value = ''
      load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setDocBusy(false)
    }
  }

  async function addDocLink(e: React.FormEvent) {
    e.preventDefault()
    if (!docLinkUrl.trim()) return
    setDocBusy(true)
    setError('')
    try {
      await api(`/vehicles/${vehicle.id}/attachment-link`, {
        method: 'POST',
        body: { label: docLinkLabel, url: docLinkUrl.trim() },
      })
      setDocLinkLabel('')
      setDocLinkUrl('')
      load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setDocBusy(false)
    }
  }

  async function removeDoc(a: VehicleAttachment) {
    if (!confirm('Bu belge silinsin mi?')) return
    await api(`/vehicle-attachments/${a.id}`, { method: 'DELETE' })
    load()
  }

  return (
    <div className="fade-in">
      <div className="row" style={{ marginBottom: 14, justifyContent: 'space-between' }}>
        <button className="ghost" onClick={onBack} style={{ paddingLeft: 8 }}>
          <ArrowLeft size={14} style={{ marginRight: 6, verticalAlign: -2 }} /> Araç Listesi
        </button>
        <button className="small ghost" onClick={() => window.print()} title="Bu sayfayı yazdır">
          <Printer size={13} style={{ marginRight: 5, verticalAlign: -2 }} /> Yazdır
        </button>
      </div>
      <div className="print-only" style={{ marginBottom: 10, fontSize: 12, color: '#555' }}>
        Ulaşım Bakım — {v.code} {v.name ? `(${v.name})` : ''} · Yazdırma tarihi: {new Date().toLocaleString('tr-TR')}
      </div>

      {/* Profil başlığı: fotoğraf + kimlik bilgileri */}
      <div className="card">
        <div className="vprofile-head">
          <div className="vprofile-photo">
            {v.photo_filename ? (
              <img src={`/uploads/vehicles/${v.photo_filename}`} alt={v.code} />
            ) : (
              <span className="vprofile-photo-empty">
                <ImageIcon size={26} />
                Fotoğraf yok
              </span>
            )}
          </div>
          <div className="vprofile-info">
            <div className="row">
              <h1 style={{ marginBottom: 0, fontSize: 22 }}>
                <span className="vehicle-code">{v.code}</span>
                {v.name && <span className="muted"> — {v.name}</span>}
              </h1>
            </div>
            <div className="vprofile-meta">
              <InfoField label="Anlık KM" value={<strong>{fmtKm(v.current_km)} km</strong>} />
              <InfoField label="Üretici" value={v.manufacturer || '—'} />
              <InfoField label="Model" value={v.model || '—'} />
              <InfoField label="Üretim Yılı" value={v.production_year ?? '—'} />
              <InfoField
                label="Hizmete Başlama Tarihi"
                value={
                  v.commissioning_date
                    ? new Date(v.commissioning_date).toLocaleDateString('tr-TR')
                    : '—'
                }
              />
              <InfoField label="Son KM Güncelleme" value={fmt(v.last_km_update_at)} />
            </div>
            <div className="row" style={{ gap: 8, marginTop: 14 }}>
              <button className="small" disabled={photoBusy} onClick={() => photoRef.current?.click()}>
                <Camera size={13} style={{ marginRight: 5, verticalAlign: -2 }} />
                {photoBusy ? 'Yükleniyor…' : v.photo_filename ? 'Fotoğrafı Değiştir' : 'Fotoğraf Ekle'}
              </button>
              {v.photo_filename && (
                <button className="small danger" disabled={photoBusy} onClick={removePhoto}>
                  <Trash2 size={13} style={{ marginRight: 5, verticalAlign: -2 }} /> Kaldır
                </button>
              )}
              <input
                ref={photoRef}
                type="file"
                accept="image/*"
                capture="environment"
                style={{ display: 'none' }}
                onChange={(e) => e.target.files?.[0] && uploadPhoto(e.target.files[0])}
              />
            </div>
            {v.notes && <div className="muted" style={{ marginTop: 10 }}>Not: {v.notes}</div>}
            {error && <div className="error" style={{ marginTop: 8 }}>{error}</div>}
          </div>
          <div style={{ flexShrink: 0, textAlign: 'center' }}>
            <img
              src={`/api/vehicles/${v.id}/qrcode`}
              alt="QR kod"
              width={84}
              height={84}
              style={{ borderRadius: 8, border: '1px solid var(--border)' }}
            />
            <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>Okutunca bakım geçmişi açılır</div>
            <a
              href={`/api/vehicles/${v.id}/qrcode?download=true`}
              className="small ghost no-print"
              style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4, marginTop: 6 }}
              download
            >
              <QrCode size={12} />
              İndir
            </a>
          </div>
        </div>
      </div>

      {/* Özet istatistikler */}
      <div className="vprofile-stats">
        <StatCard label="Toplam Bakım" value={profile?.total_maintenances ?? '—'} icon={<Wrench size={16} />} />
        <StatCard label="Tamamlanan" value={profile?.completed_count ?? '—'} tone="completed" icon={<CheckCircle2 size={16} />} />
        <StatCard label="Açık İş Emri" value={profile?.open_count ?? '—'} tone="pending" icon={<Clock size={16} />} />
        <StatCard
          label="Yaklaşan Bakım"
          value={
            profile?.next_upcoming
              ? remainingDisplayV(profile.next_upcoming)
              : 'Yok'
          }
          tone={
            profile?.next_upcoming?.status === 'gecikti'
              ? 'danger'
              : profile?.next_upcoming?.status === 'planlanacak'
                ? 'pending'
                : profile?.next_upcoming?.status === 'planlandi'
                  ? 'in_progress'
                  : undefined
          }
          icon={<AlertTriangle size={16} />}
        />
      </div>

      {/* Son bakım / son not (arıza) özet kartları */}
      <div className="grid grid-2">
        <div className="card">
          <div className="card-title" style={{ marginBottom: 8 }}>
            <CheckCircle2 size={15} style={{ verticalAlign: -2, marginRight: 6 }} /> Son Tamamlanan Bakım
          </div>
          {profile?.last_completed ? (
            <div
              className="clickable-row"
              style={{ padding: 6, borderRadius: 8 }}
              onClick={() => onOpenMaintenance(profile.last_completed!.id)}
            >
              <div style={{ fontWeight: 600 }}>
                {profile.last_completed.plan.name} · {hedefLabelV(profile.last_completed)}
              </div>
              <div className="muted" style={{ fontSize: 12.5 }}>
                Bitiş: {fmt(profile.last_completed.completed_at)} · İş Emri #{profile.last_completed.id}
              </div>
            </div>
          ) : (
            <div className="muted">Henüz tamamlanmış bakım yok.</div>
          )}
        </div>
        <div className="card">
          <div className="card-title" style={{ marginBottom: 8 }}>
            <StickyNote size={15} style={{ verticalAlign: -2, marginRight: 6 }} /> Son Arıza / Bakım Notu
          </div>
          {profile?.last_note ? (
            <div>
              <div style={{ fontWeight: 500 }}>{profile.last_note.text}</div>
              <div className="muted" style={{ fontSize: 12.5, marginTop: 4 }}>
                {profile.last_note.author?.full_name ?? 'Bilinmiyor'} · {fmt(profile.last_note.created_at)}
              </div>
            </div>
          ) : (
            <div className="muted">Bu araç için kaydedilmiş bir not yok.</div>
          )}
        </div>
      </div>

      {/* Araç belgeleri: PDF, servis manueli, fotoğraf, CAD/STEP, elektrik şeması */}
      <div className="card">
        <h3 style={{ marginTop: 0, fontSize: 15, display: 'flex', alignItems: 'center', gap: 6 }}>
          <FolderOpen size={15} /> Belgeler
        </h3>
        <div className="muted" style={{ fontSize: 12.5, marginBottom: 12 }}>
          Bu araca ait PDF, servis manueli, fotoğraf, CAD/STEP mühendislik dosyası, elektrik şeması vb. belgeleri buradan yükleyip yönetebilirsiniz.
        </div>
        {(profile?.attachments ?? []).length === 0 && (
          <div className="muted" style={{ marginBottom: 12 }}>Henüz bu araca belge eklenmemiş.</div>
        )}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 12 }}>
          {(profile?.attachments ?? []).map((a) => {
            const href = a.kind === 'link' ? a.url : `/uploads/vehicles/${a.filename}`
            return (
              <div key={a.id} className="card" style={{ margin: 0, padding: 10 }}>
                <div className="row" style={{ gap: 6, marginBottom: 4 }}>
                  {vehicleAttIcon(a.kind)}
                  <a
                    href={href}
                    target="_blank"
                    rel="noreferrer"
                    style={{ fontWeight: 600, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                  >
                    {a.label || a.original_name || a.url || 'Belge'}
                  </a>
                  <button className="small danger" style={{ marginLeft: 'auto' }} onClick={() => removeDoc(a)} title="Belgeyi sil">
                    <Trash2 size={12} />
                  </button>
                </div>
                <div className="muted" style={{ fontSize: 11 }}>{fmt(a.created_at)}</div>
              </div>
            )
          })}
        </div>
        <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--border)' }}>
          <form className="row" onSubmit={uploadDoc} style={{ flexWrap: 'wrap', marginBottom: 10 }}>
            <input
              placeholder="Etiket (ör. Servis Manueli, Elektrik Şeması, CAD/STEP)"
              value={docLabel}
              onChange={(e) => setDocLabel(e.target.value)}
              style={{ minWidth: 240 }}
            />
            <input ref={docFileRef} type="file" onChange={(e) => setDocFile(e.target.files?.[0] ?? null)} />
            <button className="small primary" disabled={!docFile || docBusy}>
              Belge Ekle (PDF/foto/CAD/STEP)
            </button>
          </form>
          <form className="row" onSubmit={addDocLink} style={{ flexWrap: 'wrap' }}>
            <input placeholder="Bağlantı etiketi (ör. Bulut Doküman)" value={docLinkLabel} onChange={(e) => setDocLinkLabel(e.target.value)} />
            <input
              placeholder="https://…"
              value={docLinkUrl}
              onChange={(e) => setDocLinkUrl(e.target.value)}
              style={{ minWidth: 240 }}
            />
            <button className="small" disabled={!docLinkUrl.trim() || docBusy}>Bağlantı Ekle</button>
          </form>
        </div>
      </div>

      {/* Bakım geçmişi */}
      <div className="table-card" style={{ marginTop: 16 }}>
        <div className="table-toolbar"><div className="card-title">Bakım Geçmişi</div></div>
        {history === null && (
          <div className="table-scroll">
            <table><tbody><SkeletonTableRows columns={7} /></tbody></table>
          </div>
        )}
        {history !== null && history.length === 0 && (
          <EmptyState title="Bakım kaydı yok" description="Bu araç için henüz açılmış bir bakım kaydı bulunmuyor." />
        )}
        {history !== null && history.length > 0 && (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>İş Emri</th>
                  <th>Periyot</th>
                  <th>Hedef</th>
                  <th>Durum</th>
                  <th>Oluşturma</th>
                  <th>Bitiş</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {history.map((m) => (
                  <tr key={m.id} className="clickable-row" onClick={() => onOpenMaintenance(m.id)}>
                    <td style={{ fontWeight: 600 }}>#{m.id}</td>
                    <td>{m.plan.name}</td>
                    <td>{hedefLabelV(m)}</td>
                    <td><Badge tone={m.status}>{STATUS_TR[m.status]}</Badge></td>
                    <td>{fmt(m.created_at)}</td>
                    <td>{fmt(m.completed_at)}</td>
                    <td>
                      <button className="small primary" onClick={(e) => { e.stopPropagation(); onOpenMaintenance(m.id) }}>
                        Aç
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------- Araç listesi
export default function VehiclesPage({
  user,
  perms,
  onOpenMaintenance,
  initialVehicleId = null,
}: {
  user: User
  perms: Record<string, boolean>
  onOpenMaintenance: (id: number) => void
  initialVehicleId?: number | null
}) {
  const [list, setList] = useState<Vehicle[] | null>(null)
  const [alerts, setAlerts] = useState<UpcomingItem[]>([])
  const [code, setCode] = useState('')
  const [name, setName] = useState('')
  const [km, setKm] = useState('')
  const [manufacturer, setManufacturer] = useState('')
  const [model, setModel] = useState('')
  const [productionYear, setProductionYear] = useState('')
  const [commissioningDate, setCommissioningDate] = useState('')
  const [notes, setNotes] = useState('')
  const [error, setError] = useState('')
  const [kmEdit, setKmEdit] = useState<Record<number, string>>({})
  const [importMsg, setImportMsg] = useState('')
  const [importBusy, setImportBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const [search, setSearch] = useState('')
  const [modelFilter, setModelFilter] = useState('')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [knownModels, setKnownModels] = useState<string[]>([])
  const [favIds, setFavIds] = useState<number[]>(() => readIdList(FAV_VEHICLES_KEY))
  const [recentIds, setRecentIds] = useState<number[]>(() => readIdList(RECENT_VEHICLES_KEY))

  function toggleFavorite(vehicleId: number) {
    setFavIds((prev) => {
      const next = prev.includes(vehicleId) ? prev.filter((id) => id !== vehicleId) : [vehicleId, ...prev]
      writeIdList(FAV_VEHICLES_KEY, next)
      return next
    })
  }

  // Bir araç profili her açıldığında (tablo satırına tıklama, global arama,
  // QR kod, "Son Açılanlar" çipi — hepsi selectedId'yi değiştirir) o aracı
  // "son açılanlar" listesinin başına taşır.
  useEffect(() => {
    if (selectedId == null) return
    setRecentIds((prev) => {
      const next = [selectedId, ...prev.filter((id) => id !== selectedId)].slice(0, RECENT_VEHICLES_MAX)
      writeIdList(RECENT_VEHICLES_KEY, next)
      return next
    })
  }, [selectedId])

  const load = () => {
    api<Vehicle[]>('/vehicles').then(setList).catch(() => setList([]))
    api<UpcomingItem[]>('/vehicles/alerts').then(setAlerts).catch(() => setAlerts([]))
    api<string[]>('/vehicle-models').then(setKnownModels).catch(() => setKnownModels([]))
  }
  useEffect(() => {
    load()
  }, [])
  // Bir bakım başka bir sayfada (Bakımlar) tamamlanınca/açılınca bu
  // listedeki "Çok Kritik/Yaklaşıyor" rozetleri ve araç listesi otomatik
  // güncellensin — önceden yalnızca sayfa ilk açıldığında yükleniyordu,
  // bu yüzden tamamlanan bir bakım Araçlar sayfasını etkilemiyordu (kullanıcı
  // manuel olarak sayfayı yeniden açana kadar eski/kritik rozet kalıyordu).
  useLiveEvent(['maintenance', 'vehicle'], load)

  // Global aramadan bir araç profili açılmak istendiğinde doğrudan o
  // aracın detayına geç. initialVehicleId null'a döndüğünde
  // (ör. Dashboard'daki "Araçlar"a normal menüden tekrar girildiğinde,
  // App.tsx'teki navigate() bunu temizler) burada da seçili aracı EXPLICIT
  // olarak null'a çekip listeye dönülür — sayfa yeniden mount edilmese
  // bile önceden açılmış bir araç profili "yapışık" kalmaz.
  useEffect(() => {
    setSelectedId(initialVehicleId)
  }, [initialVehicleId])

  const alertByVehicleId = useMemo(() => {
    const m = new Map<number, UpcomingItem>()
    for (const a of alerts) {
      const existing = m.get(a.vehicle.id)
      if (!existing || a.remaining_km < existing.remaining_km) m.set(a.vehicle.id, a)
    }
    return m
  }, [alerts])

  // Araçların "sonraki ilk bakım" durumu — 4 sabit kategori (kullanıcının
  // onayladığı tanım): Yaklaşıyor (<=5.000 km kaldı), Planlanacak (bakım
  // takip periyodu içinde ve henüz planlanmadı), Planlandı (aynı pencere
  // içinde ve zaten bir iş emri açılmış), Bakım gecikti (periyot aşılmış).
  // Bkz. backend services._classify_upcoming_status.
  const approachingCount = useMemo(
    () => Array.from(alertByVehicleId.values()).filter((a) => a.status === 'yaklasiyor').length,
    [alertByVehicleId],
  )
  const toPlanCount = useMemo(
    () => Array.from(alertByVehicleId.values()).filter((a) => a.status === 'planlanacak').length,
    [alertByVehicleId],
  )
  const plannedCount = useMemo(
    () => Array.from(alertByVehicleId.values()).filter((a) => a.status === 'planlandi').length,
    [alertByVehicleId],
  )
  const overdueCount = useMemo(
    () => Array.from(alertByVehicleId.values()).filter((a) => a.status === 'gecikti').length,
    [alertByVehicleId],
  )

  // Filoda kullanılan tipleri doğrudan araç kayıtlarından üret. Böylece Sirio,
  // Bozankaya ya da sonradan eklenen başka bir model için ayrıca tanım gerekmez.
  const vehicleModels = useMemo(() => {
    const counts = new Map<string, number>()
    for (const v of list ?? []) {
      const value = v.model.trim()
      if (value) counts.set(value, (counts.get(value) ?? 0) + 1)
    }
    return [...counts.entries()].sort(([a], [b]) => a.localeCompare(b, 'tr'))
  }, [list])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    const base = (list ?? []).filter((v) => {
      if (modelFilter && v.model !== modelFilter) return false
      if (q && !(
        v.code.toLowerCase().includes(q) ||
        v.name.toLowerCase().includes(q) ||
        v.manufacturer.toLowerCase().includes(q) ||
        v.model.toLowerCase().includes(q)
      )) return false
      return true
    })
    // Favori araçlar her zaman listenin en üstünde — mühendis sürekli aynı
    return [...base].sort((a, b) => Number(favIds.includes(b.id)) - Number(favIds.includes(a.id)))
  }, [list, search, modelFilter, favIds])

  const recentVehicles = useMemo(
    () => recentIds.map((id) => (list ?? []).find((v) => v.id === id)).filter((v): v is Vehicle => !!v),
    [recentIds, list],
  )

  async function add(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    try {
      await api('/vehicles', {
        method: 'POST',
        body: {
          code,
          name,
          current_km: Number(km) || 0,
          manufacturer,
          model,
          production_year: productionYear ? Number(productionYear) : null,
          commissioning_date: commissioningDate || null,
          notes,
        },
      })
      setCode('')
      setName('')
      setKm('')
      setManufacturer('')
      setModel('')
      setProductionYear('')
      setCommissioningDate('')
      setNotes('')
      load()
      showToast('✓ Araç eklendi')
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function updateKm(v: Vehicle) {
    const value = Number(kmEdit[v.id])
    if (!value) return
    setError('')
    try {
      await api(`/vehicles/${v.id}/km`, { method: 'PUT', body: { current_km: value } })
      setKmEdit((s) => ({ ...s, [v.id]: '' }))
      load()
      showToast('✓ KM güncellendi')
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function removeVehicle(v: Vehicle) {
    if (
      !confirm(
        `"${v.code}" aracı ve bu araca ait tüm bakım geçmişi kalıcı olarak silinecek. Bu işlem geri alınamaz. Devam edilsin mi?`,
      )
    )
      return
    setError('')
    try {
      await api(`/vehicles/${v.id}`, { method: 'DELETE' })
      load()
      showToast(`"${v.code}" silindi`, 'info')
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function handleImportFile(file: File) {
    setImportBusy(true)
    setImportMsg('')
    setError('')
    try {
      const r = await apiUpload<{ created: number; updated: number; errors: string[] }>('/vehicles/import', file)
      setImportMsg(
        `${r.created} araç eklendi, ${r.updated} araç güncellendi.` +
          (r.errors.length ? ` ${r.errors.length} satırda uyarı: ${r.errors.join('; ')}` : ''),
      )
      load()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setImportBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  async function handleExport() {
    setError('')
    try {
      await apiDownload('/vehicles/export', 'araclar.xlsx')
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const selected = selectedId !== null ? (list ?? []).find((v) => v.id === selectedId) : undefined
  if (selected) {
    return (
      <VehicleDetail
        vehicle={selected}
        onBack={() => setSelectedId(null)}
        onOpenMaintenance={onOpenMaintenance}
        onChanged={load}
      />
    )
  }

  return (
    <>
      <PageHeader
        title="Araçlar"
      />

      {user.role === 'admin' && <KmSimCard onChanged={load} />}

      <div className="grid grid-2">
        <div className="card">
          <div className="card-head">
            <div>
              <div className="card-title">Excel ile İçe/Dışa Aktarma</div>
              <div className="card-subtitle">Kod, KM, Üretici, Model, Not sütunları desteklenir.</div>
            </div>
          </div>
          <div className="row">
            <label className="row" style={{ gap: 8 }}>
              <button type="button" onClick={() => fileRef.current?.click()} disabled={importBusy}>
                <Upload size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
                {importBusy ? 'Yükleniyor…' : 'Excel Yükle'}
              </button>
              <input
                ref={fileRef}
                type="file"
                accept=".xlsx"
                disabled={importBusy}
                style={{ display: 'none' }}
                onChange={(e) => e.target.files?.[0] && handleImportFile(e.target.files[0])}
              />
            </label>
            {perms.export_excel && (
              <button onClick={handleExport} title="Tüm araçlar indirilir">
                <Download size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
                Excel'e Aktar
              </button>
            )}
          </div>
          {importMsg && <div className="muted" style={{ marginTop: 8 }}>{importMsg}</div>}
        </div>

        <div className="card">
          <div className="card-head">
            <div>
              <div className="card-title">Yeni Araç Ekle</div>
              <div className="card-subtitle">Kod formatı: 3801, 3802, … 3880</div>
            </div>
          </div>
          <form className="row" onSubmit={add} style={{ flexWrap: 'wrap' }}>
            <input placeholder="Araç kodu (ör. 3803)" value={code} onChange={(e) => setCode(e.target.value)} required />
            <input placeholder="Araç adı" value={name} onChange={(e) => setName(e.target.value)} />
            <input placeholder="Mevcut KM" type="number" value={km} onChange={(e) => setKm(e.target.value)} />
            <input placeholder="Üretici" value={manufacturer} onChange={(e) => setManufacturer(e.target.value)} />
            <input
              placeholder="Model"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              list="vehicle-known-models"
              title="Bakım Ağacı sayfasında bu modele özel bir Excel yüklediyseniz, buraya birebir aynı model adını yazın."
            />
            <datalist id="vehicle-known-models">
              {knownModels.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
            <input
              placeholder="Üretim yılı"
              type="number"
              value={productionYear}
              onChange={(e) => setProductionYear(e.target.value)}
              style={{ width: 120 }}
            />
            <input
              type="date"
              value={commissioningDate}
              onChange={(e) => setCommissioningDate(e.target.value)}
              title="Hizmete başlama tarihi — aylık/yıllık (süre bazlı) bakımların hesabında baz alınır. Boş bırakılırsa aracın eklendiği tarih kullanılır."
              style={{ width: 160 }}
            />
            <input placeholder="Not" value={notes} onChange={(e) => setNotes(e.target.value)} style={{ minWidth: 220 }} />
            <button className="primary">
              <PlusCircle size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
              Ekle
            </button>
          </form>
        </div>
      </div>
      {error && <div className="error">{error}</div>}

      {recentVehicles.length > 0 && (
        <div className="recent-vehicles-row fade-in">
          <span className="label">Son Açılanlar:</span>
          {recentVehicles.map((v) => (
            <button key={v.id} className="recent-vehicle-chip" onClick={() => setSelectedId(v.id)}>
              {v.code}
            </button>
          ))}
        </div>
      )}

      <div className="table-card fade-in vehicle-list-card">
        <div className="table-toolbar vehicle-toolbar">
          <SearchInput value={search} onChange={setSearch} placeholder="Kod, ad, üretici veya model ara…" />
          <select
            className="vehicle-model-filter"
            value={modelFilter}
            onChange={(e) => setModelFilter(e.target.value)}
            aria-label="Araç tipi filtrele"
          >
            <option value="">Tüm araç tipleri</option>
            {vehicleModels.map(([modelName, count]) => (
              <option key={modelName} value={modelName}>{modelName} ({count})</option>
            ))}
          </select>
          {modelFilter && (
            <button className="small ghost" onClick={() => setModelFilter('')}>
              Tip filtresini temizle
            </button>
          )}
          {approachingCount > 0 && (
            <span className="alert-pill">
              <AlertTriangle size={12} /> {approachingCount} araçta bakım yaklaşıyor (&le;5.000 km)
            </span>
          )}
          {toPlanCount > 0 && (
            <span className="toplan-pill">
              <AlertTriangle size={12} /> {toPlanCount} araç planlanacak (bakım takip periyodunda, henüz planlanmadı)
            </span>
          )}
          {plannedCount > 0 && (
            <span className="planned-pill">
              <AlertTriangle size={12} /> {plannedCount} araç planlandı (iş emri açıldı)
            </span>
          )}
          {overdueCount > 0 && (
            <span className="critical-pill">
              <AlertTriangle size={12} /> {overdueCount} araçta bakım gecikti
            </span>
          )}
          <span className="muted" style={{ marginLeft: 'auto' }}>
            {filtered.length} / {(list ?? []).length} araç
          </span>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th style={{ width: 34 }}></th>
                <th>Kod</th>
                <th>Anlık KM</th>
                <th>Üretici / Model</th>
                <th>Son Güncelleme</th>
                <th>Bakım Uyarısı</th>
                <th style={{ width: 260 }}>KM Güncelle</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {list === null && <SkeletonTableRows columns={8} />}
              {list !== null && filtered.map((v) => {
                const alert = alertByVehicleId.get(v.id)
                const rowClass = alert
                  ? alert.status === 'gecikti'
                    ? 'critical-row'
                    : alert.status === 'planlanacak'
                      ? 'toplan-row'
                      : alert.status === 'planlandi'
                        ? 'planned-row'
                        : alert.status === 'yaklasiyor'
                          ? 'alert-row'
                          : ''
                  : ''
                const isFav = favIds.includes(v.id)
                return (
                  <tr key={v.id} className={rowClass}>
                    <td>
                      <button
                        className={`fav-star-btn ${isFav ? 'active' : ''}`}
                        title={isFav ? 'Favorilerden çıkar' : 'Favorilere ekle'}
                        onClick={(e) => { e.stopPropagation(); toggleFavorite(v.id) }}
                      >
                        <Star size={16} />
                      </button>
                    </td>
                    <td className="clickable-row" onClick={() => setSelectedId(v.id)}>
                      <span className="vehicle-code">{v.code}</span>
                    </td>
                    <td>{fmtKm(v.current_km)}</td>
                    <td className="vehicle-model-cell">
                      {v.model ? <span className="vehicle-model-name">{v.model}</span> : <span>—</span>}
                      {v.manufacturer && <span className="vehicle-manufacturer">{v.manufacturer}</span>}
                    </td>
                    <td>{fmt(v.last_km_update_at)}</td>
                    <td>
                      {alert ? (
                        alert.status === 'gecikti' ? (
                          <span
                            className="critical-pill"
                            title={`${alert.plan_name} — ${remainingDisplayV(alert)} geçti, bakım periyodu aşıldı`}
                          >
                            <AlertTriangle size={12} /> Bakım Gecikti!
                          </span>
                        ) : alert.status === 'planlanacak' ? (
                          <span
                            className="toplan-pill"
                            title={`${alert.plan_name} bakım takip periyoduna girdi (${remainingDisplayV(alert)}) — henüz planlanmadı`}
                          >
                            <AlertTriangle size={12} /> Planlanacak
                          </span>
                        ) : alert.status === 'planlandi' ? (
                          <span
                            className="planned-pill"
                            title={`${alert.plan_name} bakım takip periyodunda (${remainingDisplayV(alert)}) — iş emri açıldı`}
                          >
                            <AlertTriangle size={12} /> Planlandı
                          </span>
                        ) : (
                          <span className="alert-pill" title={`${alert.plan_name} yaklaşıyor (${remainingDisplayV(alert)})`}>
                            <AlertTriangle size={12} /> Bakım Yaklaşıyor
                          </span>
                        )
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td>
                      <div className="row">
                        <input
                          type="number"
                          style={{ width: 120 }}
                          placeholder="Yeni KM"
                          value={kmEdit[v.id] ?? ''}
                          onChange={(e) => setKmEdit((s) => ({ ...s, [v.id]: e.target.value }))}
                        />
                        <button className="small" onClick={() => updateKm(v)}>Kaydet</button>
                      </div>
                    </td>
                    <td>
                      <div className="row">
                        <button className="small" onClick={() => setSelectedId(v.id)}>Detaylar</button>
                        <button className="small danger" onClick={() => removeVehicle(v)}>Sil</button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {list !== null && filtered.length === 0 && (
            <EmptyState icon={<Truck size={22} />} title="Kayıt bulunamadı" description="Arama veya filtre kriterlerine uyan araç yok." />
          )}
        </div>
      </div>
      <p className="muted" style={{ marginTop: 10 }}>
        KM güncellendiğinde sistem bakım aralıklarını otomatik kontrol eder ve gerekiyorsa bakım açar.
        Kırmızı satırlar, bir bakım hedefine -%5/+%10 aralığında yaklaşan araçları gösterir.
      </p>
    </>
  )
}
