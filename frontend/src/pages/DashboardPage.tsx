import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Dashboard, User } from '../types'
import { STATUS_TR } from '../types'
import { PageHeader, StatCard, CircularProgress, Badge, EmptyState, Skeleton } from '../components/ui'
import {
  Truck,
  Clock,
  Loader2,
  CheckCircle2,
  UserCheck,
  ArrowRight,
  ClipboardList,
  Users as UsersIcon,
  Workflow,
  Sparkles,
  Gauge,
  AlertTriangle,
} from 'lucide-react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

const fmtKm = (n: number) => n.toLocaleString('tr-TR')
const fmt = (s: string | null) => (s ? new Date(s).toLocaleString('tr-TR') : '—')
// Hedef km'ye kalan/geçen mesafeyi anlaşılır göster: hedef zaten geçildiyse
// (remaining <= 0) "X km geçti" — negatif/garip büyük sayı yerine.
const kmDisplay = (remaining: number) =>
  remaining <= 0 ? `${fmtKm(Math.abs(remaining))} km geçti` : `${fmtKm(remaining)} km kaldı`

type NavTarget = 'vehicles' | 'plans' | 'tree' | 'users'

const STATUS_BY_BAR_KEY: Record<string, string> = {
  'Bekliyor': 'pending',
  'Devam Ediyor': 'in_progress',
  'Tamamlandı': 'completed',
}

export default function DashboardPage({
  user,
  onOpenMaintenance,
  onGoToMaintenances,
  onFilterMaintenances,
  onNavigate,
}: {
  user: User
  onOpenMaintenance: (id: number) => void
  onGoToMaintenances: () => void
  onFilterMaintenances?: (filter: { status?: string; planName?: string }) => void
  onNavigate?: (page: NavTarget) => void
}) {
  const [data, setData] = useState<Dashboard | null>(null)
  const dueTableRef = useRef<HTMLDivElement>(null)

  const load = () => {
    api<Dashboard>('/dashboard').then(setData).catch(() => setData(null))
  }

  useEffect(() => {
    load()
    // Bu ekran genelde açık bırakılıyor; başka bir kullanıcının yaptığı
    // değişiklikler (bakım tamamlama, km güncelleme vb.) otomatik yansısın
    // diye periyodik olarak ve sekmeye geri dönüldüğünde tazelenir.
    const interval = setInterval(load, 30000)
    const onVisible = () => {
      if (document.visibilityState === 'visible') load()
    }
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('focus', onVisible)
    return () => {
      clearInterval(interval)
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('focus', onVisible)
    }
  }, [])

  const isAdmin = user.role === 'admin'
  const isStaff = isAdmin || user.role === 'engineer' || user.role === 'amir'

  if (!data) {
    return (
      <>
        <PageHeader title="Dashboard" subtitle="Genel bakım durumu ve araç özetleri yükleniyor…" />
        <div className="grid stat-grid-4" style={{ marginBottom: 16 }}>
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="card">
              <Skeleton width="50%" height={11} style={{ marginBottom: 12 }} />
              <Skeleton width="35%" height={26} />
            </div>
          ))}
        </div>
        <div className="card"><Skeleton height={160} /></div>
      </>
    )
  }

  const totalDone = data.by_plan.reduce((s, p) => s + p.completed, 0)
  const totalAll = data.by_plan.reduce((s, p) => s + p.pending + p.in_progress + p.completed, 0)
  const chartData = data.by_plan.map((s) => ({
    name: s.plan.name.replace(' Bakımı', ''),
    fullName: s.plan.name,
    Bekliyor: s.pending,
    'Devam Ediyor': s.in_progress,
    Tamamlandı: s.completed,
  }))

  function handleBarClick(barKey: string, barData: { payload?: { fullName?: string } }) {
    const planName = barData?.payload?.fullName
    const status = STATUS_BY_BAR_KEY[barKey]
    onFilterMaintenances?.({ status, planName })
  }

  const criticalCount =
    (data.due_now ?? []).filter((u) => u.critical).length +
    data.upcoming.filter((u) => u.critical).length

  function scrollToDueTable() {
    dueTableRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const quickActions: { key: NavTarget; label: string; icon: React.ReactNode; show: boolean }[] = [
    { key: 'vehicles', label: 'Araçlar', icon: <Truck size={15} />, show: isStaff },
    { key: 'plans', label: 'Bakım Planları', icon: <ClipboardList size={15} />, show: isAdmin },
    { key: 'tree', label: 'Bakım Ağacı', icon: <Workflow size={15} />, show: isAdmin },
    { key: 'users', label: 'Kullanıcılar', icon: <UsersIcon size={15} />, show: isAdmin },
  ]

  return (
    <>
      <PageHeader
        title="Dashboard"
        subtitle="Filo genelinde bakım durumunu, yaklaşan işleri ve son aktiviteleri tek ekrandan izleyin."
        actions={
          <button className="primary" onClick={onGoToMaintenances}>
            Tüm Bakımları Listele <ArrowRight size={14} style={{ marginLeft: 6, verticalAlign: -2 }} />
          </button>
        }
      />

      <div className={`grid stagger ${isAdmin ? 'stat-grid-5' : 'stat-grid-4'}`} style={{ marginBottom: 20 }}>
        <StatCard
          label="Toplam Araç"
          value={data.vehicle_count}
          icon={<Truck size={18} />}
          onClick={isStaff ? () => onNavigate?.('vehicles') : undefined}
        />
        <StatCard
          label="Bekleyen Bakım"
          value={data.pending_count}
          tone="pending"
          icon={<Clock size={18} />}
          onClick={() => onFilterMaintenances?.({ status: 'pending' })}
        />
        <StatCard
          label="Devam Eden"
          value={data.in_progress_count}
          tone="in_progress"
          icon={<Loader2 size={18} />}
          onClick={() => onFilterMaintenances?.({ status: 'in_progress' })}
        />
        <StatCard
          label="Tamamlanan (Bu Ay)"
          value={data.completed_this_month}
          tone="completed"
          icon={<CheckCircle2 size={18} />}
          onClick={() => onFilterMaintenances?.({ status: 'completed' })}
        />
        {criticalCount > 0 && (
          <StatCard
            label="Çok Kritik"
            value={criticalCount}
            tone="danger"
            icon={<AlertTriangle size={18} />}
            onClick={scrollToDueTable}
          />
        )}
        {isAdmin && (
          <StatCard
            label="Onay Bekleyen Kullanıcı"
            value={data.pending_user_approvals}
            tone={data.pending_user_approvals > 0 ? 'danger' : undefined}
            icon={<UserCheck size={18} />}
            onClick={() => onNavigate?.('users')}
          />
        )}
      </div>

      <div className="grid dash-split" style={{ alignItems: 'stretch', marginBottom: 4 }}>
        {chartData.length > 0 && (
          <div className="card hoverable slide-up">
            <div className="card-head">
              <div>
                <div className="card-title">Periyoda Göre Bakım Dağılımı</div>
                <div className="card-subtitle">
                  Her periyottaki bekleyen / devam eden / tamamlanan bakım sayısı — bir sütuna tıklayınca
                  Bakımlar sayfasında o duruma/periyoda göre filtrelenmiş liste açılır
                </div>
              </div>
            </div>
            <ResponsiveContainer width="100%" height={230}>
              <BarChart data={chartData} barGap={4}>
                <CartesianGrid vertical={false} stroke="var(--border)" />
                <XAxis dataKey="name" tick={{ fontSize: 11, fill: 'var(--text-tertiary)' }} axisLine={{ stroke: 'var(--border)' }} tickLine={false} />
                <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: 'var(--text-tertiary)' }} axisLine={false} tickLine={false} />
                <Tooltip
                  cursor={{ fill: 'var(--surface-alt)' }}
                  contentStyle={{ borderRadius: 10, border: '1px solid var(--border)', fontSize: 12.5, boxShadow: 'var(--shadow-md)' }}
                />
                <Bar
                  dataKey="Bekliyor"
                  stackId="a"
                  fill="var(--amber)"
                  radius={[0, 0, 0, 0]}
                  cursor={onFilterMaintenances ? 'pointer' : undefined}
                  onClick={(barData) => handleBarClick('Bekliyor', barData)}
                />
                <Bar
                  dataKey="Devam Ediyor"
                  stackId="a"
                  fill="var(--accent)"
                  cursor={onFilterMaintenances ? 'pointer' : undefined}
                  onClick={(barData) => handleBarClick('Devam Ediyor', barData)}
                />
                <Bar
                  dataKey="Tamamlandı"
                  stackId="a"
                  fill="var(--green)"
                  radius={[6, 6, 0, 0]}
                  cursor={onFilterMaintenances ? 'pointer' : undefined}
                  onClick={(barData) => handleBarClick('Tamamlandı', barData)}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}

        <div className="card hoverable slide-up" style={{ display: 'flex', flexDirection: 'column' }}>
          <div className="card-head">
            <div>
              <div className="card-title">Genel Tamamlanma Oranı</div>
              <div className="card-subtitle">Tüm periyotlar birleşik</div>
            </div>
            <Gauge size={16} color="var(--text-tertiary)" />
          </div>
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 18 }}>
            <CircularProgress value={totalAll ? (totalDone / totalAll) * 100 : 0} size={104} label="tamamlandı" />
          </div>
        </div>
      </div>

      {quickActions.some((q) => q.show) && (
        <div className="card slide-up" style={{ marginTop: 16 }}>
          <div className="card-head">
            <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
              <Sparkles size={15} color="var(--primary-600)" /> Hızlı Erişim
            </div>
          </div>
          <div className="row">
            {quickActions
              .filter((q) => q.show)
              .map((q) => (
                <button key={q.key} onClick={() => onNavigate?.(q.key)}>
                  {q.icon}
                  <span style={{ marginLeft: 8 }}>{q.label}</span>
                </button>
              ))}
          </div>
        </div>
      )}

      <div className="grid grid-2" style={{ marginTop: 16, alignItems: 'start' }}>
        <div className="table-card slide-up" ref={dueTableRef}>
          <div className="table-toolbar">
            <div className="card-title">Zamanı Gelen ve Yaklaşan Bakımlar</div>
          </div>
          {(data.due_now?.length ?? 0) === 0 && data.upcoming.length === 0 ? (
            <EmptyState title="Zamanı gelen veya yaklaşan bakım yok" description="Tüm araçlar hedef kilometrelerinden uzakta." />
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Araç</th>
                    <th>Bakım</th>
                    <th>Hedef KM</th>
                    <th>Durum / Kalan</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.due_now ?? []).map((u, i) => (
                    <tr
                      key={`due-${i}`}
                      className={`${u.critical ? 'critical-row' : 'alert-row'} ${u.maintenance_id ? 'clickable-row' : ''}`}
                      onClick={u.maintenance_id ? () => onOpenMaintenance(u.maintenance_id!) : undefined}
                    >
                      <td className="vehicle-code">{u.vehicle.code}</td>
                      <td>
                        {u.plan_name}
                        {u.covered_tiers && u.covered_tiers.length > 1 && (
                          <span className="muted" style={{ fontSize: 11.5, marginLeft: 6 }}>
                            ({u.covered_tiers.length} periyot birleşik)
                          </span>
                        )}
                      </td>
                      <td>{fmtKm(u.target_km)}</td>
                      <td>
                        <span className={u.critical ? 'critical-pill' : undefined}>
                          <Badge tone="danger">
                            {u.remaining_km <= 0 ? `Zamanı geldi · ${kmDisplay(u.remaining_km)}` : 'Bekliyor'}
                          </Badge>
                        </span>
                      </td>
                    </tr>
                  ))}
                  {data.upcoming.map((u, i) => (
                    <tr
                      key={`up-${i}`}
                      className={`${u.critical ? 'critical-row' : u.urgent ? 'alert-row' : ''} clickable-row`}
                      onClick={() => onFilterMaintenances?.({ planName: u.plan_name })}
                      title="Bu periyottaki bakımları Bakımlar sayfasında görüntüle"
                    >
                      <td className="vehicle-code">{u.vehicle.code}</td>
                      <td>
                        {u.plan_name}
                        {u.covered_tiers && u.covered_tiers.length > 1 && (
                          <span className="muted" style={{ fontSize: 11.5, marginLeft: 6 }}>
                            ({u.covered_tiers.length} periyot birleşik)
                          </span>
                        )}
                      </td>
                      <td>{fmtKm(u.target_km)}</td>
                      <td>
                        <Badge tone={u.critical ? 'danger' : u.urgent ? 'danger' : 'in_progress'}>
                          {kmDisplay(u.remaining_km)}
                          {u.urgent && (
                            <> · ~{u.estimated_days} gün</>
                          )}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="table-card slide-up">
          <div className="table-toolbar">
            <div className="card-title">Son Tamamlanan Bakımlar</div>
          </div>
          {data.recent_completed.length === 0 ? (
            <EmptyState title="Henüz tamamlanan bakım yok" />
          ) : (
            <div style={{ padding: '4px 6px' }}>
              {data.recent_completed.map((m) => (
                <div
                  key={m.id}
                  className="row"
                  style={{
                    padding: '11px 12px',
                    borderRadius: 10,
                    cursor: 'pointer',
                    transition: 'background var(--dur-fast) var(--ease)',
                  }}
                  onClick={() => onOpenMaintenance(m.id)}
                  onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--surface-alt)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
                >
                  <span
                    style={{
                      width: 32,
                      height: 32,
                      borderRadius: 9,
                      background: 'var(--green-light)',
                      color: 'var(--green)',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      flexShrink: 0,
                    }}
                  >
                    <CheckCircle2 size={16} />
                  </span>
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: 13.5 }}>
                      {m.vehicle.code} · {fmtKm(m.target_km)} km
                    </div>
                    <div className="muted" style={{ fontSize: 12 }}>{fmt(m.completed_at)}</div>
                  </span>
                  <Badge tone="completed">{STATUS_TR.completed}</Badge>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  )
}
