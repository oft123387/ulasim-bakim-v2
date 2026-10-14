import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { Dashboard, Maintenance, Shift, UpcomingItem, User } from '../types'
import { STATUS_TR } from '../types'
import { PageHeader, StatCard, CircularProgress, Badge, EmptyState, Skeleton } from '../components/ui'
import ParticleNetwork from '../components/ParticleNetwork'
import { useLiveEvent } from '../liveEvents'
import { rawTargetLabel, mergedPeriodLabel } from '../format'
import {
  Truck,
  Clock,
  Loader2,
  CheckCircle2,
  UserCheck,
  ArrowRight,
  AlertTriangle,
  ClipboardList,
  Users,
} from 'lucide-react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

const fmtKm = (n: number) => n.toLocaleString('tr-TR')
const fmt = (s: string | null) => (s ? new Date(s).toLocaleString('tr-TR') : '—')
const fmtClock = (d: Date) => d.toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' })
const fmtLongDate = (d: Date) =>
  d.toLocaleDateString('tr-TR', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
// Saate göre "Günaydın / İyi günler / İyi akşamlar" — daha kişisel, kurumsal
// bir dashboard hissi için (statik bir alt başlık yerine).
function greeting(): string {
  const h = new Date().getHours()
  if (h < 6) return 'İyi geceler'
  if (h < 12) return 'Günaydın'
  if (h < 18) return 'İyi günler'
  return 'İyi akşamlar'
}
// Hedef km'ye kalan/geçen mesafeyi anlaşılır göster: hedef zaten geçildiyse
// (remaining <= 0) "X km geçti" — negatif/garip büyük sayı yerine.
const kmDisplay = (remaining: number) =>
  remaining <= 0 ? `${fmtKm(Math.abs(remaining))} km geçti` : `${fmtKm(remaining)} km kaldı`
// Süre bazlı (aylık/yıllık) maddelerde remaining_km alanı gerçekte gün
// sayısıdır (negatif=gecikti) — target_date doluysa gün olarak göster.
const remainingDisplay = (u: UpcomingItem) =>
  u.target_date
    ? u.remaining_km <= 0
      ? `${Math.abs(u.remaining_km)} gün geçti`
      : `${u.remaining_km} gün kaldı`
    : kmDisplay(u.remaining_km)
// Kullanıcı isteği: hedef km HER ZAMAN tam km olarak yazılsın (Sirio kısa
// M/Y kodu KULLANILMASIN — ör. "1.500.000 km", asla "150M").
const hedefDisplay = (u: UpcomingItem) => rawTargetLabel(u.target_km, u.target_date)

type NavTarget = 'vehicles' | 'plans' | 'tree' | 'users' | 'history'

const STATUS_BY_BAR_KEY: Record<string, string> = {
  'Bekliyor': 'pending',
  'Devam Ediyor': 'in_progress',
  'Tamamlandı': 'completed',
}

// "Zamanı Gelen ve Yaklaşan Bakımlar" tablosunda ilk bakışta gösterilen en
// fazla satır (araç) sayısı — fazlası "Daha fazlasını gör" ile açılır.
const DUE_TABLE_LIMIT = 10

export default function DashboardPage({
  user,
  perms,
  onOpenMaintenance,
  onGoToMaintenances,
  onFilterMaintenances,
  onNavigate,
  onGoToHistoryThisMonth,
  onGoToHistoryForPlan,
}: {
  user: User
  // Yetki Yönetimi sayfasındaki "Dashboard Görünürlüğü" kategorisi altındaki
  // izinler — ör. view_dashboard_analytics=false ise Analiz bölümü gizlenir.
  // Verilmezse (perms=undefined) her şey görünür kalır (geriye dönük uyumlu).
  perms?: Record<string, boolean>
  onOpenMaintenance: (id: number) => void
  onGoToMaintenances: () => void
  onFilterMaintenances?: (filter: { status?: string; planName?: string }) => void
  onNavigate?: (page: NavTarget) => void
  // "Tamamlanan (Bu Ay)" kartına tıklayınca Bakım Geçmişi'ne kartla BİREBİR
  // aynı (bu ayın başından bugüne) tarih filtresiyle gider — verilmezse
  // (geriye dönük uyumluluk) filtresiz onNavigate('history')'e düşer.
  onGoToHistoryThisMonth?: () => void
  // Periyoda göre dağılım grafiğindeki "Tamamlandı" barına tıklayınca (ör.
  // "50.000 km") Bakım Geçmişi'ne o periyoda göre filtrelenmiş gider —
  // verilmezse (geriye dönük uyumluluk) filtresiz onNavigate('history')'e düşer.
  onGoToHistoryForPlan?: (planName: string) => void
}) {
  const [data, setData] = useState<Dashboard | null>(null)
  // "Zamanı Gelen ve Yaklaşan Bakımlar" tablosunda tüm satırlar mı görünsün
  // (varsayılan: yalnızca ilk DUE_TABLE_LIMIT satır).
  const [showAllDue, setShowAllDue] = useState(false)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const dueTableRef = useRef<HTMLDivElement>(null)

  const load = () => {
    api<Dashboard>('/dashboard')
      .then((d) => {
        setData(d)
        setLastUpdated(new Date())
      })
      .catch(() => setData(null))
  }

  // Canlı Güncelleme (SSE) — bir başkası bakım tamamlayınca/km güncelleyince
  // vb. 30 saniyelik polling'i beklemeden anında yenile.
  useLiveEvent(['maintenance', 'vehicle', 'part', 'tree_node'], load)

  useEffect(() => {
    load()
    // Bu ekran genelde açık bırakılıyor; başka bir kullanıcının yaptığı
    // değişiklikler (bakım tamamlama, km güncelleme vb.) otomatik yansısın
    // diye periyodik olarak ve sekmeye geri dönüldüğünde tazelenir (SSE
    // bağlantısı koparsa bu polling yedek olarak devam eder).
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

  // Kullanıcı isteği: "usta ekranında dashboardda [filo geneli KPI/analiz]
  // gözükmesin, onun yerine bakımlar, kendine atanan bakımlar ve vardiya
  // bilgileri gözüksün, usta için gereksiz şeyleri kaldırmış olalım." —
  // Usta girişinde filo geneli istatistik/analiz yerine kendisiyle ilgili
  // veriler gösterilir. /maintenances zaten usta oturumunda sunucu
  // tarafında "bana atanan VEYA henüz kimseye atanmamış" ile daraltılmış
  // döner (bkz. backend _filtered_maintenance_query) — bu yüzden ek bir
  // filtre parametresi gerekmez.
  const isTechnician = user.role === 'technician'
  const [myMaintenances, setMyMaintenances] = useState<Maintenance[] | null>(null)
  const [shifts, setShifts] = useState<Shift[]>([])
  const [shiftmates, setShiftmates] = useState<User[]>([])

  const loadTechnicianExtras = () => {
    if (user.role !== 'technician') return
    Promise.all([
      api<Maintenance[]>('/maintenances?status=pending'),
      api<Maintenance[]>('/maintenances?status=in_progress'),
    ])
      .then(([pending, inProgress]) => setMyMaintenances([...pending, ...inProgress]))
      .catch(() => setMyMaintenances([]))
  }

  useEffect(() => {
    if (!isTechnician) return
    loadTechnicianExtras()
    api<Shift[]>('/shifts').then(setShifts).catch(() => setShifts([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isTechnician])

  useEffect(() => {
    if (!isTechnician || user.shift_id == null) {
      setShiftmates([])
      return
    }
    api<User[]>(`/users/assignable?shift_id=${user.shift_id}`).then(setShiftmates).catch(() => setShiftmates([]))
  }, [isTechnician, user.shift_id])

  useLiveEvent(['maintenance'], loadTechnicianExtras)

  const isAdmin = user.role === 'admin'
  const isStaff = isAdmin || user.role === 'engineer' || user.role === 'amir'
  const myShift = shifts.find((s) => s.id === user.shift_id) ?? null
  // Kullanıcı isteği: "vardiyaya atanan bakım o vardiyada olan herkeste
  // gözükmeli" — kişiye atama (assigned_to) ile vardiyaya atama
  // (assigned_shift) birbirinden bağımsız iki ayrı alan (bkz. Bakımlar
  // sayfasındaki "Atanan" ve "Vardiya" satırları); "bana ait" sayılması
  // için ya doğrudan kendisine ya da kendi vardiyasına atanmış olması yeterli.
  const myAssigned = (myMaintenances ?? []).filter(
    (m) => m.assigned_to?.id === user.id || (user.shift_id != null && m.assigned_shift?.id === user.shift_id),
  )

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
    // Tamamlanan bakımlar artık Bakımlar sekmesinde değil, yalnızca Bakım
    // Geçmişi'nde gösteriliyor — o duruma tıklanınca doğrudan oraya gidilir,
    // barın kendi periyoduna (plan) göre filtrelenmiş halde ki "50.000 km'de
    // 7 tamamlanan" derken Geçmiş'te gerçekten yalnızca o 7 kayıt görünsün.
    if (status === 'completed') {
      if (onGoToHistoryForPlan && planName) onGoToHistoryForPlan(planName)
      else onNavigate?.('history')
      return
    }
    onFilterMaintenances?.({ status, planName })
  }

  const criticalCount =
    (data.due_now ?? []).filter((u) => u.critical).length +
    data.upcoming.filter((u) => u.critical).length

  function scrollToDueTable() {
    dueTableRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  // "Zamanı Gelen ve Yaklaşan Bakımlar" tablosu varsayılan olarak en fazla
  // DUE_TABLE_LIMIT satır gösterir; gerisi "Daha fazlasını gör" ile açılır.
  // Sıra korunur: önce açık iş emirleri (öncelik sırasında, bkz. backend
  // dashboard() → due_now), ardından yaklaşanlar.
  const dueRows = data.due_now ?? []
  const upcomingRows = data.upcoming ?? []
  const totalDueRows = dueRows.length + upcomingRows.length
  const visibleDueRows = showAllDue ? dueRows : dueRows.slice(0, DUE_TABLE_LIMIT)
  const visibleUpcomingRows = showAllDue
    ? upcomingRows
    : upcomingRows.slice(0, Math.max(0, DUE_TABLE_LIMIT - visibleDueRows.length))

  // perms verilmemişse (ör. eski bir çağıran) ya da anahtar henüz gelmediyse
  // varsayılan olarak GÖRÜNÜR kalır — mevcut davranışı bozmaz.
  const canViewAnalytics = perms ? perms.view_dashboard_analytics !== false : true

  return (
    <div className="dashboard-page">
      <PageHeader
        title={`${greeting()}, ${user.full_name.split(' ')[0]}`}
        subtitle={`${fmtLongDate(new Date())} · Canlı bakım özeti`}
        decoration={<ParticleNetwork particleDensity={0.00006} maxParticles={90} linkDistance={140} mouseLinkDistance={180} />}
        actions={
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
            <button className="primary" onClick={onGoToMaintenances}>
              Tüm Bakımları Listele <ArrowRight size={14} style={{ marginLeft: 6, verticalAlign: -2 }} />
            </button>
            {lastUpdated && (
              <span className="muted" style={{ fontSize: 11, display: 'flex', alignItems: 'center', gap: 5 }}>
                <span className="live-dot" /> Son güncelleme: {fmtClock(lastUpdated)}
              </span>
            )}
          </div>
        }
      />

      {isTechnician && (
        <>
          <div className="grid stagger dashboard-kpis stat-grid-4" style={{ marginBottom: 16 }}>
            <StatCard
              label="Bana Atanan (Aktif)"
              value={myAssigned.length}
              icon={<ClipboardList size={18} />}
              onClick={onGoToMaintenances}
            />
            <StatCard
              label="Bekleyen"
              value={(myMaintenances ?? []).filter((m) => m.status === 'pending').length}
              tone="pending"
              icon={<Clock size={18} />}
              onClick={() => onFilterMaintenances?.({ status: 'pending' })}
            />
            <StatCard
              label="Devam Eden"
              value={(myMaintenances ?? []).filter((m) => m.status === 'in_progress').length}
              tone="in_progress"
              icon={<Loader2 size={18} />}
              onClick={() => onFilterMaintenances?.({ status: 'in_progress' })}
            />
            <StatCard
              label="Tamamlanan (Bu Ay)"
              value={data.completed_this_month}
              tone="completed"
              icon={<CheckCircle2 size={18} />}
              onClick={() => (onGoToHistoryThisMonth ? onGoToHistoryThisMonth() : onNavigate?.('history'))}
            />
          </div>

          <div className="grid grid-2" style={{ alignItems: 'start', marginBottom: 16 }}>
            <div className="table-card slide-up">
              <div className="table-toolbar">
                <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                  <ClipboardList size={14} /> Bana Atanan Bakımlar
                </div>
              </div>
              {myMaintenances === null ? (
                <div style={{ padding: 16 }}><Skeleton height={120} /></div>
              ) : myAssigned.length === 0 ? (
                <EmptyState
                  title="Şu anda size atanmış bir bakım yok"
                  description="Bekleyen bakımlardan birini üstlenerek başlayabilirsiniz."
                />
              ) : (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr>
                        <th>Araç</th>
                        <th>Bakım</th>
                        <th>Hedef</th>
                        <th>Durum</th>
                      </tr>
                    </thead>
                    <tbody>
                      {myAssigned.map((m) => (
                        <tr key={m.id} className="clickable-row" onClick={() => onOpenMaintenance(m.id)}>
                          <td className="vehicle-code">{m.vehicle.code}</td>
                          <td>{mergedPeriodLabel(m.covered_tiers, m.plan.plan_type, m.vehicle.model, m.plan.name)}</td>
                          <td>{rawTargetLabel(m.target_km, m.target_date)}</td>
                          <td><Badge tone={m.status}>{STATUS_TR[m.status]}</Badge></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            <div className="card slide-up">
              <div className="card-head">
                <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                  <Users size={14} /> Vardiyam
                </div>
              </div>
              {myShift ? (
                <>
                  <div style={{ fontSize: 20, fontWeight: 800, marginBottom: 4 }}>{myShift.name}</div>
                  <div className="muted" style={{ fontSize: 12.5, marginBottom: 12 }}>
                    {shiftmates.length > 0
                      ? `Bu vardiyada sizinle birlikte ${shiftmates.length} kişi var`
                      : 'Bu vardiyada başka kimse yok'}
                  </div>
                  {shiftmates.length > 0 && (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                      {shiftmates.map((u) => (
                        <Badge key={u.id} tone="neutral">{u.full_name}</Badge>
                      ))}
                    </div>
                  )}
                </>
              ) : (
                <EmptyState
                  title="Bir vardiyaya atanmamışsınız"
                  description="Vardiya ataması amiriniz/yöneticiniz tarafından yapılır."
                />
              )}
            </div>
          </div>
        </>
      )}

      {!isTechnician && (
        <>
      <div className={`grid stagger dashboard-kpis ${isAdmin ? 'stat-grid-5' : 'stat-grid-4'}`} style={{ marginBottom: 16 }}>
        <StatCard
          label="Toplam Araç"
          value={data.vehicle_count}
          icon={<Truck size={18} />}
          onClick={isStaff ? () => onNavigate?.('vehicles') : undefined}
        />
        <StatCard
          label="Planlanan Bakım"
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
          onClick={() => (onGoToHistoryThisMonth ? onGoToHistoryThisMonth() : onNavigate?.('history'))}
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

      {canViewAnalytics && chartData.length > 0 && (
        <div className="card slide-up dashboard-analysis" style={{ marginBottom: 16 }}>
          <div className="card-head">
            <div className="card-title">Periyoda Göre Bakım Dağılımı ve Tamamlanma Oranı</div>
            <div className="dashboard-chart-legend" aria-label="Bakım durumları">
              <span className="pending">Bekliyor</span>
              <span className="in-progress">Devam ediyor</span>
              <span className="completed">Tamamlandı</span>
            </div>
          </div>
          <div className="row" style={{ alignItems: 'center', gap: 24, flexWrap: 'wrap' }}>
            <div style={{ flex: '3 1 320px', minWidth: 260 }}>
              <ResponsiveContainer width="100%" height={180}>
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
            <div style={{ flex: '1 1 140px', display: 'flex', justifyContent: 'center' }}>
              <CircularProgress value={totalAll ? (totalDone / totalAll) * 100 : 0} size={104} label="tamamlandı" />
            </div>
          </div>
        </div>
      )}
        </>
      )}

      <div className="grid grid-2 dashboard-activity" style={{ alignItems: 'start' }}>
        <div className="table-card slide-up" ref={dueTableRef}>
          <div className="table-toolbar">
            <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
              <AlertTriangle size={14} color="var(--amber)" /> Zamanı Gelen ve Yaklaşan Bakımlar
            </div>
          </div>
          {totalDueRows === 0 ? (
            <EmptyState title="Zamanı gelen veya yaklaşan bakım yok" description="Tüm araçlar hedef kilometrelerinden uzakta." />
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Araç</th>
                    <th>Bakım</th>
                    <th>Hedef</th>
                    <th>Durum / Kalan</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleDueRows.map((u, i) => (
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
                      <td>{hedefDisplay(u)}</td>
                      <td>
                        <span className={u.critical ? 'critical-pill' : undefined}>
                          {/* Başlatılmış (in_progress) iş emirleri de artık bu
                              listede kalır — bkz. dashboard() → due_rows.
                              Rozet, işin hangi aşamada olduğunu ayırt eder. */}
                          <Badge tone={u.status === 'planlandi' ? 'in_progress' : 'danger'}>
                            {u.status === 'planlandi'
                              ? `Planlandı · ${remainingDisplay(u)}`
                              : u.remaining_km <= 0
                                ? `Zamanı geldi · ${remainingDisplay(u)}`
                                : 'Bekliyor'}
                          </Badge>
                        </span>
                      </td>
                    </tr>
                  ))}
                  {visibleUpcomingRows.map((u, i) => (
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
                      <td>{hedefDisplay(u)}</td>
                      <td>
                        <Badge tone={u.critical ? 'danger' : u.urgent ? 'danger' : 'in_progress'}>
                          {remainingDisplay(u)}
                          {u.urgent && !u.target_date && (
                            <> · ~{u.estimated_days} gün</>
                          )}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {totalDueRows > DUE_TABLE_LIMIT && (
                <div style={{ padding: '10px 12px', textAlign: 'center' }}>
                  <button className="small ghost" onClick={() => setShowAllDue((s) => !s)}>
                    {showAllDue
                      ? 'Daha az göster'
                      : `Daha fazlasını gör (${totalDueRows - DUE_TABLE_LIMIT} araç daha)`}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="table-card slide-up">
          <div className="table-toolbar">
            <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
              <CheckCircle2 size={14} color="var(--green)" /> Son Tamamlanan Bakımlar
            </div>
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
                      {m.vehicle.code} · {mergedPeriodLabel(m.covered_tiers, m.plan.plan_type, m.vehicle.model, m.plan.name)}
                    </div>
                    <div className="muted" style={{ fontSize: 12 }}>
                      {rawTargetLabel(m.target_km, m.target_date)} · {fmt(m.completed_at)}
                    </div>
                  </span>
                  <Badge tone="completed">{STATUS_TR.completed}</Badge>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
