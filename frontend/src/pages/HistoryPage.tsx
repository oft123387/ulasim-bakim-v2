import { useEffect, useMemo, useState } from 'react'
import { api, apiDownload } from '../api'
import type { Maintenance, Plan, User, Vehicle } from '../types'
import { STATUS_TR } from '../types'
import { PageHeader, Badge, EmptyState, SkeletonTableRows } from '../components/ui'
import { ChevronRight, Download, ListFilter, History as HistoryIcon, Truck } from 'lucide-react'
import { targetLabel, mergedPeriodLabel } from '../format'

const fmt = (s: string | null) => (s ? new Date(s).toLocaleString('tr-TR') : '—')
// Süre bazlı iş emirlerinde gerçek hedef target_date'dedir (target_km gizli/null).
const hedefLabel = (m: Maintenance) => targetLabel(m.target_km, m.target_date, m.vehicle.model)

interface Filters {
  status: string
  vehicle_id: string
  plan_id: string
  worked_by_id: string
  date_from: string
  date_to: string
  km_min: string
  km_max: string
}

const EMPTY: Filters = {
  status: 'completed',
  vehicle_id: '',
  plan_id: '',
  worked_by_id: '',
  date_from: '',
  date_to: '',
  km_min: '',
  km_max: '',
}

/** Bakım Geçmişi (Faz 13-14): tamamlanan (veya seçilen durumdaki) bakımları
 * TEK bir düz tabloda (araç bazlı gruplama/üst başlık YOK — araç kendi
 * sütununda), sunucunun döndürdüğü sırayla (en yeni üstte) listeler;
 * tarih/araç/plan/personel/km filtreleri ve Excel dışa aktarma sunar. */
export default function HistoryPage({
  user,
  perms,
  onOpenMaintenance,
}: {
  user: User
  perms: Record<string, boolean>
  onOpenMaintenance: (id: number) => void
}) {
  const [f, setF] = useState<Filters>(EMPTY)
  const [list, setList] = useState<Maintenance[] | null>(null)
  const [vehicles, setVehicles] = useState<Vehicle[]>([])
  const [plans, setPlans] = useState<Plan[]>([])
  const [users, setUsers] = useState<User[]>([])
  const [exportError, setExportError] = useState('')

  const isAdmin = user.role === 'admin'

  useEffect(() => {
    api<Vehicle[]>('/vehicles').then(setVehicles).catch(() => setVehicles([]))
    api<Plan[]>('/plans').then(setPlans).catch(() => setPlans([]))
    if (isAdmin) api<User[]>('/users').then(setUsers).catch(() => setUsers([]))
  }, [isAdmin])

  const query = useMemo(() => {
    const p = new URLSearchParams()
    if (f.status) p.set('status', f.status)
    if (f.vehicle_id) p.set('vehicle_id', f.vehicle_id)
    if (f.plan_id) p.set('plan_id', f.plan_id)
    if (f.worked_by_id) p.set('worked_by_id', f.worked_by_id)
    if (f.km_min) p.set('km_min', f.km_min)
    if (f.km_max) p.set('km_max', f.km_max)
    // Geçmiş: tamamlanma tarihine göre; aksi halde oluşturma tarihine göre filtrele
    p.set('date_field', f.status === 'completed' ? 'completed' : 'created')
    if (f.date_from) p.set('date_from', f.date_from)
    if (f.date_to) p.set('date_to', f.date_to)
    return p.toString()
  }, [f])

  useEffect(() => {
    setList(null)
    api<Maintenance[]>(`/maintenances?${query}`).then(setList).catch(() => setList([]))
  }, [query])

  const rows = list ?? []

  function set<K extends keyof Filters>(k: K, v: string) {
    setF((prev) => ({ ...prev, [k]: v }))
  }

  async function exportExcel() {
    setExportError('')
    try {
      await apiDownload(`/maintenances/export?${query}`, 'bakim_gecmisi.xlsx')
    } catch (e) {
      setExportError((e as Error).message)
    }
  }

  const hasFilter =
    f.vehicle_id || f.plan_id || f.worked_by_id || f.date_from || f.date_to || f.km_min || f.km_max ||
    f.status !== 'completed'

  return (
    <>
      <PageHeader
        title="Bakım Geçmişi"
      />

      <div className="table-card fade-in">
        <div className="table-toolbar" style={{ flexWrap: 'wrap' }}>
          <ListFilter size={15} color="var(--text-tertiary)" />
          <select value={f.status} onChange={(e) => set('status', e.target.value)}>
            <option value="completed">Tamamlandı</option>
            <option value="">Tüm Durumlar</option>
            <option value="pending">Bekliyor</option>
            <option value="in_progress">Devam Ediyor</option>
          </select>
          <select value={f.vehicle_id} onChange={(e) => set('vehicle_id', e.target.value)}>
            <option value="">Tüm Araçlar</option>
            {vehicles.map((v) => (
              <option key={v.id} value={v.id}>{v.code}</option>
            ))}
          </select>
          <select value={f.plan_id} onChange={(e) => set('plan_id', e.target.value)}>
            <option value="">Tüm Periyotlar</option>
            {plans.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
          {isAdmin && (
            <select value={f.worked_by_id} onChange={(e) => set('worked_by_id', e.target.value)}>
              <option value="">Tüm Personel</option>
              {users.map((u) => (
                <option key={u.id} value={u.id}>{u.full_name}</option>
              ))}
            </select>
          )}
          <label className="filter-inline">
            <span>Tarih</span>
            <input type="date" value={f.date_from} onChange={(e) => set('date_from', e.target.value)} />
            <span>–</span>
            <input type="date" value={f.date_to} onChange={(e) => set('date_to', e.target.value)} />
          </label>
          <label className="filter-inline">
            <span>KM</span>
            <input
              type="number"
              placeholder="min"
              value={f.km_min}
              onChange={(e) => set('km_min', e.target.value)}
              style={{ width: 90 }}
            />
            <span>–</span>
            <input
              type="number"
              placeholder="max"
              value={f.km_max}
              onChange={(e) => set('km_max', e.target.value)}
              style={{ width: 90 }}
            />
          </label>
          {hasFilter && (
            <button className="small ghost" onClick={() => setF(EMPTY)}>Filtreleri Temizle</button>
          )}
          <span className="muted" style={{ marginLeft: 'auto' }}>{rows.length} kayıt</span>
          {perms.export_excel && (
            <button className="small" onClick={exportExcel}>
              <Download size={13} style={{ marginRight: 5, verticalAlign: -2 }} />
              Excel'e Aktar
            </button>
          )}
        </div>
        {exportError && <div className="error" style={{ margin: '0 16px 12px' }}>{exportError}</div>}

        {list === null && (
          <div className="table-scroll">
            <table><tbody><SkeletonTableRows columns={6} /></tbody></table>
          </div>
        )}

        {list !== null && rows.length === 0 && (
          <EmptyState
            icon={<HistoryIcon size={22} />}
            title="Kayıt bulunamadı"
            description="Seçtiğiniz filtrelere uyan bir bakım yok."
          />
        )}

        {list !== null && rows.length > 0 && (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Araç</th>
                  <th>İş Emri</th>
                  <th>Periyot</th>
                  <th>Hedef</th>
                  <th>Durum</th>
                  <th>Bitiş</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((m) => (
                  <tr key={m.id} className="clickable-row" onClick={() => onOpenMaintenance(m.id)}>
                    <td>
                      <span className="vehicle-code"><Truck size={13} style={{ verticalAlign: -2, marginRight: 4 }} />{m.vehicle.code}</span>
                    </td>
                    <td style={{ fontWeight: 600 }}>#{m.id}</td>
                    <td>{mergedPeriodLabel(m.covered_tiers, m.plan.plan_type, m.vehicle.model, m.plan.name)}</td>
                    <td>{hedefLabel(m)}</td>
                    <td><Badge tone={m.status}>{STATUS_TR[m.status]}</Badge></td>
                    <td>{fmt(m.completed_at)}</td>
                    <td>
                      <button className="small primary" onClick={(e) => { e.stopPropagation(); onOpenMaintenance(m.id) }}>
                        Detay <ChevronRight size={12} style={{ verticalAlign: -1, marginLeft: 2 }} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  )
}
