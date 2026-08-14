import { useMemo, useState } from 'react'
import type { Maintenance, User } from '../types'
import { ChevronLeft, ChevronRight, CalendarDays, Wrench, UserCheck, CheckCircle2, Users } from 'lucide-react'

/* =============================================================================
 * Usta Bakım Takvimi (Faz 27)
 *
 * Bakımlar sayfasında, listeli görünümün ÜSTÜNDE duran aylık takvim. İKİ
 * SEKMESİ vardır (kullanıcı isteği):
 *   • "Bana Atanan Bakımlar" — yalnızca bu kullanıcıya atanmış iş emirleri
 *     (tamamladıkları dahil), kendi iş listesini tarihleriyle görsün diye.
 *   • "Genel Bakımlar"       — erişebildiği TÜM iş emirleri.
 * Sekme değişince ay/gün seçimi korunur, yalnızca içerik süzülür.
 *
 * Yalnızca GERÇEKTEN başlatılmış (is_activated) iş emirlerini gösterir, üç renkte:
 *   • "Tamamlandı"   — yeşil; geçmiş günlerde de görünür (bkz. aşağıda)
 *   • "Bana atanan"  — assigned_to bu kullanıcı (mavi)
 *   • "Başlatılan"   — atanmamış / başkasına atanmış açık iş emirleri (turuncu)
 * Henüz başlatılmamış adaylar (Başlatılmayı Bekleyenler) burada YER ALMAZ.
 *
 * Takvimdeki gün seçimi:
 *   • TAMAMLANMIŞ iş emri, gerçekte tamamlandığı güne (completed_at) düşer —
 *     yönetici "Tamamlandı" dediği anda takvimde o güne yeşil olarak oturur.
 *   • Açık iş emri, planlanan gününe düşer: planned_date → scheduled_start_at
 *     → started_at → target_date → created_at.
 * Tamamlanmışta "tamamlandı" durumu atamadan/atanmışlıktan ÖNCE gelir: bitmiş
 * bir iş, size atanmış olsa da yeşil görünür.
 * ========================================================================== */

const DAY_NAMES = ['Pzt', 'Sal', 'Çar', 'Per', 'Cum', 'Cmt', 'Paz']
const MONTH_NAMES = [
  'Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
  'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık',
]

/** Yerel saate göre YYYY-MM-DD (toISOString UTC'ye kaydırdığı için kullanılmaz). */
function dayKey(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${m}-${day}`
}

/** Bir iş emrinin takvimde hangi güne düşeceği. */
function maintenanceDayKey(m: Maintenance): string {
  const raw = m.status === 'completed' && m.completed_at
    ? m.completed_at
    : m.planned_date || m.scheduled_start_at || m.started_at || m.target_date || m.created_at
  if (!raw) return ''
  // 'YYYY-MM-DD' biçimindeki (saatsiz) değerleri Date'e sokmadan kullan —
  // aksi halde UTC yorumu yüzünden bir gün geriye kayabilir.
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) return raw
  const d = new Date(raw)
  return isNaN(d.getTime()) ? '' : dayKey(d)
}

export default function MaintenanceCalendar({
  maintenances,
  user,
  onOpenMaintenance,
}: {
  /** Takvimde gösterilecek iş emirleri — açık olanlar + tamamlananlar. */
  maintenances: Maintenance[]
  user: User
  onOpenMaintenance: (id: number) => void
}) {
  const today = new Date()
  const [cursor, setCursor] = useState(() => new Date(today.getFullYear(), today.getMonth(), 1))
  const [selectedDay, setSelectedDay] = useState<string | null>(dayKey(today))
  // Hangi sekme açık: kendi işlerim mi, herkesinki mi.
  const [scope, setScope] = useState<'mine' | 'all'>('mine')

  const isDone = (m: Maintenance) => m.status === 'completed'
  // Bana atanmış mı — tamamlanmış olsa bile "benim işim" sayılır (sekme
  // süzmesi için). Renk seçiminde ise tamamlanmışlık öne geçer (bkz. toneOf).
  const assignedToMe = (m: Maintenance) => m.assigned_to?.id === user.id
  const isMine = (m: Maintenance) => !isDone(m) && assignedToMe(m)
  /** Renk sınıfı — tamamlanmış her zaman yeşil, sonra atama durumuna bakılır. */
  const toneOf = (m: Maintenance) => (isDone(m) ? 'done' : isMine(m) ? 'mine' : 'open')

  // Sekmeye göre süzülmüş liste — takvimin geri kalanı hep bunu kullanır.
  const visible = useMemo(
    () => (scope === 'mine' ? maintenances.filter((m) => m.assigned_to?.id === user.id) : maintenances),
    [maintenances, scope, user.id],
  )
  const mineTotal = useMemo(
    () => maintenances.filter((m) => m.assigned_to?.id === user.id).length,
    [maintenances, user.id],
  )

  // Gün -> o güne düşen iş emirleri.
  const byDay = useMemo(() => {
    const map = new Map<string, Maintenance[]>()
    for (const m of visible) {
      const k = maintenanceDayKey(m)
      if (!k) continue
      const list = map.get(k)
      if (list) list.push(m)
      else map.set(k, [m])
    }
    // Aynı gün içinde önce yapılacak işler (kendine atananlar başta),
    // tamamlananlar en sonda listelensin.
    const rank = (m: Maintenance) => (isMine(m) ? 0 : isDone(m) ? 2 : 1)
    for (const list of map.values()) {
      list.sort((a, b) => rank(a) - rank(b) || a.id - b.id)
    }
    return map
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, user.id])

  // Ay ızgarası — haftalar Pazartesi başlar.
  const cells = useMemo(() => {
    const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1)
    const offset = (first.getDay() + 6) % 7 // Pazar=0 -> 6
    const start = new Date(first)
    start.setDate(first.getDate() - offset)
    const out: Date[] = []
    for (let i = 0; i < 42; i++) {
      const d = new Date(start)
      d.setDate(start.getDate() + i)
      out.push(d)
    }
    // Son hafta tamamen bir sonraki aya aitse gösterme (5 satır yeter).
    if (out.slice(35).every((d) => d.getMonth() !== cursor.getMonth())) return out.slice(0, 35)
    return out
  }, [cursor])

  const mineCount = visible.filter(isMine).length
  const doneCount = visible.filter(isDone).length
  const openCount = visible.length - mineCount - doneCount
  const todayKey = dayKey(today)
  const selectedList = selectedDay ? byDay.get(selectedDay) ?? [] : []

  function shiftMonth(delta: number) {
    setCursor((c) => new Date(c.getFullYear(), c.getMonth() + delta, 1))
  }

  return (
    <div className="card mnt-cal fade-in">
      <div className="card-head">
        <div>
          <div className="card-title">
            <CalendarDays size={15} style={{ marginRight: 6, verticalAlign: -3 }} />
            Bakım Takvimi
          </div>
          <div className="card-subtitle">
            {scope === 'mine'
              ? 'Size atanan bakımlar ve tarihleri — bir güne tıklayın.'
              : 'Başlatılmış tüm bakımlar ve tarihleri — bir güne tıklayın.'}
          </div>
        </div>
        <div className="row" style={{ gap: 6, alignItems: 'center' }}>
          <button className="small ghost" onClick={() => shiftMonth(-1)} aria-label="Önceki ay">
            <ChevronLeft size={14} />
          </button>
          <span className="mnt-cal-month">
            {MONTH_NAMES[cursor.getMonth()]} {cursor.getFullYear()}
          </span>
          <button className="small ghost" onClick={() => shiftMonth(1)} aria-label="Sonraki ay">
            <ChevronRight size={14} />
          </button>
          <button
            className="small ghost"
            onClick={() => {
              setCursor(new Date(today.getFullYear(), today.getMonth(), 1))
              setSelectedDay(todayKey)
            }}
          >
            Bugün
          </button>
        </div>
      </div>

      <div className="row mnt-cal-tabs" style={{ gap: 8, flexWrap: 'wrap' }}>
        {([
          ['mine', 'Bana Atanan Bakımlar', UserCheck, mineTotal],
          ['all', 'Genel Bakımlar', Users, maintenances.length],
        ] as const).map(([key, label, Icon, count]) => (
          <button
            key={key}
            className={scope === key ? 'primary' : ''}
            style={{ padding: '8px 14px', fontSize: 13, fontWeight: 700 }}
            onClick={() => setScope(key)}
          >
            <Icon size={14} style={{ marginRight: 7, verticalAlign: -3 }} />
            {label}
            <span className="mnt-cal-tab-count">{count}</span>
          </button>
        ))}
      </div>

      <div className="mnt-cal-legend">
        <span className="mnt-cal-key">
          <i className="mnt-chip-swatch mine" /> Bana atanan ({mineCount})
        </span>
        {(scope === 'all' || openCount > 0) && (
          <span className="mnt-cal-key">
            <i className="mnt-chip-swatch open" /> Başlatılan ({openCount})
          </span>
        )}
        <span className="mnt-cal-key">
          <i className="mnt-chip-swatch done" /> Tamamlandı ({doneCount})
        </span>
      </div>

      <div className="mnt-cal-grid">
        {DAY_NAMES.map((d) => (
          <div key={d} className="mnt-cal-dayname">{d}</div>
        ))}
        {cells.map((d) => {
          const key = dayKey(d)
          const list = byDay.get(key) ?? []
          const outside = d.getMonth() !== cursor.getMonth()
          const classes = [
            'mnt-cal-cell',
            outside ? 'outside' : '',
            key === todayKey ? 'today' : '',
            key === selectedDay ? 'selected' : '',
          ].filter(Boolean).join(' ')
          return (
            <button key={key} className={classes} onClick={() => setSelectedDay(key)}>
              <span className="mnt-cal-daynum">{d.getDate()}</span>
              <span className="mnt-cal-chips">
                {list.slice(0, 3).map((m) => (
                  <span
                    key={m.id}
                    className={`mnt-cal-chip ${toneOf(m)}`}
                    title={`${m.vehicle.code} · ${m.plan.name}${
                      isDone(m) ? ' (tamamlandı)' : isMine(m) ? ' (size atandı)' : ''
                    }`}
                  >
                    {m.vehicle.code}
                  </span>
                ))}
                {list.length > 3 && <span className="mnt-cal-more">+{list.length - 3}</span>}
              </span>
            </button>
          )
        })}
      </div>

      <div className="mnt-cal-day">
        <div className="mnt-cal-day-head">
          {selectedDay
            ? new Date(`${selectedDay}T00:00:00`).toLocaleDateString('tr-TR', {
                day: 'numeric', month: 'long', year: 'numeric', weekday: 'long',
              })
            : 'Bir gün seçin'}
        </div>
        {selectedList.length === 0 ? (
          <div className="muted" style={{ fontSize: 13 }}>
            {scope === 'mine'
              ? 'Bu güne düşen, size atanmış bir bakım yok.'
              : 'Bu güne düşen bir bakım yok.'}
          </div>
        ) : (
          <div className="mnt-cal-day-list">
            {selectedList.map((m) => (
              <button
                key={m.id}
                className={`mnt-cal-item ${toneOf(m)}`}
                onClick={() => onOpenMaintenance(m.id)}
              >
                <span className="mnt-cal-item-icon">
                  {isDone(m) ? <CheckCircle2 size={14} /> : isMine(m) ? <UserCheck size={14} /> : <Wrench size={14} />}
                </span>
                <span className="mnt-cal-item-main">
                  <span className="mnt-cal-item-title">
                    <span className="vehicle-code">{m.vehicle.code}</span>
                    <span className="muted"> · {m.plan.name}</span>
                  </span>
                  {/* Kullanıcı isteği: atanan kişi araç/periyot satırının ALTINDA
                      görünsün — atanmamışsa da bu açıkça yazılır. */}
                  <span className="mnt-cal-item-sub">
                    {isDone(m) ? 'Tamamlandı' : isMine(m) ? 'Size atandı' : 'Atanan'}
                    {': '}
                    {m.assigned_to
                      ? m.assigned_to.full_name
                      : isMine(m) ? user.full_name : 'Atanmamış'}
                    {m.assigned_shift?.name && ` · ${m.assigned_shift.name}`}
                  </span>
                </span>
                <span className="mnt-cal-item-id">#{m.id}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
