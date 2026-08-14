import { useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { Tabs, EmptyState } from './ui'

/* =========================================================================
   PendingCalendar — CalendarPage'deki ana takvim ızgarasının (haftalık/aylık,
   cal-grid/cal-cell/cal-chip stilleri) küçültülmüş, salt-okunur bir versiyonu.
   Amaç: henüz başlatılmamış / henüz iş emri açılmamış "bekleyen" kayıtları da
   (Planlamayı Bekleyenler, Başlatılmayı Bekleyenler) bir tabloya ek olarak
   takvim üzerinde görselleştirebilmek. Sürükle-bırak YOK — bu liste henüz
   plana alınmadığı için bir güne "sabitlemek" anlamlı değil, yalnızca tahmini
   günü gösterir.
   Generic <T>: hem Maintenance (aday iş emirleri) hem UpcomingItem (henüz iş
   emri bile açılmamış projeksiyonlar) ile kullanılabilsin diye. ========== */

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
function startOfWeek(d: Date): Date {
  const x = startOfDay(d)
  const dow = (x.getDay() + 6) % 7
  return addDays(x, -dow)
}
function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1)
}
function endOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth() + 1, 0)
}

type MiniView = 'week' | 'month'

function getRange(anchor: Date, view: MiniView): { from: Date; to: Date } {
  if (view === 'week') {
    const from = startOfWeek(anchor)
    return { from, to: addDays(from, 6) }
  }
  const mStart = startOfMonth(anchor)
  const mEnd = endOfMonth(anchor)
  return { from: startOfWeek(mStart), to: addDays(startOfWeek(mEnd), 6) }
}

function rangeTitle(anchor: Date, view: MiniView): string {
  if (view === 'month') return `${MONTH_TR[anchor.getMonth()]} ${anchor.getFullYear()}`
  const { from, to } = getRange(anchor, view)
  const sameMonth = from.getMonth() === to.getMonth()
  return sameMonth
    ? `${from.getDate()}–${to.getDate()} ${MONTH_TR[from.getMonth()]} ${from.getFullYear()}`
    : `${from.getDate()} ${MONTH_TR[from.getMonth()]} – ${to.getDate()} ${MONTH_TR[to.getMonth()]} ${to.getFullYear()}`
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

export interface PendingChip {
  key: string | number
  label: string
  title?: string
  tone?: 'pending' | 'in_progress' | 'completed'
  onClick?: () => void
}

export function PendingCalendar<T>({
  items,
  getDate,
  renderChip,
  defaultView = 'week',
  emptyTitle = 'Bu aralıkta kayıt yok',
  emptyDescription,
}: {
  items: T[]
  /** Kaydın takvimde hangi güne düşeceği — null dönerse takvime dahil edilmez. */
  getDate: (item: T) => Date | null
  renderChip: (item: T) => PendingChip
  defaultView?: MiniView
  emptyTitle?: string
  emptyDescription?: string
}) {
  const [view, setView] = useState<MiniView>(defaultView)
  const [anchor, setAnchor] = useState<Date>(() => startOfDay(new Date()))

  const { from, to } = useMemo(() => getRange(anchor, view), [anchor, view])

  const eventsByDay = useMemo(() => {
    const map = new Map<string, T[]>()
    for (const d of gridDays(from, to)) map.set(toISODate(d), [])
    for (const item of items) {
      const date = getDate(item)
      if (!date) continue
      const iso = toISODate(date)
      if (map.has(iso)) map.get(iso)!.push(item)
    }
    return map
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, from, to])

  const totalVisible = useMemo(() => {
    const fromISO = toISODate(from)
    const toISO = toISODate(to)
    return items.filter((it) => {
      const d = getDate(it)
      if (!d) return false
      const iso = toISODate(d)
      return iso >= fromISO && iso <= toISO
    }).length
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [items, from, to])

  function shift(delta: number) {
    if (view === 'week') setAnchor((a) => addDays(a, delta * 7))
    else setAnchor((a) => new Date(a.getFullYear(), a.getMonth() + delta, 1))
  }

  return (
    <div>
      <div className="row" style={{ justifyContent: 'space-between', flexWrap: 'wrap', gap: 10, marginBottom: 10 }}>
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
          <span className="calendar-range-title">{rangeTitle(anchor, view)}</span>
        </div>
        <Tabs
          items={[
            { key: 'week', label: 'Haftalık' },
            { key: 'month', label: 'Aylık' },
          ]}
          active={view}
          onChange={(k) => setView(k as MiniView)}
        />
      </div>

      <div className="card cal-card fade-in" style={{ marginBottom: totalVisible === 0 ? 12 : 0 }}>
        <div className="cal-grid cal-weekday-header">
          {DOW_TR.map((d) => (
            <div key={d} className="cal-weekday">{d}</div>
          ))}
        </div>
        <div className={`cal-grid ${view === 'week' ? 'cal-grid-week' : 'cal-grid-month'}`}>
          {gridDays(from, to).map((d) => {
            const iso = toISODate(d)
            const isToday = iso === toISODate(new Date())
            const inMonth = view === 'week' ? true : d.getMonth() === anchor.getMonth()
            const events = eventsByDay.get(iso) ?? []
            const visible = events.slice(0, 3)
            const extra = events.length - visible.length
            return (
              <div key={iso} className={`cal-cell ${inMonth ? '' : 'cal-out'} ${isToday ? 'cal-today' : ''}`}>
                <div className="cal-daynum">{d.getDate()}</div>
                <div className="cal-chips">
                  {visible.map((item) => {
                    const chip = renderChip(item)
                    return (
                      <button
                        key={chip.key}
                        className={`cal-chip ${chip.tone ? `cal-${chip.tone}` : 'cal-pending'}`}
                        onClick={(e) => {
                          e.stopPropagation()
                          chip.onClick?.()
                        }}
                        title={chip.title}
                        style={chip.onClick ? undefined : { cursor: 'default' }}
                      >
                        <span className="cal-chip-title">{chip.label}</span>
                      </button>
                    )
                  })}
                  {extra > 0 && <div className="cal-more">+{extra} daha</div>}
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {totalVisible === 0 && (
        <EmptyState icon={undefined} title={emptyTitle} description={emptyDescription} />
      )}
    </div>
  )
}
