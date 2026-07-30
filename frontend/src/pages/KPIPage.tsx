import { useEffect, useMemo, useState } from 'react'
import { api, apiDownload } from '../api'
import type { KPIReport } from '../types'
import { PageHeader, StatCard, Skeleton, EmptyState } from '../components/ui'
import { Download, CheckCircle2, Clock, AlertTriangle, Flame, Truck, Users } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

const fmtKm = (n: number) => n.toLocaleString('tr-TR')

type PresetKey = '7' | '30' | '90' | 'month' | 'year' | 'all' | 'custom'

const PRESETS: { key: PresetKey; label: string }[] = [
  { key: '7', label: 'Son 7 Gün' },
  { key: '30', label: 'Son 30 Gün' },
  { key: '90', label: 'Son 90 Gün' },
  { key: 'month', label: 'Bu Ay' },
  { key: 'year', label: 'Bu Yıl' },
  { key: 'all', label: 'Tüm Zamanlar' },
  { key: 'custom', label: 'Özel Aralık' },
]

function isoDaysAgo(days: number): string {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}
function isoToday(): string {
  return new Date().toISOString().slice(0, 10)
}
function isoMonthStart(): string {
  const d = new Date()
  return new Date(d.getFullYear(), d.getMonth(), 1).toISOString().slice(0, 10)
}
function isoYearStart(): string {
  const d = new Date()
  return new Date(d.getFullYear(), 0, 1).toISOString().slice(0, 10)
}

function rangeForPreset(preset: PresetKey): { from: string; to: string } {
  switch (preset) {
    case '7':
      return { from: isoDaysAgo(7), to: isoToday() }
    case '30':
      return { from: isoDaysAgo(30), to: isoToday() }
    case '90':
      return { from: isoDaysAgo(90), to: isoToday() }
    case 'month':
      return { from: isoMonthStart(), to: isoToday() }
    case 'year':
      return { from: isoYearStart(), to: isoToday() }
    case 'all':
      return { from: '', to: '' }
    default:
      return { from: isoDaysAgo(30), to: isoToday() }
  }
}

export default function KPIPage() {
  const [preset, setPreset] = useState<PresetKey>('30')
  const [customFrom, setCustomFrom] = useState(isoDaysAgo(30))
  const [customTo, setCustomTo] = useState(isoToday())
  const [data, setData] = useState<KPIReport | null>(null)
  const [error, setError] = useState('')
  const [exporting, setExporting] = useState(false)

  const range = useMemo(
    () => (preset === 'custom' ? { from: customFrom, to: customTo } : rangeForPreset(preset)),
    [preset, customFrom, customTo],
  )

  const query = useMemo(() => {
    const p = new URLSearchParams()
    if (range.from) p.set('date_from', range.from)
    if (range.to) p.set('date_to', range.to)
    return p.toString()
  }, [range])

  useEffect(() => {
    setData(null)
    setError('')
    api<KPIReport>(`/kpi?${query}`).then(setData).catch((e) => setError((e as Error).message))
  }, [query])

  async function exportExcel() {
    setExporting(true)
    setError('')
    try {
      await apiDownload(`/kpi/export?${query}`, 'kpi_raporu.xlsx')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setExporting(false)
    }
  }

  const modelChartData = (data?.by_vehicle_model ?? []).map((r) => ({
    name: r.vehicle_model,
    Açılan: r.opened,
    Tamamlanan: r.completed,
    'Şu An Açık': r.open_now,
  }))

  return (
    <div>
      <PageHeader
        title="KPI Raporu"
        subtitle="Filo genelinde tamamlanma, kritiklik, araç/model ve personel performansı"
        actions={
          <button className="primary" onClick={exportExcel} disabled={exporting || !data}>
            <Download size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
            {exporting ? 'Hazırlanıyor…' : 'Excel Olarak İndir'}
          </button>
        }
      />

      <div className="card" style={{ marginBottom: 16 }}>
        <div className="row calendar-view-tabs" style={{ gap: 4, flexWrap: 'wrap', alignItems: 'center', width: 'fit-content' }}>
          {PRESETS.map((p) => (
            <button
              key={p.key}
              className={preset === p.key ? 'primary' : ''}
              style={{ padding: '6px 12px', fontSize: 12.5 }}
              onClick={() => setPreset(p.key)}
            >
              {p.label}
            </button>
          ))}
          {preset === 'custom' && (
            <span className="row" style={{ gap: 8, marginLeft: 8 }}>
              <input type="date" value={customFrom} onChange={(e) => setCustomFrom(e.target.value)} />
              <span className="muted">—</span>
              <input type="date" value={customTo} onChange={(e) => setCustomTo(e.target.value)} />
            </span>
          )}
        </div>
      </div>

      {error && (
        <div className="card" style={{ marginBottom: 16, color: 'var(--red, #d33)' }}>
          {error}
        </div>
      )}

      {!data && !error ? (
        <>
          <div className="grid stat-grid-4" style={{ marginBottom: 16 }}>
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="card">
                <Skeleton width="50%" height={11} style={{ marginBottom: 12 }} />
                <Skeleton width="35%" height={26} />
              </div>
            ))}
          </div>
          <div className="card"><Skeleton height={200} /></div>
        </>
      ) : data ? (
        <>
          <div className="grid stat-grid-4" style={{ marginBottom: 16 }}>
            <StatCard
              label="Tamamlanma Oranı"
              value={`%${data.completion.completion_rate_pct.toLocaleString('tr-TR')}`}
              tone="completed"
              icon={<CheckCircle2 size={18} />}
            />
            <StatCard
              label="Ort. Tamamlanma Süresi"
              value={
                data.completion.avg_completion_days != null
                  ? `${data.completion.avg_completion_days.toLocaleString('tr-TR')} gün`
                  : '—'
              }
              icon={<Clock size={18} />}
            />
            <StatCard
              label="Kritik (açık)"
              value={data.critical.urgent_count}
              tone="pending"
              icon={<AlertTriangle size={18} />}
            />
            <StatCard
              label="Çok Kritik / Gecikmiş"
              value={`${data.critical.very_critical_count} / ${data.critical.overdue_count}`}
              tone="danger"
              icon={<Flame size={18} />}
            />
          </div>
          <div className="muted" style={{ fontSize: 11.5, marginTop: -10, marginBottom: 16 }}>
            Kritik/gecikmiş sayıları her zaman güncel duruma göredir, seçilen tarih aralığından bağımsızdır.
            Seçilen aralıkta {data.completion.opened_in_range} iş emri açıldı, {data.completion.completed_in_range} iş emri tamamlandı.
          </div>

          {modelChartData.length > 0 && (
            <div className="card slide-up" style={{ marginBottom: 16 }}>
              <div className="card-head">
                <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                  <Truck size={14} /> Araç Modeli Bazlı Performans
                </div>
              </div>
              <ResponsiveContainer width="100%" height={220}>
                <BarChart data={modelChartData} barGap={4}>
                  <CartesianGrid vertical={false} stroke="var(--border)" />
                  <XAxis dataKey="name" tick={{ fontSize: 11, fill: 'var(--text-tertiary)' }} axisLine={{ stroke: 'var(--border)' }} tickLine={false} />
                  <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: 'var(--text-tertiary)' }} axisLine={false} tickLine={false} />
                  <Tooltip
                    cursor={{ fill: 'var(--surface-alt)' }}
                    contentStyle={{ borderRadius: 10, border: '1px solid var(--border)', fontSize: 12.5, boxShadow: 'var(--shadow-md)' }}
                  />
                  <Bar dataKey="Açılan" fill="var(--amber)" radius={[6, 6, 0, 0]} />
                  <Bar dataKey="Tamamlanan" fill="var(--green)" radius={[6, 6, 0, 0]} />
                  <Bar dataKey="Şu An Açık" fill="var(--accent)" radius={[6, 6, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          <div className="grid grid-2" style={{ alignItems: 'start', gap: 16 }}>
            <div className="table-card slide-up">
              <div className="table-toolbar">
                <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                  <Truck size={14} /> Araç Bazlı (İlk 10)
                </div>
              </div>
              {data.top_vehicles.length === 0 ? (
                <EmptyState title="Araç verisi yok" />
              ) : (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Araç</th>
                        <th>Açılan</th>
                        <th>Tamamlanan</th>
                        <th>Şu An Açık</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.top_vehicles.map((r) => (
                        <tr key={r.vehicle.id}>
                          <td className="vehicle-code">{r.vehicle.code}</td>
                          <td>{fmtKm(r.opened)}</td>
                          <td>{fmtKm(r.completed)}</td>
                          <td>{fmtKm(r.open_now)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div className="table-card slide-up">
              <div className="table-toolbar">
                <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                  <Users size={14} /> Personel Performansı
                </div>
              </div>
              {data.by_person.length === 0 ? (
                <EmptyState title="Bu aralıkta tamamlama kaydı yok" />
              ) : (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Ad Soyad</th>
                        <th>Vardiya</th>
                        <th>Tamamlanan Madde</th>
                        <th>Tamamlanan İş Emri</th>
                        <th>Ort. Süre</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.by_person.map((r) => (
                        <tr key={r.user.id}>
                          <td>{r.user.full_name}</td>
                          <td>{r.shift_name ?? '—'}</td>
                          <td>{r.items_completed_in_range}</td>
                          <td>{r.maintenances_completed_in_range}</td>
                          <td>{r.avg_completion_days != null ? `${r.avg_completion_days} gün` : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </>
      ) : null}
    </div>
  )
}
