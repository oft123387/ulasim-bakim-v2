import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Plan } from '../types'
import { PageHeader, Badge, EmptyState, SkeletonTableRows } from '../components/ui'
import { PlusCircle, ClipboardList } from 'lucide-react'

const fmtKm = (n: number) => n.toLocaleString('tr-TR')

function EditPlanForm({ plan, onDone, onCancel }: { plan: Plan; onDone: () => void; onCancel: () => void }) {
  const [name, setName] = useState(plan.name)
  const [interval, setIntervalKm] = useState(String(plan.interval_km))
  const [lower, setLower] = useState(String(plan.lower_pct))
  const [upper, setUpper] = useState(String(plan.upper_pct))
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    setError('')
    try {
      await api(`/plans/${plan.id}`, {
        method: 'PATCH',
        body: { name, interval_km: Number(interval), lower_pct: Number(lower), upper_pct: Number(upper) },
      })
      onDone()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <tr>
      <td colSpan={6}>
        <form className="row" onSubmit={save}>
          <input placeholder="Plan adı" value={name} onChange={(e) => setName(e.target.value)} required />
          <input placeholder="Periyot (km)" type="number" value={interval} onChange={(e) => setIntervalKm(e.target.value)} required />
          <input placeholder="Alt limit %" type="number" style={{ width: 110 }} value={lower} onChange={(e) => setLower(e.target.value)} />
          <input placeholder="Üst limit %" type="number" style={{ width: 110 }} value={upper} onChange={(e) => setUpper(e.target.value)} />
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
  const [lower, setLower] = useState('50')
  const [upper, setUpper] = useState('10')
  const [error, setError] = useState('')
  const [editingId, setEditingId] = useState<number | null>(null)

  const load = () => api<Plan[]>('/plans').then(setList).catch(() => setList([]))
  useEffect(() => {
    load()
  }, [])

  async function add(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    try {
      await api('/plans', { method: 'POST', body: { name, interval_km: Number(interval), lower_pct: Number(lower), upper_pct: Number(upper) } })
      setName('')
      setIntervalKm('')
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

  return (
    <>
      <PageHeader title="Bakım Planları" subtitle="Periyot bazlı bakım kurallarını (kilometre aralığı, tolerans) yönetin." />
      <div className="card">
        <div className="card-head">
          <div>
            <div className="card-title">Yeni Plan Ekle</div>
            <div className="card-subtitle">Örnek: 25.000 km periyot, alt %50, üst %10 → 1.025.000 km bakımı için aralık 1.012.500 – 1.027.500 km.</div>
          </div>
        </div>
        <form className="row" onSubmit={add}>
          <input placeholder="Plan adı" value={name} onChange={(e) => setName(e.target.value)} required />
          <input placeholder="Periyot (km)" type="number" value={interval} onChange={(e) => setIntervalKm(e.target.value)} required />
          <input placeholder="Alt limit %" type="number" style={{ width: 110 }} value={lower} onChange={(e) => setLower(e.target.value)} />
          <input placeholder="Üst limit %" type="number" style={{ width: 110 }} value={upper} onChange={(e) => setUpper(e.target.value)} />
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
                <th>Periyot</th>
                <th>Alt Limit</th>
                <th>Üst Limit</th>
                <th>Durum</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {list === null && <SkeletonTableRows columns={6} />}
              {list !== null && list.map((p) =>
                editingId === p.id ? (
                  <EditPlanForm key={p.id} plan={p} onDone={() => { setEditingId(null); load() }} onCancel={() => setEditingId(null)} />
                ) : (
                  <tr key={p.id}>
                    <td style={{ fontWeight: 600 }}>{p.name}</td>
                    <td>{fmtKm(p.interval_km)} km</td>
                    <td>-%{p.lower_pct}</td>
                    <td>+%{p.upper_pct}</td>
                    <td><Badge tone={p.is_active ? 'completed' : 'neutral'}>{p.is_active ? 'Aktif' : 'Pasif'}</Badge></td>
                    <td style={{ display: 'flex', gap: 6 }}>
                      <button className="small" onClick={() => setEditingId(p.id)}>Düzenle</button>
                      <button className="small" onClick={() => toggle(p)}>{p.is_active ? 'Pasife Al' : 'Aktifleştir'}</button>
                      <button className="small danger" onClick={() => remove(p)}>Sil</button>
                    </td>
                  </tr>
                ),
              )}
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
