import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Plan } from '../types'
import { PageHeader, Badge, EmptyState, SkeletonTableRows } from '../components/ui'
import { PlusCircle, ClipboardList } from 'lucide-react'

const fmtKm = (n: number) => n.toLocaleString('tr-TR')

// Bir planın açılma penceresini okunabilir metne çevirir — tabloda hem
// yüzde hem km modunu tek bakışta anlaşılır göstermek için.
function windowLabel(p: Plan): { lower: string; upper: string } {
  if (p.threshold_type === 'km') {
    return { lower: `-${fmtKm(p.lower_km)} km`, upper: `+${fmtKm(p.upper_km)} km` }
  }
  return { lower: `-%${p.lower_pct}`, upper: `+%${p.upper_pct}` }
}

// Toplu katalog içe aktarmasından gelen süre bazlı (aylık/yıllık) planlarda
// interval_km=0'dır — periyot sütununda km yerine ay/yıl göster.
function periodLabel(p: Plan): string {
  if (p.plan_type === 'unknown') return 'Belirsiz (?)'
  if (p.plan_type === 'time') {
    const mo = p.interval_months ?? 0
    return mo % 12 === 0 ? `${mo / 12} yıl` : `${mo} ay`
  }
  return `${fmtKm(p.interval_km)} km`
}

function ThresholdFields({
  mode, setMode, lower, setLower, upper, setUpper,
}: {
  mode: 'percent' | 'km'
  setMode: (m: 'percent' | 'km') => void
  lower: string
  setLower: (v: string) => void
  upper: string
  setUpper: (v: string) => void
}) {
  return (
    <>
      <select value={mode} onChange={(e) => setMode(e.target.value as 'percent' | 'km')} style={{ width: 130 }}>
        <option value="percent">Yüzde (%)</option>
        <option value="km">Sabit km</option>
      </select>
      <input
        placeholder={mode === 'percent' ? 'Alt limit %' : 'Alt limit (km)'}
        type="number"
        style={{ width: 120 }}
        value={lower}
        onChange={(e) => setLower(e.target.value)}
      />
      <input
        placeholder={mode === 'percent' ? 'Üst limit %' : 'Üst limit (km)'}
        type="number"
        style={{ width: 120 }}
        value={upper}
        onChange={(e) => setUpper(e.target.value)}
      />
    </>
  )
}

function EditPlanForm({
  plan, knownModels, onDone, onCancel,
}: {
  plan: Plan
  knownModels: string[]
  onDone: () => void
  onCancel: () => void
}) {
  const [name, setName] = useState(plan.name)
  const [interval, setIntervalKm] = useState(String(plan.interval_km))
  const [vehicleModel, setVehicleModel] = useState(plan.vehicle_model ?? '')
  const [mode, setMode] = useState<'percent' | 'km'>(plan.threshold_type ?? 'percent')
  const [lower, setLower] = useState(String(mode === 'km' ? plan.lower_km : plan.lower_pct))
  const [upper, setUpper] = useState(String(mode === 'km' ? plan.upper_km : plan.upper_pct))
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  function onModeChange(next: 'percent' | 'km') {
    // Mod değişince ilgili alanın mevcut değerini gösterip diğerini
    // karıştırmamak için karşılık gelen alana geçilir.
    setMode(next)
    setLower(String(next === 'km' ? plan.lower_km : plan.lower_pct))
    setUpper(String(next === 'km' ? plan.upper_km : plan.upper_pct))
  }

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    setError('')
    try {
      const body: Record<string, unknown> = {
        name,
        interval_km: Number(interval),
        threshold_type: mode,
        vehicle_model: vehicleModel.trim(),
      }
      if (mode === 'km') {
        body.lower_km = Number(lower)
        body.upper_km = Number(upper)
      } else {
        body.lower_pct = Number(lower)
        body.upper_pct = Number(upper)
      }
      await api(`/plans/${plan.id}`, { method: 'PATCH', body })
      onDone()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <tr>
      <td colSpan={7}>
        <form className="row" onSubmit={save} style={{ flexWrap: 'wrap' }}>
          <input placeholder="Plan adı" value={name} onChange={(e) => setName(e.target.value)} required />
          <input placeholder="Periyot (km)" type="number" value={interval} onChange={(e) => setIntervalKm(e.target.value)} required />
          <select
            value={vehicleModel}
            onChange={(e) => setVehicleModel(e.target.value)}
            style={{ minWidth: 200 }}
          >
            <option value="">Genel / Tüm Araçlar</option>
            {(vehicleModel && !knownModels.includes(vehicleModel) ? [vehicleModel, ...knownModels] : knownModels).map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
          <ThresholdFields mode={mode} setMode={onModeChange} lower={lower} setLower={setLower} upper={upper} setUpper={setUpper} />
          <button className="small primary" disabled={saving}>{saving ? 'Kaydediliyor…' : 'Kaydet'}</button>
          <button className="small" type="button" onClick={onCancel}>Vazgeç</button>
        </form>
        {error && <div className="error">{error}</div>}
      </td>
    </tr>
  )
}

export default function PlansPage() {
  const [list, setList] = useState<Plan[] | null>(null)
  const [name, setName] = useState('')
  const [interval, setIntervalKm] = useState('')
  const [vehicleModel, setVehicleModel] = useState('')
  const [mode, setMode] = useState<'percent' | 'km'>('percent')
  const [lower, setLower] = useState('5')
  const [upper, setUpper] = useState('10')
  const [error, setError] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)
  const [knownModels, setKnownModels] = useState<string[]>([])
  const [normalizing, setNormalizing] = useState(false)
  const [normalizeMsg, setNormalizeMsg] = useState('')

  const load = () => api<Plan[]>('/plans').then(setList).catch(() => setList([]))
  useEffect(() => {
    load()
    api<string[]>('/vehicle-models').then(setKnownModels).catch(() => setKnownModels([]))
  }, [])

  function onModeChange(next: 'percent' | 'km') {
    setMode(next)
    // Yeni plan formunda mod değişince alanlara o modun tipik varsayılan
    // değerlerini koy (kullanıcı boş/anlamsız bir değerle kalmasın).
    setLower(next === 'km' ? '2000' : '5')
    setUpper(next === 'km' ? '500' : '10')
  }

  async function add(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    try {
      const body: Record<string, unknown> = {
        name, interval_km: Number(interval), threshold_type: mode,
        vehicle_model: vehicleModel.trim(),
      }
      if (mode === 'km') {
        body.lower_km = Number(lower)
        body.upper_km = Number(upper)
      } else {
        body.lower_pct = Number(lower)
        body.upper_pct = Number(upper)
      }
      await api('/plans', { method: 'POST', body })
      setName('')
      setIntervalKm('')
      setVehicleModel('')
      load()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function toggle(p: Plan) {
    await api(`/plans/${p.id}`, { method: 'PATCH', body: { is_active: !p.is_active } })
    load()
  }

  async function remove(p: Plan) {
    if (!confirm(`"${p.name}" planını silmek istediğinize emin misiniz?`)) return
    try {
      const res = await api<{ ok: boolean; deactivated: boolean }>(`/plans/${p.id}`, { method: 'DELETE' })
      if (res.deactivated) {
        alert('Bu plana ait açılmış bakım kayıtları olduğu için plan silinmedi, pasife alındı.')
      }
      load()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function normalizeSirioNames() {
    setNormalizing(true)
    setNormalizeMsg('')
    try {
      const res = await api<{ count: number }>('/plans/normalize-sirio-names', { method: 'POST' })
      setNormalizeMsg(
        res.count > 0
          ? `${res.count} Sirio planının adı M/Y koduna çevrildi.`
          : 'Zaten tüm Sirio planları M/Y biçiminde — değişiklik yapılmadı.',
      )
      load()
    } catch (err) {
      setNormalizeMsg((err as Error).message)
    } finally {
      setNormalizing(false)
    }
  }

  return (
    <>
      <PageHeader
        title="Bakım Planları"
        actions={
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
            <button className="small" onClick={normalizeSirioNames} disabled={normalizing}
              title="Var olan tüm Sirio km planlarının adını kısa periyot koduna (ör. 1.440.000 km -> 12Y) çevirir">
              {normalizing ? 'Düzeltiliyor...' : 'Sirio Adlarını M/Y Yap'}
            </button>
            {normalizeMsg && <span className="muted" style={{ fontSize: 12 }}>{normalizeMsg}</span>}
          </div>
        }
      />
      <div className="card">
        <div className="card-head">
          <div>
            <div className="card-title">Yeni Plan Ekle</div>
          </div>
        </div>
        <form className="row" onSubmit={add} style={{ flexWrap: 'wrap' }}>
          <input placeholder="Plan adı" value={name} onChange={(e) => setName(e.target.value)} required />
          <input placeholder="Periyot (km)" type="number" value={interval} onChange={(e) => setIntervalKm(e.target.value)} required />
          <select
            value={vehicleModel}
            onChange={(e) => setVehicleModel(e.target.value)}
            style={{ minWidth: 200 }}
          >
            <option value="">Genel / Tüm Araçlar</option>
            {knownModels.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
          <ThresholdFields mode={mode} setMode={onModeChange} lower={lower} setLower={setLower} upper={upper} setUpper={setUpper} />
          <button className="primary"><PlusCircle size={14} style={{ marginRight: 6, verticalAlign: -2 }} />Plan Ekle</button>
        </form>
        {error && <div className="error">{error}</div>}
      </div>

      <div className="table-card fade-in">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Plan</th>
                <th>Araç Modeli</th>
                <th>Periyot</th>
                <th>Alt Limit</th>
                <th>Üst Limit</th>
                <th>Durum</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {list === null && <SkeletonTableRows columns={7} />}
              {list !== null && list.map((p) => {
                if (editingId === p.id) {
                  return (
                    <EditPlanForm
                      key={p.id}
                      plan={p}
                      knownModels={knownModels}
                      onDone={() => { setEditingId(null); load() }}
                      onCancel={() => setEditingId(null)}
                    />
                  )
                }
                const { lower: lowerLabel, upper: upperLabel } = windowLabel(p)
                return (
                  <tr key={p.id}>
                    <td style={{ fontWeight: 600 }}>{p.name}</td>
                    <td>
                      {p.vehicle_model ? (
                        <span className="badge in_progress">{p.vehicle_model}</span>
                      ) : (
                        <span className="muted">Genel (tüm araçlar)</span>
                      )}
                    </td>
                    <td>{periodLabel(p)}</td>
                    <td>{lowerLabel}</td>
                    <td>{upperLabel}</td>
                    <td><Badge tone={p.is_active ? 'completed' : 'neutral'}>{p.is_active ? 'Aktif' : 'Pasif'}</Badge></td>
                    <td style={{ display: 'flex', gap: 6 }}>
                      <button className="small" onClick={() => setEditingId(p.id)}>Düzenle</button>
                      <button className="small" onClick={() => toggle(p)}>{p.is_active ? 'Pasife Al' : 'Aktifleştir'}</button>
                      <button className="small danger" onClick={() => remove(p)}>Sil</button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {list !== null && list.length === 0 && (
            <EmptyState icon={<ClipboardList size={22} />} title="Henüz bakım planı yok" description="Yukarıdan yeni bir periyot planı ekleyin." />
          )}
        </div>
      </div>
    </>
  )
}
