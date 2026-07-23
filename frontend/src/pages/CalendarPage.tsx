import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { CalendarProjection, Maintenance } from '../types'
import { STATUS_TR } from '../types'
import { PageHeader, Tabs, EmptyState, Badge } from '../components/ui'
import { ChevronLeft, ChevronRight, CalendarDays, Wrench, Gauge, Info } from 'lucide-react'

type ViewMode = 'day' | 'week' | 'month'
type DateField = 'created' | 'completed'

const DOW_TR = ['Pzt', 'Sal', 'Çar', 'Per', 'Cum', 'Cmt', 'Paz']
const MONTH_TR = [
  'Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
  'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık',
]

function toISODate(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}
function startOfDay(d: Date): Date {
  const x = new Date(d)
  x.setHours(0, 0, 0, 0)
  return x
}
function addDays(d: Date, n: number): Date {
  const x = new Date(d)
  x.setDate(x.getDate() + n)
  return x
}
/** Pazartesi başlangıçlı hafta başı. */
function startOfWeek(d: Date): Date {
  const x = startOfDay(d)
  const dow = (x.getDay() + 6) % 7 // Pzt=0 .. Paz=6
  return addDays(x, -dow)
}
function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1)
}
function endOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth() + 1, 0)
}

function getRange(anchor: Date, view: ViewMode): { from: Date; to: Date } {
  if (view === 'day') {
    const d = startOfDay(anchor)
    return { from: d, to: d }
  }
  if (view === 'week') {
    const from = startOfWeek(anchor)
    return { from, to: addDays(from, 6) }
  }
  const mStart = startOfMonth(anchor)
  const mEnd = endOfMonth(anchor)
  return { from: startOfWeek(mStart), to: addDays(startOfWeek(mEnd), 6) }
}

function rangeTitle(anchor: Date, view: ViewMode): string {
  if (view === 'month') return `${MONTH_TR[anchor.getMonth()]} ${anchor.getFullYear()}`
  const { from, to } = getRange(anchor, view)
  if (view === 'day') {
    return `${from.getDate()} ${MONTH_TR[from.getMonth()]} ${from.getFullYear()} · ${DOW_TR[(from.getDay() + 6) % 7]}`
  }
  const sameMonth = from.getMonth() === to.getMonth()
  return sameMonth
    ? `${from.getDate()}–${to.getDate()} ${MONTH_TR[from.getMonth()]} ${from.getFullYear()}`
    : `${from.getDate()} ${MONTH_TR[from.getMonth()]} – ${to.getDate()} ${MONTH_TR[to.getMonth()]} ${to.getFullYear()}`
}

function fmtTime(s: string | null): string {
  if (!s) return ''
  return new Date(s).toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' })
}

function fmtDateShort(iso: string): string {
  const d = new Date(iso + 'T00:00:00')
  return `${d.getDate()} ${MONTH_TR[d.getMonth()]}`
}

function eventDate(m: Maintenance, field: DateField): string | null {
  return field === 'completed' ? m.completed_at : m.created_at
}

// En dolu hücrelerde en önemli maddelerin görünür kalması için: Bekliyor ve
// Devam Ediyor, Tamamlandı'dan önce gösterilir (ilgi/aksiyon gerektiren
// işler "+N daha" arkasında kaybolmasın diye).
const _STATUS_PRIORITY: Record<Maintenance['status'], number> = {
  pending: 0,
  in_progress: 1,
  completed: 2,
}
function sortByStatusPriority(events: Maintenance[]): Maintenance[] {
  return [...events].sort((a, b) => _STATUS_PRIORITY[a.status] - _STATUS_PRIORITY[b.status])
}

// ---------------------------------------------------------- Takvim lejantı
// Renk kodlamasının ne anlama geldiğini tek bakışta göstermek için — ızgara
// kendi başına yalnızca renkli kutucuklar olduğundan bu olmadan anlaşılması
// zor olabiliyordu.
function CalendarLegend() {
  return (
    <div className="row" style={{ gap: 16, flexWrap: 'wrap', fontSize: 12, color: 'var(--text-tertiary)' }}>
      <span className="row" style={{ gap: 6 }}>
        <span className="cal-legend-dot" style={{ background: 'var(--amber)' }} /> Bekliyor
      </span>
      <span className="row" style={{ gap: 6 }}>
        <span className="cal-legend-dot" style={{ background: 'var(--primary-600)' }} /> Devam Ediyor
      </span>
      <span className="row" style={{ gap: 6 }}>
        <span className="cal-legend-dot" style={{ background: 'var(--green)' }} /> Tamamlandı
      </span>
      <span className="row" style={{ gap: 6 }}>
        <Gauge size={12} /> Tahmini bakım penceresi
      </span>
      <span className="row" style={{ gap: 6 }}>
        <span className="cal-legend-today" /> Bugün
      </span>
    </div>
  )
}

/** Projeksiyon penceresi verilen günü kapsıyor mu (ISO string karşılaştırması, aynı format olduğu için doğrudan çalışır). */
function projectionCoversDay(p: CalendarProjection, dayISO: string): boolean {
  return p.window_start <= dayISO && dayISO <= p.window_end
}

/** Projeksiyon penceresi verilen [from,to] aralığıyla kesişiyor mu. */
function projectionOverlapsRange(p: CalendarProjection, fromISO: string, toISO: string): boolean {
  return p.window_start <= toISO && p.window_end >= fromISO
}

// ---------------------------------------------------------- Ay/Hafta hücresi
function DayCell({
  date,
  inMonth,
  events,
  projections,
  onOpenMaintenance,
  onOpenDay,
}: {
  date: Date
  inMonth: boolean
  events: Maintenance[]
  projections: CalendarProjection[]
  onOpenMaintenance: (id: number) => void
  onOpenDay: (d: Date) => void
}) {
  const isToday = toISODate(date) === toISODate(new Date())
  const sorted = sortByStatusPriority(events)
  const visible = sorted.slice(0, 3)
  const extra = sorted.length - visible.length
  const hasProjection = projections.length > 0
  return (
    <div
      className={`cal-cell ${inMonth ? '' : 'cal-out'} ${isToday ? 'cal-today' : ''} ${hasProjection ? 'cal-projected' : ''}`}
      onClick={() => onOpenDay(date)}
      title={
        hasProjection
          ? `Tahmini bakım aralığı: ${projections.map((p) => `${p.vehicle.code} — ${p.plan_name}`).join(', ')}`
          : undefined
      }
    >
      <div className="cal-daynum">{date.getDate()}</div>
      {hasProjection && (
        <div className="cal-projection-band">
          <Gauge size={10} />
          {projections.length === 1 ? projections[0].vehicle.code : `${projections.length} araç`}
        </div>
      )}
      <div className="cal-chips">
        {visible.map((m) => (
          <button
            key={m.id}
            className={`cal-chip cal-${m.status}`}
            onClick={(e) => {
              e.stopPropagation()
              onOpenMaintenance(m.id)
            }}
            title={`${m.vehicle.code} — ${m.plan.name} (${STATUS_TR[m.status]})`}
          >
            {m.vehicle.code} · {m.plan.name}
          </button>
        ))}
        {extra > 0 && <div className="cal-more">+{extra} daha</div>}
      </div>
    </div>
  )
}

// ---------------------------------------------------------- Tahmini pencere listesi
function ProjectionsPanel({
  projections,
  onOpenMaintenance,
}: {
  projections: CalendarProjection[]
  onOpenMaintenance: (id: number) => void
}) {
  if (projections.length === 0) return null
  return (
    <div className="table-card fade-in" style={{ marginTop: 16 }}>
      <div className="table-toolbar">
        <Gauge size={15} color="var(--text-tertiary)" />
        <span style={{ fontWeight: 700, fontSize: 13.5 }}>Tahmini Bakım Pencereleri</span>
        <span
          title="Aracın günlük ortalama 300 km yaptığı varsayılarak hesaplanır; pencere hedef km'nin %5 öncesinden %10 sonrasına kadardır. Henüz açılmamış (yaklaşan) bakımların tahmini tarihidir — kesin değildir."
          style={{ cursor: 'help', display: 'inline-flex' }}
        >
          <Info size={13} color="var(--text-tertiary)" />
        </span>
        <span className="muted" style={{ marginLeft: 'auto' }}>{projections.length} kayıt</span>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Araç</th>
              <th>Periyot</th>
              <th>Tahmini Aralık</th>
              <th>Kalan KM</th>
              <th>Durum</th>
            </tr>
          </thead>
          <tbody>
            {projections.map((p, i) => (
              <tr
                key={i}
                className={p.maintenance_id ? 'clickable-row' : ''}
                onClick={() => p.maintenance_id && onOpenMaintenance(p.maintenance_id)}
              >
                <td><span className="vehicle-code">{p.vehicle.code}</span></td>
                <td>
                  {p.plan_name}
                  {p.covered_tiers && p.covered_tiers.length > 1 && (
                    <span className="muted" style={{ fontSize: 11.5, marginLeft: 6 }}>
                      ({p.covered_tiers.length} periyot birleşik)
                    </span>
                  )}
                </td>
                <td>{fmtDateShort(p.window_start)} – {fmtDateShort(p.window_end)}</td>
                <td>{p.remaining_km > 0 ? `${p.remaining_km.toLocaleString('tr-TR')} km` : 'Hedefe ulaşıldı'}</td>
                <td>
                  {p.status ? (
                    <Badge tone={p.status}>{STATUS_TR[p.status]}</Badge>
                  ) : (
                    <Badge tone="neutral">Henüz açılmadı</Badge>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ---------------------------------------------------------- Takvim sayfası
export default function CalendarPage({
  onOpenMaintenance,
}: {
  onOpenMaintenance: (id: number) => void
}) {
  const [view, setView] = useState<ViewMode>('month')
  const [anchor, setAnchor] = useState<Date>(() => startOfDay(new Date()))
  const [dateField, setDateField] = useState<DateField>('created')
  const [list, setList] = useState<Maintenance[] | null>(null)
  const [projections, setProjections] = useState<CalendarProjection[]>([])
  const [error, setError] = useState('')

  const { from, to } = useMemo(() => getRange(anchor, view), [anchor, view])

  useEffect(() => {
    setList(null)
    setError('')
    api<Maintenance[]>(
      `/maintenances?date_field=${dateField}&date_from=${toISODate(from)}&date_to=${toISODate(to)}`,
    )
      .then(setList)
      .catch((e) => {
        setError((e as Error).message)
        setList([])
      })
  }, [from, to, dateField])

  // Tahmini bakım pencereleri tarih aralığından bağımsızdır (mevcut km'ye
  // göre hesaplanır) — bir kez çekilir, tüm görünümlerde aynı veri kullanılır.
  useEffect(() => {
    api<CalendarProjection[]>('/maintenances/projections')
      .then(setProjections)
      .catch(() => setProjections([]))
  }, [])

  const byDate = useMemo(() => {
    const map = new Map<string, Maintenance[]>()
    for (const m of list ?? []) {
      const raw = eventDate(m, dateField)
      if (!raw) continue
      const key = toISODate(new Date(raw))
      if (!map.has(key)) map.set(key, [])
      map.get(key)!.push(m)
    }
    for (const arr of map.values()) {
      arr.sort((a, b) => new Date(eventDate(a, dateField)!).getTime() - new Date(eventDate(b, dateField)!).getTime())
    }
    return map
  }, [list, dateField])

  const projectionsByDay = useMemo(() => {
    const map = new Map<string, CalendarProjection[]>()
    for (const d of gridDays(from, to)) {
      const key = toISODate(d)
      map.set(key, projections.filter((p) => projectionCoversDay(p, key)))
    }
    return map
  }, [projections, from, to])

  const visibleProjections = useMemo(() => {
    const fromISO = toISODate(from)
    const toISO = toISODate(to)
    return projections
      .filter((p) => projectionOverlapsRange(p, fromISO, toISO))
      .sort((a, b) => a.window_start.localeCompare(b.window_start))
  }, [projections, from, to])

  function shift(delta: number) {
    if (view === 'day') setAnchor((a) => addDays(a, delta))
    else if (view === 'week') setAnchor((a) => addDays(a, delta * 7))
    else setAnchor((a) => new Date(a.getFullYear(), a.getMonth() + delta, 1))
  }

  const totalEvents = list?.length ?? 0

  const statusCounts = useMemo(() => {
    const counts = { pending: 0, in_progress: 0, completed: 0 }
    for (const m of list ?? []) counts[m.status]++
    return counts
  }, [list])

  return (
    <>
      <PageHeader
        title="Takvim"
        subtitle="Bakım iş emirlerini ve tahmini bakım pencerelerini günlük, haftalık ve aylık görünümde inceleyin."
        actions={
          <div className="row" style={{ gap: 8 }}>
            <label className="row" style={{ gap: 6, fontSize: 12.5 }}>
              <span className="muted">Takvimde göster:</span>
              <select value={dateField} onChange={(e) => setDateField(e.target.value as DateField)}>
                <option value="created">İş emrinin açılış tarihi</option>
                <option value="completed">İş emrinin tamamlanma tarihi</option>
              </select>
            </label>
          </div>
        }
      />

      <div className="card" style={{ marginBottom: 12 }}>
        <div className="row" style={{ justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
          <div className="row" style={{ gap: 8 }}>
            <button className="small ghost" onClick={() => shift(-1)}>
              <ChevronLeft size={14} />
            </button>
            <button className="small ghost" onClick={() => setAnchor(startOfDay(new Date()))}>
              Bugün
            </button>
            <button className="small ghost" onClick={() => shift(1)}>
              <ChevronRight size={14} />
            </button>
            <input
              type="date"
              value={toISODate(anchor)}
              onChange={(e) => {
                if (!e.target.value) return
                setAnchor(startOfDay(new Date(`${e.target.value}T00:00:00`)))
              }}
              style={{ fontSize: 12.5, padding: '6px 10px' }}
              title="Tarihe git"
            />
            <span style={{ fontWeight: 700, fontSize: 15, marginLeft: 6 }}>
              {rangeTitle(anchor, view)}
            </span>
          </div>
          <Tabs
            items={[
              { key: 'day', label: 'Günlük' },
              { key: 'week', label: 'Haftalık' },
              { key: 'month', label: 'Aylık' },
            ]}
            active={view}
            onChange={(k) => setView(k as ViewMode)}
          />
        </div>
        <div
          className="row"
          style={{ justifyContent: 'space-between', flexWrap: 'wrap', gap: 10, marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border)' }}
        >
          <CalendarLegend />
          <div className="row" style={{ gap: 10, fontSize: 12.5 }}>
            <span className="muted">Bu aralıkta {totalEvents} kayıt:</span>
            {statusCounts.pending > 0 && <Badge tone="pending">{statusCounts.pending} Bekliyor</Badge>}
            {statusCounts.in_progress > 0 && <Badge tone="in_progress">{statusCounts.in_progress} Devam Ediyor</Badge>}
            {statusCounts.completed > 0 && <Badge tone="completed">{statusCounts.completed} Tamamlandı</Badge>}
          </div>
        </div>
      </div>

      {error && <div className="error" style={{ marginBottom: 12 }}>{error}</div>}

      {list === null ? (
        <div className="card"><div className="muted">Yükleniyor…</div></div>
      ) : view === 'day' ? (
        <>
          {totalEvents === 0 ? (
            <EmptyState
              icon={<Wrench size={22} />}
              title="Bu gün için bakım kaydı yok"
              description={`${from.toLocaleDateString('tr-TR')} tarihinde oluşturulmuş/tamamlanmış bir iş emri bulunamadı.`}
            />
          ) : (
            <DayAgenda events={byDate.get(toISODate(from)) ?? []} dateField={dateField} onOpenMaintenance={onOpenMaintenance} />
          )}
          <ProjectionsPanel projections={visibleProjections} onOpenMaintenance={onOpenMaintenance} />
        </>
      ) : (
        <>
          <div className="card cal-card">
            <div className="cal-grid cal-weekday-header">
              {DOW_TR.map((d) => (
                <div key={d} className="cal-weekday">{d}</div>
              ))}
            </div>
            <div className={`cal-grid ${view === 'week' ? 'cal-grid-week' : 'cal-grid-month'}`}>
              {gridDays(from, to).map((d) => (
                <DayCell
                  key={toISODate(d)}
                  date={d}
                  inMonth={view === 'week' ? true : d.getMonth() === anchor.getMonth()}
                  events={byDate.get(toISODate(d)) ?? []}
                  projections={projectionsByDay.get(toISODate(d)) ?? []}
                  onOpenMaintenance={onOpenMaintenance}
                  onOpenDay={(day) => {
                    setAnchor(day)
                    setView('day')
                  }}
                />
              ))}
            </div>
          </div>
          <ProjectionsPanel projections={visibleProjections} onOpenMaintenance={onOpenMaintenance} />
          {totalEvents === 0 && visibleProjections.length === 0 && (
            <EmptyState
              icon={<CalendarDays size={22} />}
              title="Bu aralıkta bakım yok"
              description="Seçili tarih aralığında iş emri veya tahmini bakım penceresi bulunamadı."
            />
          )}
        </>
      )}
    </>
  )
}

function gridDays(from: Date, to: Date): Date[] {
  const days: Date[] = []
  let cur = from
  while (cur.getTime() <= to.getTime()) {
    days.push(cur)
    cur = addDays(cur, 1)
  }
  return days
}

// ---------------------------------------------------------- Günlük ajanda
function DayAgenda({
  events,
  dateField,
  onOpenMaintenance,
}: {
  events: Maintenance[]
  dateField: DateField
  onOpenMaintenance: (id: number) => void
}) {
  return (
    <div className="table-card fade-in">
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Saat</th>
              <th>Araç</th>
              <th>Periyot</th>
              <th>Durum</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {events.map((m) => (
              <tr key={m.id} className="clickable-row" onClick={() => onOpenMaintenance(m.id)}>
                <td style={{ fontWeight: 600 }}>{fmtTime(eventDate(m, dateField))}</td>
                <td><span className="vehicle-code">{m.vehicle.code}</span></td>
                <td>{m.plan.name}</td>
                <td><Badge tone={m.status}>{STATUS_TR[m.status]}</Badge></td>
                <td className="muted">#{m.id}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
