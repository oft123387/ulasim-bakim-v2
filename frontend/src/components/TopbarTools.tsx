import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { useLiveEvent } from '../liveEvents'
import type { NotificationItem, SearchHit, SearchResults } from '../types'
import {
  Search,
  Bell,
  X,
  Truck,
  Wrench,
  ClipboardList,
  PackageSearch,
  UserCircle2,
  FileText,
  CheckCircle2,
  Unlock,
  UserPlus,
  AlertTriangle,
  Inbox,
  Lightbulb,
  HelpCircle,
  PackageX,
} from 'lucide-react'

// Global aramada sonucu tıklanınca hangi sayfaya gidileceğini App belirler.
export type HitType = SearchHit['type']

const HIT_ICON: Record<HitType, React.ReactNode> = {
  vehicle: <Truck size={15} />,
  maintenance: <Wrench size={15} />,
  plan: <ClipboardList size={15} />,
  part: <PackageSearch size={15} />,
  user: <UserCircle2 size={15} />,
  pdf: <FileText size={15} />,
}

const GROUP_LABEL: Record<HitType, string> = {
  vehicle: 'Araçlar',
  maintenance: 'İş Emirleri',
  plan: 'Bakım Planları',
  part: 'Parçalar',
  user: 'Personel',
  pdf: 'PDF / Doküman',
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime()
  const diff = Date.now() - then
  const min = Math.floor(diff / 60000)
  if (min < 1) return 'az önce'
  if (min < 60) return `${min} dk önce`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr} saat önce`
  const day = Math.floor(hr / 24)
  if (day < 30) return `${day} gün önce`
  const mon = Math.floor(day / 30)
  if (mon < 12) return `${mon} ay önce`
  return `${Math.floor(mon / 12)} yıl önce`
}

// ------------------------------------------------------------- Global Arama
function GlobalSearch({
  allow,
  onPick,
}: {
  allow: Record<HitType, boolean>
  onPick: (hit: SearchHit) => void
}) {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [results, setResults] = useState<SearchResults | null>(null)
  const [loading, setLoading] = useState(false)
  const boxRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Ctrl+K (Cmd+K Mac'te) — çoğu kurumsal uygulamada olduğu gibi, nerede
  // olursanız olun tek tuşla global aramaya odaklanmayı sağlar.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        inputRef.current?.focus()
        inputRef.current?.select()
        setOpen(true)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  useEffect(() => {
    const query = q.trim()
    if (query.length < 2) {
      setResults(null)
      return
    }
    setLoading(true)
    const t = setTimeout(() => {
      api<SearchResults>(`/search?q=${encodeURIComponent(query)}`)
        .then((r) => setResults(r))
        .catch(() => setResults(null))
        .finally(() => setLoading(false))
    }, 280)
    return () => clearTimeout(t)
  }, [q])

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [])

  // App'in izin verdiği türlerdeki grupları, tanımlı sırada gösterir.
  const groups: HitType[] = ['vehicle', 'maintenance', 'plan', 'part', 'user', 'pdf']
  const groupHits = (t: HitType): SearchHit[] => {
    if (!results || !allow[t]) return []
    const map: Record<HitType, SearchHit[]> = {
      vehicle: results.vehicles,
      maintenance: results.maintenances,
      plan: results.plans,
      part: results.parts,
      user: results.users,
      pdf: results.documents,
    }
    return map[t]
  }
  const visibleTotal = groups.reduce((n, t) => n + groupHits(t).length, 0)

  return (
    <div className="topbar-search" ref={boxRef}>
      <div className="search-box topbar-search-box">
        <Search size={15} />
        <input
          ref={inputRef}
          value={q}
          onFocus={() => setOpen(true)}
          onChange={(e) => {
            setQ(e.target.value)
            setOpen(true)
          }}
          placeholder="Araç, iş emri, parça, personel, PDF ara… (Ctrl+K)"
        />
        {q && (
          <button className="topbar-search-clear" title="Temizle" onClick={() => { setQ(''); setResults(null) }}>
            <X size={14} />
          </button>
        )}
      </div>

      {open && q.trim().length >= 2 && (
        <div className="topbar-pop">
          {loading && <div className="topbar-pop-empty">Aranıyor…</div>}
          {!loading && visibleTotal === 0 && (
            <div className="topbar-pop-empty">
              <Inbox size={18} />
              <span>"{q.trim()}" için sonuç bulunamadı.</span>
            </div>
          )}
          {!loading &&
            groups.map((t) => {
              const hits = groupHits(t)
              if (hits.length === 0) return null
              return (
                <div key={t} className="topbar-pop-group">
                  <div className="topbar-pop-group-label">{GROUP_LABEL[t]}</div>
                  {hits.map((h) => (
                    <button
                      key={`${t}-${h.ref_id}-${h.title}`}
                      className="topbar-pop-row"
                      onClick={() => {
                        onPick(h)
                        setOpen(false)
                      }}
                    >
                      <span className="topbar-pop-icon">{HIT_ICON[t]}</span>
                      <span className="topbar-pop-text">
                        <span className="topbar-pop-title">{h.title}</span>
                        {h.subtitle && <span className="topbar-pop-sub">{h.subtitle}</span>}
                      </span>
                    </button>
                  ))}
                </div>
              )
            })}
        </div>
      )}
    </div>
  )
}

// -------------------------------------------------------- Bildirim Merkezi
const NOTIF_ICON: Record<NotificationItem['category'], React.ReactNode> = {
  work_order: <Wrench size={15} />,
  completed: <CheckCircle2 size={15} />,
  unlock: <Unlock size={15} />,
  user: <UserPlus size={15} />,
  overdue: <AlertTriangle size={15} />,
  suggestion: <Lightbulb size={15} />,
  low_stock: <PackageX size={15} />,
}

const NOTIF_TONE: Record<NotificationItem['category'], string> = {
  work_order: 'info',
  completed: 'ok',
  unlock: 'warn',
  user: 'info',
  overdue: 'crit',
  suggestion: 'info',
  low_stock: 'warn',
}

const LAST_SEEN_KEY = 'notif_last_seen'

function NotificationBell({ onOpenMaintenance }: { onOpenMaintenance: (id: number) => void }) {
  const [items, setItems] = useState<NotificationItem[]>([])
  const [open, setOpen] = useState(false)
  const [lastSeen, setLastSeen] = useState<number>(() => {
    const v = localStorage.getItem(LAST_SEEN_KEY)
    return v ? Number(v) : 0
  })
  const boxRef = useRef<HTMLDivElement>(null)

  const load = () => {
    api<NotificationItem[]>('/notifications?limit=40')
      .then(setItems)
      .catch(() => setItems([]))
  }
  useEffect(() => {
    load()
    const id = setInterval(load, 60000)
    return () => clearInterval(id)
  }, [])

  // Canlı Güncelleme (SSE) — yeni bir iş emri/tamamlama/kilit açma/kullanıcı/
  // öneri/düşük stok olayı olunca zil anında güncellensin.
  useLiveEvent(['maintenance', 'user', 'suggestion', 'part', 'permission'], load)

  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [])

  const unread = items.filter((n) => new Date(n.created_at).getTime() > lastSeen).length

  function toggle() {
    const next = !open
    setOpen(next)
    if (next) {
      const now = Date.now()
      localStorage.setItem(LAST_SEEN_KEY, String(now))
      setLastSeen(now)
    }
  }

  return (
    <div className="topbar-bell" ref={boxRef}>
      <button className="topbar-bell-btn" title="Bildirimler" onClick={toggle}>
        <Bell size={18} />
        {unread > 0 && <span className="topbar-bell-badge">{unread > 99 ? '99+' : unread}</span>}
      </button>

      {open && (
        <div className="topbar-pop topbar-pop-right">
          <div className="topbar-pop-head">
            <span>Bildirimler</span>
            {items.length > 0 && <span className="muted">{items.length}</span>}
          </div>
          {items.length === 0 && (
            <div className="topbar-pop-empty">
              <Inbox size={18} />
              <span>Henüz bildirim yok.</span>
            </div>
          )}
          {items.map((n) => {
            const clickable = n.maintenance_id != null
            return (
              <button
                key={n.id}
                className={`topbar-pop-row notif-${NOTIF_TONE[n.category]}`}
                disabled={!clickable}
                style={{ cursor: clickable ? 'pointer' : 'default' }}
                onClick={() => {
                  if (n.maintenance_id != null) {
                    onOpenMaintenance(n.maintenance_id)
                    setOpen(false)
                  }
                }}
              >
                <span className="topbar-pop-icon">{NOTIF_ICON[n.category]}</span>
                <span className="topbar-pop-text">
                  <span className="topbar-pop-title">{n.title}</span>
                  <span className="topbar-pop-sub">
                    {n.detail ? `${n.detail} · ` : ''}
                    {relativeTime(n.created_at)}
                  </span>
                </span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default function TopbarTools({
  allow,
  onPick,
  onOpenMaintenance,
  onStartTour,
}: {
  allow: Record<HitType, boolean>
  onPick: (hit: SearchHit) => void
  onOpenMaintenance: (id: number) => void
  // Site eğitim turunu elle (yeniden) başlatır — ilk girişte otomatik
  // açılan tur bir kez kapatıldıktan sonra buradan tekrar izlenebilir.
  onStartTour?: () => void
}) {
  return (
    <div className="topbar-tools">
      <GlobalSearch allow={allow} onPick={onPick} />
      <NotificationBell onOpenMaintenance={onOpenMaintenance} />
      {onStartTour && (
        <button className="tour-help-btn" title="Site turunu başlat" onClick={onStartTour}>
          <HelpCircle size={17} />
        </button>
      )}
    </div>
  )
}
