import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { Maintenance } from '../types'
import { STATUS_TR } from '../types'
import { PageHeader, Tabs, EmptyState, Badge } from '../components/ui'
import { ChevronLeft, ChevronRight, CalendarDays, Wrench, PlayCircle } from 'lucide-react'
import { targetLabel } from '../format'

const fmtKm = (n: number) => n.toLocaleString('tr-TR')
// Süre bazlı (aylık/yıllık) aday iş emirlerinde gerçek hedef target_date'de —
// backend km alanını bu durumda gizler (null).
const hedefLabel = (m: Maintenance) => targetLabel(m.target_km, m.target_date, m.vehicle.model)

const DAILY_KM = 300

type ViewMode = 'day' | 'week' | 'month'

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

// Kullanıcının onayladığı km-tolerans tablosuyla birebir aynı kural (bkz.
// backend services.MAX_TOLERANCE_KM ve MaintenancesPage.tsx'teki
// estimateWindow): yüzdelik tolerans periyot büyüdükçe sınırsız büyümez,
// her iki yönde de en fazla 3.000 km'de tavanlanır. Bu değer artık
// haftalık görünümde iş emri barının üzerinde METİN olarak da
// gösterildiğinden (bkz. DayCell), planın GERÇEK tolerans kuralıyla
// hesaplanması şart — eskiden burada sabit bir "2500 km payı" kullanılıyordu,
// bu da Bakımlar sayfasındaki (estimateWindow) gerçek tolerans hesabıyla
// TUTARSIZDI.
const MAX_TOLERANCE_KM = 3000

/** Bir iş emrinin planın ÜST tolerans sınırına göre en geç bitirilmesi
 * gereken tahmini tarihi — DAILY_KM varsayımıyla aracın güncel km'sinden
 * kaç gün sonra bu noktaya ulaşacağı hesaplanır. Süre bazlı (aylık/yıllık)
 * iş emirlerinde hedef zaten bir takvim tarihidir (target_date) — km
 * projeksiyonu yerine doğrudan o tarih kullanılır. */
function latestDeadline(m: Maintenance): Date {
  if (m.target_date) return new Date(m.target_date)
  const upperDist = m.plan.threshold_type === 'km'
    ? m.plan.upper_km
    : Math.min((m.plan.interval_km * m.plan.upper_pct) / 100, MAX_TOLERANCE_KM)
  const upperKm = (m.target_km ?? 0) + upperDist
  const remainingUpper = upperKm - m.vehicle.current_km
  const days = Math.max(0, Math.ceil(remainingUpper / DAILY_KM))
  const d = new Date()
  d.setDate(d.getDate() + days)
  return d
}

const fmtDate = (d: Date) => d.toLocaleDateString('tr-TR')

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
        <span className="cal-legend-today" /> Bugün
      </span>
    </div>
  )
}

// ---------------------------------------------------------- Ay/Hafta hücresi
function DayCell({
  date,
  inMonth,
  events,
  onOpenMaintenance,
  onOpenDay,
  showDeadline,
}: {
  date: Date
  inMonth: boolean
  events: Maintenance[]
  onOpenMaintenance: (id: number) => void
  onOpenDay: (d: Date) => void
  // Haftalık görünümde satırlar zaten daha uzun (bkz. .cal-grid-week) —
  // bu ekstra alanı değerlendirip her barın altında "en geç X tarihinde"
  // bilgisini de gösterir. Aylık görünümde (satırlar dar) eskisi gibi
  // yalnızca araç + periyot adı tek satırda kalır.
  showDeadline: boolean
}) {
  const isToday = toISODate(date) === toISODate(new Date())
  const sorted = sortByStatusPriority(events)
  const visible = sorted.slice(0, 3)
  const extra = sorted.length - visible.length
  return (
    <div
      className={`cal-cell ${inMonth ? '' : 'cal-out'} ${isToday ? 'cal-today' : ''}`}
      onClick={() => onOpenDay(date)}
    >
      <div className="cal-daynum">{date.getDate()}</div>
      <div className="cal-chips">
        {visible.map((m) => (
          <button
            key={m.id}
            className={`cal-chip cal-${m.status} ${showDeadline ? 'cal-chip-detailed' : ''}`}
            onClick={(e) => {
              e.stopPropagation()
              onOpenMaintenance(m.id)
            }}
            title={`${m.vehicle.code} — ${m.plan.name} (${STATUS_TR[m.status]})`}
          >
            <span className="cal-chip-title">{m.vehicle.code} · {m.plan.name}</span>
            {showDeadline && (
              <span className="cal-chip-deadline">
                {m.status === 'completed' ? 'Tamamlandı' : `En geç ${fmtDate(latestDeadline(m))}`}
              </span>
            )}
          </button>
        ))}
        {extra > 0 && <div className="cal-more">+{extra} daha</div>}
      </div>
    </div>
  )
}

// ---------------------------------------------------------- Takvim sayfası
export default function CalendarPage({
  perms,
  onOpenMaintenance,
}: {
  perms: Record<string, boolean>
  onOpenMaintenance: (id: number) => void
}) {
  // Faz 26 — Bakım başlatma onayı: Bakımlar sayfasındaki gibi burada da iki
  // ayrı bölüm var — "Planlananlar" (başlatılmış, mevcut takvim görünümü,
  // varsayılan) ve "Planlamayı Bekleyenler" (henüz başlatılmamış adaylar).
  // Teknisyenin start_maintenance yetkisi olmadığından sekme hiç gösterilmez
  // ve teknisyen her zaman yalnızca Planlananlar'ı (mevcut takvim
  // görünümünü) görür.
  const [mainView, setMainView] = useState<'planned' | 'pending'>('planned')
  const [pendingStart, setPendingStart] = useState<Maintenance[] | null>(null)
  const [pendingStartBusy, setPendingStartBusy] = useState<number | null>(null)
  const [pendingStartError, setPendingStartError] = useState('')

  const loadPendingStart = useCallback(() => {
    if (!perms.start_maintenance) return
    api<Maintenance[]>('/maintenances/pending-start')
      .then(setPendingStart)
      .catch(() => setPendingStart([]))
  }, [perms.start_maintenance])

  useEffect(() => {
    loadPendingStart()
  }, [loadPendingStart])

  // Kullanıcı isteği: sayfa ilk açıldığında (ya da bir yerden otomatik
  // gelindiğinde) varsayılan görünüm Haftalık olsun — Aylık'a geçiş hâlâ
  // sekmeden yapılabilir, yalnızca başlangıç değeri değişti.
  const [view, setView] = useState<ViewMode>('week')
  const [anchor, setAnchor] = useState<Date>(() => startOfDay(new Date()))
  const [list, setList] = useState<Maintenance[] | null>(null)
  const [error, setError] = useState('')

  // Bakımlar sayfasıyla aynı yaklaşım: tarih aralığına göre sunucu tarafında
  // filtrelemek yerine tüm başlatılmış iş emirlerini bir kez çeker (dataset
  // büyüklüğü Bakımlar sayfasında zaten kanıtlanmış) — her iş emri açılış
  // tarihinden en geç bitirilmesi gereken tarihe kadar birden fazla günde
  // gösterilebildiğinden (bkz. eventsByDay), tek bir gün/aralık filtresi
  // yeterli olmazdı.
  const loadList = useCallback(() => {
    setError('')
    api<Maintenance[]>('/maintenances')
      .then(setList)
      .catch((e) => {
        setError((e as Error).message)
        setList([])
      })
  }, [])

  useEffect(() => {
    loadList()
  }, [loadList])

  async function startPendingMaintenance(id: number) {
    setPendingStartBusy(id)
    setPendingStartError('')
    try {
      await api(`/maintenances/${id}/start`, { method: 'POST' })
      loadPendingStart()
      // Başlatılan bakım artık "Planlananlar" görünümüne dahil olmalı.
      loadList()
    } catch (e) {
      setPendingStartError((e as Error).message)
    } finally {
      setPendingStartBusy(null)
    }
  }

  const { from, to } = useMemo(() => getRange(anchor, view), [anchor, view])

  // Bir iş emrinin takvimde görüneceği [başlangıç, bitiş] gün aralığı:
  // - Tamamlanmışsa YALNIZCA tamamlandığı gün gösterilir (devamında
  //   gösterilmesine gerek kalmaz).
  // - Tamamlanmamışsa açılış tarihinden, planın üst tolerans sınırına göre
  //   en geç bitirilmesi gereken tahmini tarihe kadar HER GÜN gösterilir —
  //   süresi geçmiş ama hâlâ tamamlanmamışsa (nadir durum), gözden
  //   kaybolmasın diye en azından bugüne kadar uzatılır.
  function displayRange(m: Maintenance, todayISO: string): { startISO: string; endISO: string } | null {
    if (m.status === 'completed') {
      if (!m.completed_at) return null
      const day = toISODate(new Date(m.completed_at))
      return { startISO: day, endISO: day }
    }
    if (!m.created_at) return null
    const startISO = toISODate(new Date(m.created_at))
    const deadlineISO = toISODate(latestDeadline(m))
    const endISO = deadlineISO > todayISO ? deadlineISO : todayISO
    return { startISO, endISO: endISO > startISO ? endISO : startISO }
  }

  const eventsByDay = useMemo(() => {
    const todayISO = toISODate(new Date())
    const days = gridDays(from, to).map(toISODate)
    const map = new Map<string, Maintenance[]>()
    for (const d of days) map.set(d, [])
    for (const m of list ?? []) {
      const range = displayRange(m, todayISO)
      if (!range) continue
      for (const d of days) {
        if (d >= range.startISO && d <= range.endISO) map.get(d)!.push(m)
      }
    }
    for (const arr of map.values()) {
      arr.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime())
    }
    return map
  }, [list, from, to])

  // Görünen aralıkla kesişen (spanning) iş emirleri — üstteki özet
  // rozetlerinde ve boş durum kontrolünde tekrarsız sayı için kullanılır.
  const visibleMaintenances = useMemo(() => {
    const todayISO = toISODate(new Date())
    const fromISO = toISODate(from)
    const toISO = toISODate(to)
    return (list ?? []).filter((m) => {
      const range = displayRange(m, todayISO)
      if (!range) return false
      return range.startISO <= toISO && range.endISO >= fromISO
    })
  }, [list, from, to])

  function shift(delta: number) {
    if (view === 'day') setAnchor((a) => addDays(a, delta))
    else if (view === 'week') setAnchor((a) => addDays(a, delta * 7))
    else setAnchor((a) => new Date(a.getFullYear(), a.getMonth() + delta, 1))
  }

  const totalEvents = visibleMaintenances.length

  const statusCounts = useMemo(() => {
    const counts = { pending: 0, in_progress: 0, completed: 0 }
    for (const m of visibleMaintenances) counts[m.status]++
    return counts
  }, [visibleMaintenances])

  return (
    <>
      <PageHeader
        title="Takvim"
      />

      {!!perms.start_maintenance && (
        <div className="row calendar-view-tabs" style={{ gap: 10, marginBottom: 16 }}>
          <button
            className={mainView === 'planned' ? 'primary' : ''}
            style={{ padding: '11px 22px', fontSize: 14.5, fontWeight: 700 }}
            onClick={() => setMainView('planned')}
          >
            <CalendarDays size={16} style={{ marginRight: 8, verticalAlign: -3 }} />
            Planlananlar
          </button>
          <button
            className={mainView === 'pending' ? 'primary' : ''}
            style={{ padding: '11px 22px', fontSize: 14.5, fontWeight: 700 }}
            onClick={() => setMainView('pending')}
          >
            <PlayCircle size={16} style={{ marginRight: 8, verticalAlign: -3 }} />
            Planlamayı Bekleyenler
            {!!pendingStart && pendingStart.length > 0 && (
              <span
                style={{
                  marginLeft: 8, background: mainView === 'pending' ? 'rgba(255,255,255,0.28)' : 'var(--amber-light)',
                  color: mainView === 'pending' ? '#fff' : 'var(--amber)',
                  borderRadius: 999, padding: '1px 9px', fontSize: 12.5, fontWeight: 700,
                }}
              >
                {pendingStart.length}
              </span>
            )}
          </button>
        </div>
      )}

      {mainView === 'pending' && !!perms.start_maintenance && (
        <div className="card fade-in" style={{ marginBottom: 16 }}>
          <div className="card-head">
            <div>
              <div className="card-title">
                <PlayCircle size={15} style={{ marginRight: 6, verticalAlign: -3, color: 'var(--primary-600)' }} />
                Başlatılmayı Bekleyenler
              </div>
              <div className="card-subtitle">
                Otomatik olarak açılan bu bakımlar henüz başlatılmadı — başlatılmadıkça Takvim'de ve
                Bakımlar listesinde görünmezler.
              </div>
            </div>
            <Badge tone="pending">{pendingStart?.length ?? 0}</Badge>
          </div>
          {pendingStartError && <div className="error">{pendingStartError}</div>}
          {pendingStart && pendingStart.length === 0 && (
            <EmptyState
              icon={<PlayCircle size={22} />}
              title="Başlatılmayı bekleyen bakım yok"
              description="Otomatik açılan tüm adaylar zaten başlatılmış."
            />
          )}
          {pendingStart && pendingStart.length > 0 && (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Araç</th>
                    <th>Periyot</th>
                    <th>Hedef</th>
                    <th>Açılış KM</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {pendingStart.map((m) => (
                    <tr key={m.id}>
                      <td><span className="vehicle-code">{m.vehicle.code}</span> {m.vehicle.name}</td>
                      <td>{m.plan.name}</td>
                      <td>{hedefLabel(m)}</td>
                      <td>{fmtKm(m.km_at_open)} km</td>
                      <td>
                        <button
                          className="small primary"
                          disabled={pendingStartBusy === m.id}
                          onClick={() => startPendingMaintenance(m.id)}
                        >
                          <PlayCircle size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                          {pendingStartBusy === m.id ? 'Başlatılıyor…' : 'Başlat'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {mainView === 'planned' && (
      <>
      <div className="card calendar-controls" style={{ marginBottom: 12 }}>
        <div className="row calendar-controls-main" style={{ justifyContent: 'space-between', flexWrap: 'wrap', gap: 10 }}>
          <div className="row calendar-navigation" style={{ gap: 8 }}>
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
            <span className="calendar-range-title">
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
          className="row calendar-meta"
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
              description={`${from.toLocaleDateString('tr-TR')} tarihinde açık ya da tamamlanmış bir iş emri bulunamadı.`}
            />
          ) : (
            <DayAgenda events={eventsByDay.get(toISODate(from)) ?? []} onOpenMaintenance={onOpenMaintenance} />
          )}
        </>
      ) : (
        <>
          <div className="card cal-card fade-in">
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
                  events={eventsByDay.get(toISODate(d)) ?? []}
                  onOpenMaintenance={onOpenMaintenance}
                  onOpenDay={(day) => {
                    setAnchor(day)
                    setView('day')
                  }}
                  showDeadline={view === 'week'}
                />
              ))}
            </div>
          </div>
          {totalEvents === 0 && (
            <EmptyState
              icon={<CalendarDays size={22} />}
              title="Bu aralıkta bakım yok"
              description="Seçili tarih aralığında başlatılmış bir iş emri bulunamadı."
            />
          )}
        </>
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
  onOpenMaintenance,
}: {
  events: Maintenance[]
  onOpenMaintenance: (id: number) => void
}) {
  return (
    <div className="table-card fade-in">
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Araç</th>
              <th>Periyot</th>
              <th>Durum</th>
              <th>Açılış</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {events.map((m) => (
              <tr key={m.id} className="clickable-row" onClick={() => onOpenMaintenance(m.id)}>
                <td><span className="vehicle-code">{m.vehicle.code}</span></td>
                <td>{m.plan.name}</td>
                <td><Badge tone={m.status}>{STATUS_TR[m.status]}</Badge></td>
                <td className="muted">{fmtTime(m.created_at)}</td>
                <td className="muted">#{m.id}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
