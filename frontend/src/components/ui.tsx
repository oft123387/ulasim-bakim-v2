import type { ReactNode } from 'react'
import { ChevronRight, Search, Inbox, TrendingDown, TrendingUp } from 'lucide-react'

/* =========================================================================
   Shared enterprise UI kit.
   A small, focused set of primitives reused across every page so the whole
   application speaks the same visual language (see src/index.css tokens).
   ========================================================================= */

// ---------------------------------------------------------------- PageHeader
export function PageHeader({
  title,
  subtitle,
  actions,
  breadcrumb,
  decoration,
}: {
  title: string
  subtitle?: string
  actions?: ReactNode
  breadcrumb?: { label: string; onClick?: () => void }[]
  /** İsteğe bağlı dekoratif katman (ör. ParticleNetwork) — ".fade-in"
   * sarmalayıcısının İÇİNE, başlık/altyazıdan ÖNCE (yani görsel olarak
   * arkasına) render edilir. Yalnızca ilgili sayfa özel bir arka plan
   * stiliyle (ör. Dashboard'daki ".dashboard-page > .fade-in" hero
   * kartı — position:relative + overflow:hidden) kullanılmalıdır; aksi
   * halde sınırları belirsiz kalır. */
  decoration?: ReactNode
}) {
  return (
    <div className="fade-in">
      {decoration}
      {breadcrumb && breadcrumb.length > 0 && <Breadcrumb items={breadcrumb} />}
      <div className="page-header" style={{ marginTop: breadcrumb ? 6 : 0 }}>
        <div>
          <h1>{title}</h1>
          {subtitle && <div className="page-subtitle">{subtitle}</div>}
        </div>
        {actions && <div className="page-actions">{actions}</div>}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- Breadcrumb
export function Breadcrumb({ items }: { items: { label: string; onClick?: () => void }[] }) {
  return (
    <nav className="breadcrumb">
      {items.map((it, i) => (
        <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          {i > 0 && <ChevronRight size={13} />}
          {it.onClick ? (
            <a
              href="#"
              className="crumb"
              style={{ textDecoration: 'none' }}
              onClick={(e) => {
                e.preventDefault()
                it.onClick?.()
              }}
            >
              {it.label}
            </a>
          ) : (
            <span className={`crumb ${i === items.length - 1 ? 'current' : ''}`}>{it.label}</span>
          )}
        </span>
      ))}
    </nav>
  )
}

// ---------------------------------------------------------------- Badge
const STATUS_LABEL_TR: Record<string, string> = {
  pending: 'Bekliyor',
  in_progress: 'Devam Ediyor',
  completed: 'Tamamlandı',
}

export function Badge({
  tone = 'neutral',
  children,
}: {
  tone?: 'pending' | 'in_progress' | 'completed' | 'neutral' | 'danger'
  children?: ReactNode
}) {
  return <span className={`badge ${tone}`}>{children ?? STATUS_LABEL_TR[tone] ?? tone}</span>
}

// ---------------------------------------------------------------- ProgressBar
export function ProgressBar({ value, max = 100, tone }: { value: number; max?: number; tone?: 'green' }) {
  const pct = max > 0 ? Math.min(100, Math.max(0, (value / max) * 100)) : 0
  return (
    <div className={`progressbar ${tone ? `tone-${tone}` : ''}`}>
      <div style={{ width: `${pct}%` }} />
    </div>
  )
}

// ---------------------------------------------------------------- CircularProgress
export function CircularProgress({
  value,
  size = 84,
  strokeWidth = 9,
  label,
  color = 'var(--primary-600)',
}: {
  value: number
  size?: number
  strokeWidth?: number
  label?: string
  color?: string
}) {
  const r = (size - strokeWidth) / 2
  const c = 2 * Math.PI * r
  const pct = Math.min(100, Math.max(0, value))
  const offset = c - (pct / 100) * c
  return (
    <div style={{ position: 'relative', width: size, height: size, flexShrink: 0 }}>
      <svg className="progress-ring" width={size} height={size}>
        <circle cx={size / 2} cy={size / 2} r={r} stroke="var(--surface-sunken)" strokeWidth={strokeWidth} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          stroke={color}
          strokeWidth={strokeWidth}
          strokeDasharray={c}
          strokeDashoffset={offset}
          style={{ transition: 'stroke-dashoffset var(--dur-slow) var(--ease)' }}
        />
      </svg>
      <div
        style={{
          position: 'absolute',
          inset: 0,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <span style={{ fontSize: size * 0.24, fontWeight: 800, color: 'var(--text)', letterSpacing: '-0.02em' }}>
          {Math.round(pct)}%
        </span>
        {label && <span style={{ fontSize: 10.5, color: 'var(--text-tertiary)', fontWeight: 600 }}>{label}</span>}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- StatCard
export function StatCard({
  label,
  value,
  tone,
  icon,
  trend,
  onClick,
}: {
  label: string
  value: number | string
  tone?: 'pending' | 'in_progress' | 'completed' | 'danger'
  icon?: ReactNode
  trend?: { value: string; direction: 'up' | 'down' }
  onClick?: () => void
}) {
  return (
    <div
      className={`stat-card ${tone ? `tone-${tone}` : ''} ${onClick ? 'clickable-row' : ''}`}
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
    >
      <div className="stat-top">
        <div className="stat-label">{label}</div>
        {icon && <div className="stat-icon">{icon}</div>}
      </div>
      <div className="stat-value">{value}</div>
      {trend && (
        <div className={`stat-trend ${trend.direction}`}>
          {trend.direction === 'up' ? <TrendingUp size={13} /> : <TrendingDown size={13} />}
          {trend.value}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------- Avatar
const AVATAR_COLORS = ['#1b4da0', '#2e6bff', '#12805c', '#b45309', '#7c3aed', '#0f766e', '#be185d']

function hashString(s: string) {
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0
  return h
}

export function Avatar({ name, size = 30 }: { name: string; size?: number }) {
  const initials =
    name
      .split(' ')
      .filter(Boolean)
      .slice(0, 2)
      .map((s) => s[0]?.toUpperCase())
      .join('') || '?'
  const color = AVATAR_COLORS[hashString(name) % AVATAR_COLORS.length]
  return (
    <span
      className="avatar"
      title={name}
      style={{ width: size, height: size, background: color, fontSize: size * 0.4 }}
    >
      {initials}
    </span>
  )
}

// ---------------------------------------------------------------- SearchInput
export function SearchInput({
  value,
  onChange,
  placeholder = 'Ara…',
  style,
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  style?: React.CSSProperties
}) {
  return (
    <div className="search-box" style={style}>
      <Search size={15} />
      <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} />
    </div>
  )
}

// ---------------------------------------------------------------- Tabs
export function Tabs({
  items,
  active,
  onChange,
}: {
  items: { key: string; label: string }[]
  active: string
  onChange: (key: string) => void
}) {
  return (
    <div className="tabs">
      {items.map((it) => (
        <button key={it.key} className={active === it.key ? 'active' : ''} onClick={() => onChange(it.key)}>
          {it.label}
        </button>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------- EmptyState
export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode
  title: string
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="empty-state fade-in">
      <div className="empty-icon">{icon ?? <Inbox size={22} />}</div>
      <div className="empty-title">{title}</div>
      {description && <div className="empty-desc">{description}</div>}
      {action}
    </div>
  )
}

// ---------------------------------------------------------------- Skeleton
export function Skeleton({
  width = '100%',
  height = 12,
  radius,
  style,
}: {
  width?: number | string
  height?: number | string
  radius?: number
  style?: React.CSSProperties
}) {
  return (
    <div
      className="skeleton"
      style={{ width, height, borderRadius: radius ?? 6, ...style }}
    />
  )
}

export function SkeletonTableRows({ columns, rows = 5 }: { columns: number; rows?: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, r) => (
        <tr key={r} className="skeleton-row">
          {Array.from({ length: columns }).map((_, c) => (
            <td key={c}>
              <Skeleton width={c === 0 ? '60%' : '80%'} />
            </td>
          ))}
        </tr>
      ))}
    </>
  )
}
