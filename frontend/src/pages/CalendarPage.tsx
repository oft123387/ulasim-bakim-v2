import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { Maintenance, UpcomingItem } from '../types'
import { PageHeader, Tabs, EmptyState, Badge } from '../components/ui'
import { ChevronLeft, ChevronRight, CalendarDays, Wrench, PlayCircle, CalendarClock, XCircle, RotateCcw } from 'lucide-react'
import { rawTargetLabel, mergedPeriodLabel } from '../format'

const fmtKm = (n: number) => n.toLocaleString('tr-TR')
// Süre bazlı (aylık/yıllık) aday iş emirlerinde gerçek hedef target_date'de —
// backend km alanını bu durumda gizler (null).
// Kademeli/kümülatif bakımlarda (ör. 6M iş emri aslında 3M ve 1M'yi de
// kapsıyorsa) yalnızca "yöneten" planın adını (m.plan.name = "6M")
// göstermek kapsanan küçük periyotları gizler — takvimdeki tüm rozetlerde
// (chip) bunun yerine mergedPeriodLabel ile "1M + 3M + 6M" gösterilir.
const periodLabel = (m: Maintenance) =>
  mergedPeriodLabel(m.covered_tiers, m.plan.plan_type, m.vehicle.model, m.plan.name)
// UpcomingItem'da plan_type alanı yok — backend yorumundaki kural aynı:
// süre bazlı maddelerde target_km=0'dır ve gerçek hedef target_date'dedir.
const upcomingPeriodLabel = (item: UpcomingItem) =>
  mergedPeriodLabel(
    item.covered_tiers,
    item.target_km === 0 && item.target_date ? 'time' : 'km',
    item.vehicle.model,
    item.plan_name,
  )

const DAILY_KM = 300
// Ay -> gün yaklaşık dönüşümü, yalnızca süre bazlı planların tolerans
// penceresini (ay cinsinden) takvim gününe çevirmek için — backend
// services.py'deki _AVG_DAYS_PER_MONTH ile AYNI sabit, aynı amaç.
const _AVG_DAYS_PER_MONTH = 30.44

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

// Kullanıcının onayladığı km-tolerans tablosuyla birebir aynı kural (bkz.
// backend services.MAX_TOLERANCE_KM ve MaintenancesPage.tsx'teki
// estimateWindow): yüzdelik tolerans periyot büyüdükçe sınırsız büyümez,
// her iki yönde de en fazla 3.000 km'de tavanlanır.
const MAX_TOLERANCE_KM = 3000

/** Bir iş emrinin planın ÜST tolerans sınırına göre en geç bitirilmesi
 * gereken tahmini tarihi — DAILY_KM varsayımıyla aracın güncel km'sinden
 * kaç gün sonra bu noktaya ulaşacağı hesaplanır. Süre bazlı (aylık/yıllık)
 * iş emirlerinde hedef tarihe (target_date) planın kendi üst toleransı
 * (upper_pct, ay -> gün yaklaşık dönüşümüyle) eklenir — km tarafıyla
 * TUTARLI: yalnızca hedef değil, hedefin GERÇEK tolerans sınırı gösterilir.
 * Kullanıcı isteği: takvimde bir iş emri (veya henüz başlamamış bir aday)
 * SADECE bu iki tarih (earliestDate/latestDeadline) arasına sürüklenebilir. */
// ÖNEMLİ yuvarlama kuralı: pencerenin iki sınırı da HER ZAMAN gerçek
// tolerans kadar (asla daha DAR olmayacak şekilde) hesaplanmalı — aksi halde
// kullanıcı "tolerans içinde" sandığı bir güne sürükleyince yuvarlama payı
// yüzünden reddedilebiliyordu (bildirilen hata). Bu yüzden pencere
// GENİŞLİĞİNİ ay/km'den güne çevirirken her zaman Math.ceil (yukarı
// yuvarlama — daha CÖMERT bir pencere) kullanılır; ardından üst sınıra
// eklenir, alt sınırdan çıkarılır.
function latestDeadline(m: Maintenance): Date {
  if (m.target_date) {
    const upperDays = Math.ceil(
      (((m.plan.interval_months ?? 0) * m.plan.upper_pct) / 100) * _AVG_DAYS_PER_MONTH,
    )
    const d = new Date(m.target_date)
    d.setDate(d.getDate() + upperDays)
    return d
  }
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

/** latestDeadline'ın ALT sınır karşılığı — bir iş emrinin (ya da henüz
 * başlatılmamış bir adayın) planın ALT tolerans sınırına göre en erken hangi
 * tarihe çekilebileceği. */
function earliestDate(m: Maintenance): Date {
  if (m.target_date) {
    const lowerDays = Math.ceil(
      (((m.plan.interval_months ?? 0) * m.plan.lower_pct) / 100) * _AVG_DAYS_PER_MONTH,
    )
    const d = new Date(m.target_date)
    d.setDate(d.getDate() - lowerDays)
    return d
  }
  const lowerDist = m.plan.threshold_type === 'km'
    ? m.plan.lower_km
    : Math.min((m.plan.interval_km * m.plan.lower_pct) / 100, MAX_TOLERANCE_KM)
  const lowerKm = (m.target_km ?? 0) - lowerDist
  const remainingLower = lowerKm - m.vehicle.current_km
  // remainingLower genelde NEGATİFTİR (alt sınır km'si zaten geçilmiş) —
  // Math.floor (sıfırdan uzağa, DAHA negatife yuvarlar) kullanılır ki alt
  // sınır tarihi olması gerekenden GEÇ değil, en azından olması gereken
  // kadar ERKEN çıksın (Math.ceil kullanılsaydı ~1 gün daha GEÇ, yani daha
  // DAR bir pencere üretiyordu — bildirilen hatanın kök nedeni buydu).
  const days = Math.floor(remainingLower / DAILY_KM)
  const d = new Date()
  d.setDate(d.getDate() + days)
  return d
}

const fmtDate = (d: Date) => d.toLocaleDateString('tr-TR')
/** ISO (YYYY-MM-DD) tarih string'ini yerel saatte ayrıştırıp okunur biçime
 * çevirir — bkz. CalItem.windowLower/windowUpper yorumu (saat dilimi
 * tuzaklarından kaçınmak için pencere sınırları string olarak tutulur, bu
 * yardımcı yalnızca EKRANDA GÖSTERİRKEN Date'e çevirir). */
const fmtISO = (iso: string) => fmtDate(new Date(`${iso}T00:00:00`))
/** Dar takvim hücrelerinde (haftalık görünüm barının altı) yıl olmadan
 * kısa "gg.aa" biçimi — tam tarih tooltip/hata mesajlarında zaten var. */
const fmtShortISO = (iso: string) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString('tr-TR', { day: '2-digit', month: '2-digit' })

// ------------------------------------------------------- Birleşik takvim öğesi
// Kullanıcı isteği: "Planlananlar" ve "Planlanmayı Bekleyenler" diye iki ayrı
// bölüm/takvim yerine TEK bir takvim — her öğe otomatik olarak kendi hedef
// noktasında (km/tarih) belirir, rengi durumuna göre değişir:
//   gray       = henüz başlatılmamış aday (is_activated=False)
//   pending    = başlatılmış, henüz üzerinde iş yapılmamış ("Bekliyor")
//   in_progress= üzerinde çalışılıyor
//   completed  = tamamlanmış
// Gerçek Maintenance nesneleri (list) ve henüz açılmamış adaylar (upcoming,
// maintenance_id=null) TEK bir CalItem[] dizisinde birleştirilir.
type CalColor = 'gray' | 'pending' | 'in_progress' | 'completed'

interface CalItem {
  key: string
  color: CalColor
  vehicleCode: string
  vehicleName: string
  label: string
  date: Date | null
  maintenanceId: number | null
  // ISO (YYYY-MM-DD) STRING olarak tutulur — Date nesneleri üzerinden
  // karşılaştırma yapmak "YYYY-MM-DD" (saat bilgisi olmayan) tarih
  // string'lerinin JS'te UTC olarak yorumlanması gibi saat dilimi
  // tuzaklarına açık; string karşılaştırması (ISO format sözlüksel olarak
  // da kronolojik sıradadır) bunu tamamen ortadan kaldırır.
  windowLower: string | null
  windowUpper: string | null
  // Sürüklenebilir mi — tamamlanmış işler ve zaten "İleri Tarihe Al" ile
  // kilitli (scheduled_start_at dolu) adaylar sürüklenemez.
  draggable: boolean
  locked: boolean
  // Kullanıcı elle sürükleyip taşımış mı (planned_date dolu) — bu durumda
  // "aslında" hangi güne denk geldiğini (naturalDate) ve eski haline
  // döndürme aksiyonunu göstermek için kullanılır.
  isManual: boolean
  naturalDate: Date | null
  source: Maintenance | UpcomingItem
}

const _CAL_PRIORITY: Record<CalColor, number> = { in_progress: 0, pending: 1, gray: 2, completed: 3 }
function sortByCalPriority(items: CalItem[]): CalItem[] {
  return [...items].sort((a, b) => _CAL_PRIORITY[a.color] - _CAL_PRIORITY[b.color])
}

const CAL_LABEL_TR: Record<CalColor, string> = {
  gray: 'Henüz Başlamadı',
  pending: 'Bekliyor',
  in_progress: 'Devam Ediyor',
  completed: 'Tamamlandı',
}

// ---------------------------------------------------------- Takvim lejantı
// Renk kodlamasının ne anlama geldiğini tek bakışta göstermek için — ızgara
// kendi başına yalnızca renkli kutucuklar olduğundan bu olmadan anlaşılması
// zor olabiliyordu.
function CalendarLegend() {
  return (
    <div className="row" style={{ gap: 16, flexWrap: 'wrap', fontSize: 12, color: 'var(--text-tertiary)' }}>
      <span className="row" style={{ gap: 6 }}>
        <span className="cal-legend-dot" style={{ background: 'var(--text-tertiary)' }} /> Henüz Başlamadı
      </span>
      <span className="row" style={{ gap: 6 }}>
        <span className="cal-legend-dot" style={{ background: 'var(--orange)' }} /> Bekliyor
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
  items,
  onOpenItem,
  onOpenDay,
  onDropItem,
  onRevertManual,
  canManage,
  dragOverISO,
  setDragOverISO,
}: {
  date: Date
  inMonth: boolean
  items: CalItem[]
  onOpenItem: (item: CalItem) => void
  onOpenDay: (d: Date) => void
  onDropItem: (key: string, iso: string) => void
  onRevertManual: (item: CalItem) => void
  canManage: boolean
  dragOverISO: string | null
  setDragOverISO: (iso: string | null) => void
}) {
  const iso = toISODate(date)
  const isToday = iso === toISODate(new Date())
  // Kullanıcı isteği: hiçbir öğe bugünden daha öne (geçmişe) sürüklenemez —
  // geçmiş günler bırakma hedefi olarak devre dışı bırakılır (preventDefault
  // ÇAĞRILMAZ ki tarayıcı varsayılan "izin verilmiyor" imlecini göstersin).
  const isPast = iso < toISODate(new Date())
  const hasDraggable = items.some((it) => it.draggable)
  const isDropTarget = !isPast && hasDraggable && dragOverISO === iso
  const sorted = sortByCalPriority(items)
  const visible = sorted.slice(0, 3)
  const extra = sorted.length - visible.length
  return (
    <div
      className={`cal-cell ${inMonth ? '' : 'cal-out'} ${isToday ? 'cal-today' : ''}`}
      style={isDropTarget ? { outline: '2px dashed var(--primary-600)', outlineOffset: -2, background: 'var(--primary-50, rgba(37,99,235,0.06))' } : undefined}
      onClick={() => onOpenDay(date)}
      onDragOver={(e) => { if (isPast) return; e.preventDefault(); if (dragOverISO !== iso) setDragOverISO(iso) }}
      onDragLeave={() => { if (dragOverISO === iso) setDragOverISO(null) }}
      onDrop={(e) => {
        if (isPast) return
        e.preventDefault()
        setDragOverISO(null)
        const key = e.dataTransfer.getData('text/plain')
        if (key) onDropItem(key, iso)
      }}
    >
      <div className="cal-daynum">{date.getDate()}</div>
      <div className="cal-chips">
        {visible.map((item) => {
          const showRevert = item.isManual && canManage
          return (
            <div
              key={item.key}
              className={`cal-chip cal-${item.color} cal-chip-detailed`}
              role="button"
              tabIndex={0}
              draggable={item.draggable}
              onDragStart={item.draggable ? (e) => {
                e.stopPropagation()
                e.dataTransfer.setData('text/plain', item.key)
                e.dataTransfer.effectAllowed = 'move'
              } : undefined}
              onClick={(e) => {
                e.stopPropagation()
                onOpenItem(item)
              }}
              onKeyDown={(e) => {
                if (e.key !== 'Enter' && e.key !== ' ') return
                e.preventDefault()
                e.stopPropagation()
                onOpenItem(item)
              }}
              title={
                `${item.vehicleCode} — ${item.label} (${CAL_LABEL_TR[item.color]})` +
                (item.color !== 'completed' && item.windowUpper
                  ? ` · En geç ${fmtISO(item.windowUpper)} tarihine kadar ileri alınabilir, bugünden itibaren geriye istediğiniz güne taşıyabilirsiniz`
                  : '') +
                (item.locked ? ' · zamanlandı, sürüklenemez' : item.draggable ? ' · başka güne sürükleyebilirsiniz' : '') +
                (item.isManual
                  ? ` · Elle taşındı${item.naturalDate ? ` — normalde ${fmtDate(item.naturalDate)} tarihinde görünecekti` : ''}`
                  : '')
              }
              style={{
                cursor: item.draggable ? 'grab' : 'pointer',
                paddingRight: showRevert ? 22 : undefined,
              }}
            >
              {showRevert && (
                <button
                  type="button"
                  className="cal-chip-revert"
                  title={`Eski (hesaplanan) tarihine döndür${item.naturalDate ? `: ${fmtDate(item.naturalDate)}` : ''}`}
                  onClick={(e) => {
                    e.stopPropagation()
                    onRevertManual(item)
                  }}
                >
                  <RotateCcw size={10} />
                </button>
              )}
              <span className="cal-chip-title">{item.vehicleCode} · {item.label}</span>
              {/* Kullanıcı isteği: her araç kartının altında en geç ne zaman
                  yapılması gerektiği tarih HER ZAMAN görünsün (yalnızca haftalık
                  görünümde ya da üzerine gelince değil). Elle taşınmış öğelerde
                  bunun yerine/yanında "Elle taşındı" etiketi görünür. */}
              <span className="cal-chip-deadline">
                {item.color === 'completed'
                  ? 'Tamamlandı'
                  : item.windowUpper
                    ? `En geç ${fmtShortISO(item.windowUpper)}`
                    : ''}
                {item.isManual && <span className="cal-chip-manual-tag"> · Elle taşındı</span>}
              </span>
            </div>
          )
        })}
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
  // Bakımlar sayfasındaki "Bakım Akışı > Başlatılmayı Bekleyenler" ile aynı
  // veri kaynağı — henüz bir iş emri bile açılmamış, sadece km/tarih
  // eşiğine yaklaşan (ya da başlatılmadığı için gecikmiş) araçlar. Kullanıcı
  // isteği: bunlar artık ayrı bir sekme/takvim değil, TEK takvimde gri
  // renkte, kendi hedef noktalarında gösterilir.
  const [upcoming, setUpcoming] = useState<UpcomingItem[] | null>(null)
  const [upcomingBusy, setUpcomingBusy] = useState<number | null>(null)
  const [upcomingError, setUpcomingError] = useState('')
  // "İleri tarihe zamanla" inline tarih seçici — bkz. MaintenancesPage.tsx'teki
  // aynı desen.
  const [scheduleOpenKey, setScheduleOpenKey] = useState<number | null>(null)
  const [scheduleDate, setScheduleDate] = useState('')

  const loadUpcoming = useCallback(() => {
    if (!perms.start_maintenance) return
    api<UpcomingItem[]>('/vehicles/alerts')
      .then(setUpcoming)
      .catch(() => setUpcoming([]))
  }, [perms.start_maintenance])

  useEffect(() => {
    loadUpcoming()
  }, [loadUpcoming])

  // Henüz gerçek bir iş emri açılmamış (maintenance_id=null) tüm adaylar —
  // takvimde gri renkte gösterilecek ve yönetim listesinde (aşağıda)
  // Başlat/İleri Tarihe Al/İptal Et işlemleriyle yönetilecek olanlar.
  const upcomingGray = useMemo(() => (upcoming ?? []).filter((x) => x.maintenance_id == null), [upcoming])

  // Kullanıcı isteği: "Yaklaşıyor/Planlanmayı Bekleyen" bir aracı iki adımda
  // değil, TEK tıkla doğrudan başlatabilsin — hem aday iş emrini açar hem de
  // aynı anda başlatır. scheduledDate verilirse iş emri hemen başlamaz,
  // yalnızca o tarihe zamanlanır.
  async function startUpcoming(item: UpcomingItem, scheduledDate?: string) {
    const busyKey = item.maintenance_id ?? -item.target_km
    setUpcomingBusy(busyKey)
    setUpcomingError('')
    try {
      const maintenanceId =
        item.maintenance_id ??
        (await api<Maintenance>('/maintenances/plan-upcoming', {
          method: 'POST', body: { vehicle_id: item.vehicle.id, target_km: item.target_km },
        })).id
      await api(`/maintenances/${maintenanceId}/start`, {
        method: 'POST',
        body: scheduledDate ? { scheduled_start_at: scheduledDate } : {},
      })
      loadUpcoming()
      if (!scheduledDate) loadList()
      setScheduleOpenKey(null)
    } catch (e) {
      setUpcomingError((e as Error).message)
    } finally {
      setUpcomingBusy(null)
    }
  }

  async function cancelSchedule(item: UpcomingItem) {
    const busyKey = item.maintenance_id ?? -item.target_km
    setUpcomingBusy(busyKey)
    setUpcomingError('')
    try {
      const maintenanceId =
        item.maintenance_id ??
        (await api<Maintenance>('/maintenances/plan-upcoming', {
          method: 'POST', body: { vehicle_id: item.vehicle.id, target_km: item.target_km },
        })).id
      await api(`/maintenances/${maintenanceId}/cancel-schedule`, { method: 'POST' })
      loadUpcoming()
    } catch (e) {
      setUpcomingError((e as Error).message)
    } finally {
      setUpcomingBusy(null)
    }
  }

  // Kullanıcı isteği: "ileri tarihe bakım başlatılınca ... takvime de
  // planlanan zamana gitsin ve değiştirilmesin" — "İleri Tarihe Al" ile bir
  // başlangıç tarihi zamanlandıysa (scheduled_start_at), takvimde HER ZAMAN
  // o gün gösterilir ve öğe artık sürüklenemez (kilitli); tahmini (km
  // projeksiyonu/hedef tarih) hesaba hiç girmez. Zamanlanmamışsa: süre
  // bazlı planlarda hedef zaten bir takvim tarihidir (target_date); km
  // bazlıda remaining_km gün cinsine çevrilip bugüne eklenir — bu, adayın
  // otomatik olarak "0 noktasında" (hesaplanan hedefinde) belirmesini sağlar.
  function upcomingEventDate(item: UpcomingItem): Date {
    if (item.scheduled_start_at) return new Date(item.scheduled_start_at)
    if (item.target_date) return new Date(item.target_date)
    const days = Math.max(0, Math.ceil(item.remaining_km / DAILY_KM))
    const d = new Date()
    d.setDate(d.getDate() + days)
    return d
  }

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

  const { from, to } = useMemo(() => getRange(anchor, view), [anchor, view])

  // Kullanıcı isteği: her iş emri takvimde YALNIZCA TEK bir günde gösterilir
  // (eskiden açılıştan son teslim tarihine kadar her günde görünüyordu). O gün:
  //  1) Kullanıcı iş emrini elle sürükleyip taşıdıysa planned_date.
  //  2) Tamamlanmışsa tamamlandığı gün.
  //  3) Süre bazlı planlarda hedef tarih (target_date).
  //  4) Aksi halde aracın tam hedef km'ye (ör. 200.000) ulaşacağı TAHMİNİ gün
  //     (DAILY_KM ile) — hedef zaten geçilmişse gözden kaybolmasın diye bugüne
  //     çekilir.
  // Elle taşıma (planned_date) HİÇ yokmuş gibi hesaplanan "doğal" gün —
  // manuel taşınmış bir öğenin aslında hangi güne denk geldiğini göstermek
  // (ve "eski haline döndür" sonrası nereye döneceğini önceden bilmek) için.
  function naturalEventDay(m: Maintenance): string | null {
    if (m.status === 'completed') return m.completed_at ? toISODate(new Date(m.completed_at)) : null
    if (m.target_date) return toISODate(new Date(m.target_date))
    if (m.target_km == null) return null
    const days = Math.max(0, Math.ceil((m.target_km - m.vehicle.current_km) / DAILY_KM))
    const d = new Date()
    d.setDate(d.getDate() + days)
    return toISODate(d)
  }

  function eventDay(m: Maintenance): string | null {
    if (m.planned_date) return m.planned_date.slice(0, 10)
    return naturalEventDay(m)
  }

  const canDrag = !!perms.start_maintenance

  // Tek birleşik takvim: başlatılmış iş emirleri (list) + henüz açılmamış
  // adaylar (upcomingGray) TEK bir CalItem[] dizisinde birleştirilir. Bir
  // adayın gerçek iş emri açılınca (maintenance_id dolduğunda) upcomingGray
  // filtresinden zaten düşer, böylece aynı bakım İKİ KEZ gösterilmez.
  const calItems = useMemo(() => {
    const items: CalItem[] = []
    for (const m of list ?? []) {
      // Kullanıcı isteği (güncellendi): tamamlanan bakımlar da takvimde
      // görünsün — önceki "tamamlananlar takvimde gösterilmesin" kararı geri
      // alındı. Tamamlanmış bir öğe completed_at gününde (ya da elle
      // taşınmışsa planned_date'te) görünür, sürüklenemez.
      const day = eventDay(m)
      const naturalDay = naturalEventDay(m)
      items.push({
        key: `m-${m.id}`,
        color: m.status,
        vehicleCode: m.vehicle.code,
        vehicleName: m.vehicle.name,
        label: periodLabel(m),
        date: day ? new Date(`${day}T00:00:00`) : null,
        maintenanceId: m.id,
        windowLower: toISODate(earliestDate(m)),
        windowUpper: toISODate(latestDeadline(m)),
        draggable: canDrag && m.status !== 'completed',
        locked: false,
        // planned_date doluysa bu öğe takvimde elle sürüklenip taşınmış —
        // "eski haline döndür" aksiyonu ve doğal (hesaplanan) tarih tooltip'i
        // için işaretlenir.
        isManual: !!m.planned_date,
        naturalDate: naturalDay ? new Date(`${naturalDay}T00:00:00`) : null,
        source: m,
      })
    }
    for (const item of upcomingGray) {
      items.push({
        key: `u-${item.vehicle.id}-${item.target_km}`,
        color: 'gray',
        vehicleCode: item.vehicle.code,
        vehicleName: item.vehicle.name,
        label: upcomingPeriodLabel(item),
        date: upcomingEventDate(item),
        maintenanceId: null,
        // Backend zaten "YYYY-MM-DD" ISO string döner — Date'e çevirip
        // geri string'e dönmek yerine DOĞRUDAN kullanılır (bkz. yukarıdaki
        // CalItem.windowLower yorumu — saat dilimi tuzağından kaçınmak için).
        windowLower: item.window_lower_date ?? null,
        windowUpper: item.window_upper_date ?? null,
        // Zaten "İleri Tarihe Al" ile zamanlanmış bir aday elle sürüklenerek
        // değiştirilemez (kullanıcı isteği: "planlanan zamana gitsin ve
        // değiştirilmesin") — yalnızca aşağıdaki yönetim listesinden
        // "İptal Et" ile kilidi açılabilir (bu öğenin kendi geri alma yolu).
        isManual: false,
        naturalDate: null,
        draggable: canDrag && !item.scheduled_start_at,
        locked: !!item.scheduled_start_at,
        source: item,
      })
    }
    return items
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list, upcomingGray, canDrag])

  const itemsByKey = useMemo(() => {
    const map = new Map<string, CalItem>()
    for (const it of calItems) map.set(it.key, it)
    return map
  }, [calItems])

  const eventsByDay = useMemo(() => {
    const days = gridDays(from, to).map(toISODate)
    const map = new Map<string, CalItem[]>()
    for (const d of days) map.set(d, [])
    for (const it of calItems) {
      if (!it.date) continue
      const day = toISODate(it.date)
      if (map.has(day)) map.get(day)!.push(it)
    }
    for (const arr of map.values()) {
      arr.sort((a, b) => (a.vehicleCode < b.vehicleCode ? -1 : a.vehicleCode > b.vehicleCode ? 1 : 0))
    }
    return map
  }, [calItems, from, to])

  // Görünen aralıktaki öğeler — üstteki özet rozetlerinde ve boş durum
  // kontrolünde tekrarsız sayı için kullanılır.
  const visibleItems = useMemo(() => {
    const fromISO = toISODate(from)
    const toISO = toISODate(to)
    return calItems.filter((it) => {
      if (!it.date) return false
      const day = toISODate(it.date)
      return day >= fromISO && day <= toISO
    })
  }, [calItems, from, to])

  // Sürükle-bırak ile bir öğeyi başka bir güne taşıma — hem başlatılmış iş
  // emirleri (planned_date) hem de henüz açılmamış adaylar (Başlat/İleri
  // Tarihe Al ile) için TEK giriş noktası. Kullanıcı isteği (güncellendi):
  // yalnızca DAHA SONRAYA (ileri tarihe) atmak tolerans penceresinin üst
  // sınırıyla (windowUpper) kısıtlansın — DAHA ERKENE (geriye) taşırken
  // istenen HERHANGİ bir güne bırakılabilsin, alt sınır (windowLower)
  // kontrolü artık uygulanmıyor.
  const [dragOverISO, setDragOverISO] = useState<string | null>(null)

  async function reschedule(id: number, iso: string) {
    setList((prev) => (prev ? prev.map((m) => (m.id === id ? { ...m, planned_date: iso } : m)) : prev))
    try {
      await api(`/maintenances/${id}/planned-date`, { method: 'PATCH', body: { planned_date: iso } })
    } catch (e) {
      setError((e as Error).message)
      loadList()
    }
  }

  // "Eski haline döndür": elle sürüklenip taşınmış (planned_date dolu) bir
  // öğeyi tekrar hesaplanan/doğal güne döndürür — backend'de zaten
  // planned_date=null gönderilince aracın hedef km/tarihine göre TAHMİNİ
  // güne dönüyor (bkz. main.py set_maintenance_planned_date). Bu, tek
  // seviyeli bir "geri al"dır; her taşıma ayrıca İşlem Kayıtları'nda
  // (Audit Log) eski/yeni tarih olarak zaten kayıtlıdır.
  async function revertManualDate(item: CalItem) {
    if (item.maintenanceId == null) return
    setList((prev) => (prev ? prev.map((m) => (m.id === item.maintenanceId ? { ...m, planned_date: null } : m)) : prev))
    try {
      await api(`/maintenances/${item.maintenanceId}/planned-date`, { method: 'PATCH', body: { planned_date: null } })
    } catch (e) {
      setError((e as Error).message)
    } finally {
      loadList()
    }
  }

  async function handleDropItem(key: string, iso: string) {
    const item = itemsByKey.get(key)
    if (!item || !item.draggable) return
    // ISO (YYYY-MM-DD) string karşılaştırması — bkz. CalItem.windowLower
    // yorumu. Date nesnelerine çevirip karşılaştırmak saat dilimi
    // tuzaklarına açıktı (ör. "YYYY-MM-DD" formatındaki tarih-yalnızca
    // string'ler JS'te UTC olarak ayrıştırılır), bu yüzden burada HİÇ Date
    // nesnesi üretilmeden doğrudan string karşılaştırması yapılır.
    // Kullanıcı isteği: geriye (daha erken bir güne) taşımada tolerans
    // kısıtlaması YOK — yalnızca ileriye (daha geç bir güne) taşımada üst
    // tolerans sınırı (windowUpper) uygulanır. Ayrıca hiçbir öğe, içinde
    // bulunduğumuz BUGÜNKÜ tarihten daha ÖNEYE (geçmişe) sürüklenemez —
    // geçmiş bir güne bakım planlamak anlamsız.
    const todayISO = toISODate(new Date())
    if (iso < todayISO) {
      setError(`${item.vehicleCode} — ${item.label}: bugünden (${fmtISO(todayISO)}) daha öne sürüklenemez.`)
      return
    }
    if (item.windowUpper && iso > item.windowUpper) {
      setError(
        `${item.vehicleCode} — ${item.label}: tolerans aralığının dışına, ${fmtISO(item.windowUpper)} tarihinden ` +
        `daha ileriye sürüklenemez.`,
      )
      return
    }
    setError('')
    if (item.color === 'gray') {
      await startUpcoming(item.source as UpcomingItem, iso)
    } else {
      await reschedule(item.maintenanceId!, iso)
    }
  }

  function openItem(item: CalItem) {
    if (item.color === 'gray') {
      const src = item.source as UpcomingItem
      if (src.scheduled_start_at) return // kilitli — yalnızca yönetim listesinden iptal edilebilir
      if (confirm(`${item.vehicleCode} — ${item.label} bakımını başlatmak istiyor musunuz?`)) {
        startUpcoming(src)
      }
    } else if (item.maintenanceId != null) {
      onOpenMaintenance(item.maintenanceId)
    }
  }

  function shift(delta: number) {
    if (view === 'day') setAnchor((a) => addDays(a, delta))
    else if (view === 'week') setAnchor((a) => addDays(a, delta * 7))
    else setAnchor((a) => new Date(a.getFullYear(), a.getMonth() + delta, 1))
  }

  // Kullanıcı isteği: tek tek her öğedeki ↺ butonuna basmak yerine, elle
  // taşınmış TÜM bakımları bir kez tıkla hesaplanan (doğal) tarihlerine geri
  // döndüren toplu bir buton — Günlük/Haftalık/Aylık sekmelerinin yanında.
  // `calItems` görünen aralıkla sınırlı DEĞİL (bkz. loadList — tüm başlatılmış
  // iş emirlerini çeker), bu yüzden sayaç/aksiyon sistemdeki TÜM elle
  // taşınmış öğeleri kapsar, yalnızca o an ekranda görüneni değil.
  const manualItems = useMemo(() => calItems.filter((it) => it.isManual && it.maintenanceId != null), [calItems])
  const [bulkRevertBusy, setBulkRevertBusy] = useState(false)

  async function revertAllManual() {
    if (manualItems.length === 0 || bulkRevertBusy) return
    if (!confirm(`${manualItems.length} elle taşınmış bakım, hesaplanan (doğal) tarihine geri döndürülecek. Onaylıyor musunuz?`)) {
      return
    }
    setBulkRevertBusy(true)
    setError('')
    try {
      await Promise.all(
        manualItems.map((it) =>
          api(`/maintenances/${it.maintenanceId}/planned-date`, { method: 'PATCH', body: { planned_date: null } }),
        ),
      )
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBulkRevertBusy(false)
      loadList()
    }
  }

  const totalEvents = visibleItems.length

  const statusCounts = useMemo(() => {
    const counts: Record<CalColor, number> = { gray: 0, pending: 0, in_progress: 0, completed: 0 }
    for (const it of visibleItems) counts[it.color]++
    return counts
  }, [visibleItems])

  return (
    <>
      <PageHeader title="Takvim" />

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
          <div className="row" style={{ gap: 8, alignItems: 'center' }}>
            <Tabs
              items={[
                { key: 'day', label: 'Günlük' },
                { key: 'week', label: 'Haftalık' },
                { key: 'month', label: 'Aylık' },
              ]}
              active={view}
              onChange={(k) => setView(k as ViewMode)}
            />
            {canDrag && manualItems.length > 0 && (
              <button
                className="small ghost"
                disabled={bulkRevertBusy}
                title="Elle sürüklenip taşınmış tüm bakımları hesaplanan (doğal) tarihlerine geri döndürür"
                onClick={revertAllManual}
              >
                <RotateCcw size={13} style={{ marginRight: 5, verticalAlign: -2 }} />
                Tümünü Eski Haline Döndür ({manualItems.length})
              </button>
            )}
          </div>
        </div>
        <div
          className="row calendar-meta"
          style={{ justifyContent: 'space-between', flexWrap: 'wrap', gap: 10, marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border)' }}
        >
          <CalendarLegend />
          <div className="row" style={{ gap: 10, fontSize: 12.5 }}>
            <span className="muted">Bu aralıkta {totalEvents} kayıt:</span>
            {statusCounts.gray > 0 && <Badge tone="neutral">{statusCounts.gray} Henüz Başlamadı</Badge>}
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
              description={`${from.toLocaleDateString('tr-TR')} tarihinde açık, tamamlanmış ya da yaklaşan bir kayıt bulunamadı.`}
            />
          ) : (
            <DayAgenda
              events={eventsByDay.get(toISODate(from)) ?? []}
              onOpenItem={openItem}
              onRevertManual={revertManualDate}
              canManage={canDrag}
            />
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
                  items={eventsByDay.get(toISODate(d)) ?? []}
                  onOpenItem={openItem}
                  onOpenDay={(day) => {
                    setAnchor(day)
                    setView('day')
                  }}
                  onDropItem={handleDropItem}
                  onRevertManual={revertManualDate}
                  canManage={canDrag}
                  dragOverISO={dragOverISO}
                  setDragOverISO={setDragOverISO}
                />
              ))}
            </div>
          </div>
          {totalEvents === 0 && (
            <EmptyState
              icon={<CalendarDays size={22} />}
              title="Bu aralıkta bakım yok"
              description="Seçili tarih aralığında bir kayıt bulunamadı."
            />
          )}
        </>
      )}

      {!!perms.start_maintenance && upcomingGray.length > 0 && (
        <div className="card fade-in" style={{ marginTop: 16 }}>
          <div className="card-head">
            <div>
              <div className="card-title">
                <PlayCircle size={15} style={{ marginRight: 6, verticalAlign: -3, color: 'var(--primary-600)' }} />
                Başlatılmayı Bekleyen Araçlar
              </div>
              <div className="card-subtitle">
                Yukarıdaki takvimde gri renkte gösterilen, henüz iş emri açılmamış araçlar. Doğrudan
                tolerans aralığı içinde sürükleyebilir ya da buradan yönetebilirsiniz.
              </div>
            </div>
            <Badge tone="neutral">{upcomingGray.length}</Badge>
          </div>
          {upcomingError && <div className="error">{upcomingError}</div>}
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Araç</th>
                  <th>Periyot</th>
                  <th>Hedef</th>
                  <th>Kalan</th>
                  <th>Planlanan Başlangıç</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {upcomingGray.map((item) => {
                  const busyKey = item.maintenance_id ?? -item.target_km
                  return (
                    <tr key={`${item.vehicle.id}-${item.target_km}`}>
                      <td><span className="vehicle-code">{item.vehicle.code}</span> {item.vehicle.name}</td>
                      <td>{upcomingPeriodLabel(item)}</td>
                      <td>{rawTargetLabel(item.target_km, item.target_date)}</td>
                      <td>{item.remaining_km < 0 ? `${fmtKm(Math.abs(item.remaining_km))} km geçti` : `${fmtKm(item.remaining_km)} km kaldı`}</td>
                      <td>
                        {item.scheduled_start_at ? (
                          <Badge tone="in_progress">
                            <CalendarClock size={11} style={{ marginRight: 4, verticalAlign: -2 }} />
                            {new Date(item.scheduled_start_at).toLocaleDateString('tr-TR')}
                          </Badge>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td>
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
                          {item.scheduled_start_at && (
                            <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                              <button className="small ghost" disabled={upcomingBusy === busyKey} onClick={() => cancelSchedule(item)}>
                                <XCircle size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                                İptal Et
                              </button>
                            </div>
                          )}
                          {!item.scheduled_start_at && (
                            <>
                              <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                                <button
                                  className="small primary"
                                  disabled={upcomingBusy === busyKey}
                                  onClick={() => startUpcoming(item)}
                                >
                                  <PlayCircle size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                                  {upcomingBusy === busyKey ? 'Başlatılıyor…' : 'Başlat'}
                                </button>
                                <button
                                  className="small ghost"
                                  title="İleri bir tarihe zamanla"
                                  disabled={upcomingBusy === busyKey}
                                  onClick={() => {
                                    if (scheduleOpenKey === busyKey) { setScheduleOpenKey(null); return }
                                    setScheduleOpenKey(busyKey)
                                    setScheduleDate('')
                                  }}
                                >
                                  <CalendarClock size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                                  İleri Tarihe Al
                                </button>
                              </div>
                              {scheduleOpenKey === busyKey && (
                                <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                                  <input
                                    type="date"
                                    value={scheduleDate}
                                    min={new Date(Date.now() + 86400000).toISOString().slice(0, 10)}
                                    onChange={(e) => setScheduleDate(e.target.value)}
                                    style={{ fontSize: 12.5, padding: '4px 6px' }}
                                  />
                                  <button
                                    className="small"
                                    disabled={!scheduleDate || upcomingBusy === busyKey}
                                    onClick={() => startUpcoming(item, scheduleDate)}
                                  >
                                    Zamanla
                                  </button>
                                  <button className="small ghost" onClick={() => setScheduleOpenKey(null)}>Vazgeç</button>
                                </div>
                              )}
                            </>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
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
  onOpenItem,
  onRevertManual,
  canManage,
}: {
  events: CalItem[]
  onOpenItem: (item: CalItem) => void
  onRevertManual: (item: CalItem) => void
  canManage: boolean
}) {
  const sorted = sortByCalPriority(events)
  return (
    <div className="table-card fade-in">
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Araç</th>
              <th>Periyot</th>
              <th>Durum</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((item) => (
              <tr key={item.key} className="clickable-row" onClick={() => onOpenItem(item)}>
                <td><span className="vehicle-code">{item.vehicleCode}</span></td>
                <td>{item.label}</td>
                <td><Badge tone={item.color === 'gray' ? 'neutral' : item.color}>{CAL_LABEL_TR[item.color]}</Badge></td>
                <td className="muted row" style={{ gap: 8, justifyContent: 'flex-end' }}>
                  {item.isManual && (
                    <span
                      className="muted"
                      style={{ fontSize: 11.5 }}
                      title={item.naturalDate ? `Normalde ${fmtDate(item.naturalDate)} tarihinde görünecekti` : undefined}
                    >
                      Elle taşındı
                    </span>
                  )}
                  {item.isManual && canManage && (
                    <button
                      className="small ghost"
                      title={`Eski (hesaplanan) tarihine döndür${item.naturalDate ? `: ${fmtDate(item.naturalDate)}` : ''}`}
                      onClick={(e) => {
                        e.stopPropagation()
                        onRevertManual(item)
                      }}
                    >
                      <RotateCcw size={12} />
                    </button>
                  )}
                  {item.maintenanceId ? `#${item.maintenanceId}` : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
