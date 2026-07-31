import { useEffect, useRef, useState, type ReactElement } from 'react'
import { api, refreshAccessToken, revokeRefreshToken, setRefreshToken, setToken } from './api'
import { startLiveEvents, stopLiveEvents, restartLiveEvents } from './liveEvents'
import type { MyPermissions, SearchHit, User } from './types'
import { ROLE_TR } from './types'
import Login from './pages/Login'
import ParticleNetwork from './components/ParticleNetwork'
import DashboardPage from './pages/DashboardPage'
import MaintenancesPage from './pages/MaintenancesPage'
import HistoryPage from './pages/HistoryPage'
import AuditLogPage from './pages/AuditLogPage'
import VehiclesPage from './pages/VehiclesPage'
import PlansPage from './pages/PlansPage'
import TreeEditorPage from './pages/TreeEditorPage'
import UsersPage from './pages/UsersPage'
import PermissionsPage from './pages/PermissionsPage'
import CalendarPage from './pages/CalendarPage'
import ProfilePage from './pages/ProfilePage'
import PartsCatalogPage from './pages/PartsCatalogPage'
import SuggestionsPage from './pages/SuggestionsPage'
import KPIPage from './pages/KPIPage'
import SystemStatusPage from './pages/SystemStatusPage'
import HelpPage from './pages/HelpPage'
import { Avatar } from './components/ui'
import TopbarTools, { type HitType } from './components/TopbarTools'
import { ToastHost } from './components/toast'
import { OnboardingTour, ONBOARDING_STEPS } from './components/onboarding'
import {
  LayoutDashboard,
  Workflow,
  Wrench,
  Truck,
  ClipboardList,
  Users as UsersIcon,
  History as HistoryIcon,
  ScrollText,
  LogOut,
  Box,
  ShieldCheck,
  CalendarDays,
  UserCircle2,
  Menu,
  PackageSearch,
  Lightbulb,
  ChevronLeft,
  ChevronRight,
  Activity,
  HelpCircle,
  BarChart3,
} from 'lucide-react'

export type Page =
  | 'dashboard'
  | 'maintenances'
  | 'calendar'
  | 'history'
  | 'vehicles'
  | 'plans'
  | 'tree'
  | 'parts'
  | 'suggestions'
  | 'kpi'
  | 'users'
  | 'permissions'
  | 'audit'
  | 'system'
  | 'help'
  | 'profile'

type Theme = 'light' | 'dark'

const NAV_ICONS: Record<Page, ReactElement> = {
  dashboard: <LayoutDashboard size={17} />,
  tree: <Workflow size={17} />,
  maintenances: <Wrench size={17} />,
  calendar: <CalendarDays size={17} />,
  history: <HistoryIcon size={17} />,
  vehicles: <Truck size={17} />,
  plans: <ClipboardList size={17} />,
  parts: <PackageSearch size={17} />,
  suggestions: <Lightbulb size={17} />,
  kpi: <BarChart3 size={17} />,
  users: <UsersIcon size={17} />,
  permissions: <ShieldCheck size={17} />,
  audit: <ScrollText size={17} />,
  system: <Activity size={17} />,
  help: <HelpCircle size={17} />,
  profile: <UserCircle2 size={17} />,
}

const PAGE_TITLES: Record<Page, string> = {
  dashboard: 'Dashboard',
  tree: 'Bakım Ağacı',
  maintenances: 'Bakımlar',
  calendar: 'Takvim',
  history: 'Bakım Geçmişi',
  vehicles: 'Araçlar',
  plans: 'Bakım Planları',
  parts: 'Parça Bilgi Bankası',
  suggestions: 'Öneriler',
  kpi: 'KPI Raporu',
  users: 'Kullanıcılar',
  permissions: 'Yetki Yönetimi',
  audit: 'İşlem Kayıtları',
  system: 'Sistem Durumu',
  help: 'Yardım',
  profile: 'Profil',
}

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [perms, setPerms] = useState<MyPermissions | null>(null)
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState<Page>('dashboard')
  const [jumpToMaintenance, setJumpToMaintenance] = useState<number | null>(null)
  const [maintFilter, setMaintFilter] = useState<{ status?: string; planName?: string } | null>(null)
  const [openVehicleId, setOpenVehicleId] = useState<number | null>(null)
  const [openPartId, setOpenPartId] = useState<number | null>(null)
  const [navOpen, setNavOpen] = useState(false)
  // Masaüstünde kenar çubuğu daraltılıp (yalnızca ikonlar) genişletilebilir
  // ("içe girip çıkabilir") — tercih tarayıcıda hatırlanır. Mobilde zaten
  // navOpen ile tam ekran açılıp kapanıyor, bu ayrı bir özellik (bkz. CSS'te
  // masaüstüne özel medya sorgusu).
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => localStorage.getItem('sidebar_collapsed') === '1',
  )
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem('theme') as Theme | null) ?? 'light',
  )
  // Eğitim turu (onboarding) — bkz. components/onboarding.tsx. Kullanıcı
  // adına özel localStorage bayrağıyla yalnızca ilk girişte bir kez
  // otomatik açılır; topbar'daki "?" butonundan istendiğinde tekrar
  // başlatılabilir (bu durumda bayrak tekrar yazılsa da zararsızdır).
  const [tourActive, setTourActive] = useState(false)
  // Hangi kullanıcı adı için otomatik başlatmayı zaten denediğimizi tutar —
  // bir booleandan farklı olarak, aynı sekmede çıkış yapıp BAŞKA bir hesapla
  // girildiğinde (sayfa yenilenmeden) her hesap için ayrı ayrı kontrol
  // edilmesini sağlar.
  const tourAutoTriedForRef = useRef<string | null>(null)

  useEffect(() => {
    localStorage.setItem('sidebar_collapsed', sidebarCollapsed ? '1' : '0')
  }, [sidebarCollapsed])

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('theme', theme)
  }, [theme])

  useEffect(() => {
    async function boot() {
      try {
        const u = await api<User>('/auth/me')
        setUser(u)
        api<MyPermissions>('/permissions/me').then(setPerms).catch(() => setPerms(null))
        applyDeepLinkFromUrl()
        return
      } catch {
        // Erişim jetonu süresi dolmuş olabilir (sekme uzun süre açık
        // kalmış olabilir) — tam çıkışa zorlamadan önce, hâlâ geçerli
        // olabilecek uzun ömürlü yenileme jetonuyla bir kez sessizce
        // yenilemeyi dener.
      }
      const ok = await refreshAccessToken()
      if (ok) {
        try {
          const u = await api<User>('/auth/me')
          setUser(u)
          api<MyPermissions>('/permissions/me').then(setPerms).catch(() => setPerms(null))
          applyDeepLinkFromUrl()
          return
        } catch {
          // düş
        }
      }
      setToken(null)
      setRefreshToken(null)
    }
    boot().finally(() => setLoading(false))
  }, [])

  function tourStorageKey(username: string) {
    return `ulasim_bakim_onboarding_done_${username}`
  }

  // Kullanıcı bilgisi geldikten sonra, bu hesap için tur daha önce
  // görülmemişse (localStorage'da bayrak yoksa) kısa bir gecikmeyle
  // otomatik başlatılır — sayfa ilk açıldığında içerik otursun diye.
  useEffect(() => {
    if (!user || tourAutoTriedForRef.current === user.username) return
    tourAutoTriedForRef.current = user.username
    if (localStorage.getItem(tourStorageKey(user.username))) return
    const t = setTimeout(() => setTourActive(true), 900)
    return () => clearTimeout(t)
  }, [user])

  function closeTour() {
    setTourActive(false)
    if (user) localStorage.setItem(tourStorageKey(user.username), '1')
  }

  // Erişim jetonu artık kısa ömürlü (2 saat — bkz. backend/app/auth.py);
  // oturumun kesintisiz kalması için sekme açıkken periyodik olarak
  // sessizce yenilenir (Faz 23 — JWT + Refresh Token). Başarısız olursa
  // (yenileme jetonu da geçersizse) sessizce bırakılır — kullanıcı bir
  // sonraki normal istekte zaten 401 alır ve mevcut davranışla (sayfa
  // düzeyinde hata mesajı) karşılaşır; bu, tüm api() çağrılarını etkileyen
  // riskli bir 401-yakala-yeniden-dene mekanizmasına göre çok daha güvenli.
  useEffect(() => {
    if (!user) return
    const id = setInterval(() => {
      refreshAccessToken().then((ok) => { if (ok) restartLiveEvents() })
    }, 20 * 60 * 1000)
    return () => clearInterval(id)
  }, [user])

  // Canlı Güncelleme (SSE) — oturum açıkken bağlan, çıkış yapılınca kapat.
  // Bir başkasının yaptığı değişiklik (bakım tamamlama, alarm onaylama, km
  // güncelleme vb.) sayfa yenilenmeden anında yansısın diye (bkz.
  // liveEvents.ts / useLiveEvent) — mevcut sayfa-bazlı polling (setInterval)
  // yedek olarak kalmaya devam eder, SSE yalnızca onu hızlandırır.
  useEffect(() => {
    if (!user) {
      stopLiveEvents()
      return
    }
    startLiveEvents()
    return () => stopLiveEvents()
  }, [user])

  // QR kod ile açılan derin bağlantıyı uygular: ?vehicle_id=… aracın
  // profil/bakım geçmişi sayfasını, ?part_id=… ilgili parçanın Parça
  // Bilgi Bankası sayfasını açar. Kullanıldıktan sonra URL'den temizlenir
  // (geri tuşuyla veya sayfa yenilemede tekrar tetiklenmesin diye).
  function applyDeepLinkFromUrl() {
    const params = new URLSearchParams(window.location.search)
    const vehicleId = params.get('vehicle_id')
    const partId = params.get('part_id')
    if (vehicleId && !Number.isNaN(Number(vehicleId))) {
      openVehicleProfile(Number(vehicleId))
    } else if (partId && !Number.isNaN(Number(partId))) {
      openPartCatalogItem(Number(partId))
    } else {
      return
    }
    window.history.replaceState({ page: 'dashboard' }, '', window.location.pathname)
  }

  // Tarayıcının geri/ileri tuşlarını uygulama içi sayfa geçmişiyle
  // eşleştirir: navigate() her sayfa değişiminde bir geçmiş girdisi ekler,
  // geri tuşuna basınca bir önceki sayfaya (yoksa Dashboard'a) döner.
  useEffect(() => {
    function onPopState(e: PopStateEvent) {
      const p = (e.state?.page as Page | undefined) ?? 'dashboard'
      setPage(p)
    }
    window.addEventListener('popstate', onPopState)
    window.history.replaceState({ page: 'dashboard' }, '', '')
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  function navigate(next: Page) {
    if (next === 'maintenances') {
      setJumpToMaintenance(null)
      setMaintFilter(null)
    }
    // Menüden Araçlar'a geçişte, önceden global aramadan/Dashboard'dan
    // açılmış bir araç profili varsa temizle (liste görünümüyle başlasın).
    // Araçlar sayfasından BAŞKA bir sayfaya çıkarken de aynı şekilde
    // temizlenir — böylece bir dahaki sefere normal menüden (deep-link
    // olmadan) girildiğinde asla eski açık araç "yapışık" kalmaz.
    if (next === 'vehicles' || page === 'vehicles') setOpenVehicleId(null)
    if (next === 'parts') setOpenPartId(null)
    setPage(next)
    setNavOpen(false)
    window.history.pushState({ page: next }, '', '')
  }

  // Global aramadan / QR koddan bir aracın profil sayfasını açar.
  function openVehicleProfile(id: number) {
    setOpenVehicleId(id)
    setPage('vehicles')
    setNavOpen(false)
    window.history.pushState({ page: 'vehicles' }, '', '')
  }

  // Global aramadan / QR koddan bir Parça Bilgi Bankası maddesini açar.
  function openPartCatalogItem(id: number) {
    setOpenPartId(id)
    setPage('parts')
    setNavOpen(false)
    window.history.pushState({ page: 'parts' }, '', '')
  }

  // Dashboard'daki periyot grafiğinden ("50.000 km bakımda 9 araç
  // bekliyor" gibi) doğrudan Bakımlar sayfasına, o duruma/periyoda göre
  // önceden filtrelenmiş şekilde gider.
  function goToFilteredMaintenances(filter: { status?: string; planName?: string }) {
    setJumpToMaintenance(null)
    setMaintFilter(filter)
    setPage('maintenances')
    setNavOpen(false)
    window.history.pushState({ page: 'maintenances' }, '', '')
  }

  if (loading) return null
  if (!user) return (
    <Login
      onLogin={(u) => {
        setUser(u)
        api<MyPermissions>('/permissions/me').then(setPerms).catch(() => setPerms(null))
        applyDeepLinkFromUrl()
      }}
    />
  )

  const isAdmin = user.role === 'admin'
  const isStaff = isAdmin || user.role === 'engineer' || user.role === 'amir'
  const p = perms?.permissions ?? {}
  const canManageUsers = isAdmin || !!p.manage_users

  const menu: { key: Page; label: string; show: boolean }[] = [
    { key: 'dashboard', label: 'Dashboard', show: true },
    { key: 'maintenances', label: 'Bakımlar', show: true },
    { key: 'calendar', label: 'Takvim', show: isStaff },
    { key: 'vehicles', label: 'Araçlar', show: isStaff },
    { key: 'history', label: 'Bakım Geçmişi', show: true },
    { key: 'plans', label: 'Bakım Planları', show: isAdmin },
    // Ağaç düzenleme (madde ekle/sil/taşı) admin'e özel kalır, ama zorunlu/
    // opsiyonel işaretleme mühendis/amir tarafından da yapılabilmeli — bu
    // yüzden sayfanın kendisi isStaff'a açık, düzenleme düğmeleri sayfa
    // içinde isAdmin'e göre gizlenir/gösterilir.
    { key: 'tree', label: 'Bakım Ağacı', show: isStaff },
    { key: 'parts', label: 'Parça Bilgi Bankası', show: true },
    { key: 'suggestions', label: 'Öneriler', show: true },
    // KPI Raporu ustaya kasıtlı olarak kapalı — yönetici rollerine (admin/mühendis/amir) özeldir.
    { key: 'kpi', label: 'KPI Raporu', show: isStaff && p.view_dashboard_analytics !== false },
    { key: 'users', label: 'Kullanıcılar', show: canManageUsers },
    { key: 'permissions', label: 'Yetki Yönetimi', show: isAdmin },
    { key: 'audit', label: 'İşlem Kayıtları', show: isAdmin },
    { key: 'system', label: 'Sistem Durumu', show: isAdmin },
    { key: 'help', label: 'Yardım', show: true },
    { key: 'profile', label: 'Profil', show: true },
  ]

  // Eğitim turu yalnızca bu kullanıcının menüsünde görünen sayfalara ait
  // adımları içersin (ör. usta girişinde Araçlar/Planlar adımları atlanır).
  const visibleTourPages = new Set(menu.filter((m) => m.show).map((m) => m.key))
  const tourSteps = ONBOARDING_STEPS.filter((s) => visibleTourPages.has(s.page))

  function openMaintenance(id: number) {
    setJumpToMaintenance(id)
    setMaintFilter(null)
    setPage('maintenances')
    setNavOpen(false)
    window.history.pushState({ page: 'maintenances' }, '', '')
  }

  // Kullanıcının rolüne göre global aramada hangi sonuç türlerine
  // gidebileceği (gidemeyeceği türler dropdown'da gösterilmez).
  const searchAllow: Record<HitType, boolean> = {
    vehicle: isStaff,
    maintenance: true,
    plan: isAdmin,
    part: true,
    user: canManageUsers,
    pdf: true,
  }

  function handleSearchPick(hit: SearchHit) {
    switch (hit.type) {
      case 'vehicle':
        if (hit.ref_id != null) openVehicleProfile(hit.ref_id)
        break
      case 'maintenance':
        if (hit.ref_id != null) openMaintenance(hit.ref_id)
        break
      case 'plan':
        navigate('plans')
        break
      case 'part':
        navigate('parts')
        break
      case 'user':
        navigate('users')
        break
      case 'pdf':
        if (hit.url) window.open(hit.url, '_blank')
        break
    }
  }

  return (
    <div className="app-shell">
      <ToastHost />
      {tourActive && tourSteps.length > 0 && (
        <OnboardingTour steps={tourSteps} currentPage={page} onNavigate={navigate} onClose={closeTour} />
      )}
      {navOpen && <div className="nav-backdrop" onClick={() => setNavOpen(false)} />}
      <aside className={`sidebar${navOpen ? ' nav-open' : ''}${sidebarCollapsed ? ' collapsed' : ''}`}>
        <div className="brand">
          <ParticleNetwork particleDensity={0.00025} minParticles={10} maxParticles={26} linkDistance={70} mouseLinkDistance={90} />
          <span className="logo-dot">
            <Box size={17} />
          </span>
          <span className="brand-text">
            Ulaşım Bakım
            <small>Yönetim Sistemi</small>
          </span>
        </div>
        <div className="sidebar-toggle-row">
          <button
            className="sidebar-collapse-btn"
            title={sidebarCollapsed ? 'Kenar çubuğunu genişlet' : 'Kenar çubuğunu daralt'}
            onClick={() => setSidebarCollapsed((v) => !v)}
          >
            {sidebarCollapsed ? <ChevronRight size={15} /> : <ChevronLeft size={15} />}
          </button>
        </div>
        <div className="sidebar-divider" />
        <nav>
          <span className="nav-section-label">Genel</span>
          {menu
            .filter((m) => m.show)
            .map((m) => (
              <button
                key={m.key}
                className={page === m.key ? 'active' : ''}
                title={sidebarCollapsed ? m.label : undefined}
                onClick={() => navigate(m.key)}
              >
                <span className="nav-icon">{NAV_ICONS[m.key]}</span>
                <span className="nav-label">{m.label}</span>
              </button>
            ))}
        </nav>
        <div className="userbox">
          <button
            className="userbox-identity"
            title="Profil"
            onClick={() => navigate('profile')}
          >
            <Avatar name={user.full_name} size={32} />
            <span className="who">
              <span className="name">{user.full_name}</span>
              <span className="role">{ROLE_TR[user.role]}</span>
            </span>
          </button>
          <button
            className="userbox-logout"
            title="Çıkış Yap"
            onClick={() => {
              revokeRefreshToken()
              setToken(null)
              setRefreshToken(null)
              setUser(null)
              setPage('dashboard')
              setNavOpen(false)
            }}
          >
            <LogOut size={14} />
          </button>
        </div>
      </aside>
      <div className="main-area">
        <div className="topbar-strip">
          <button className="hamburger-btn" title="Menü" onClick={() => setNavOpen((v) => !v)}>
            <Menu size={18} />
          </button>
          <nav className="breadcrumb">
            <span className="crumb">Ulaşım Bakım</span>
            <ChevronDivider />
            <span className="crumb current">{PAGE_TITLES[page]}</span>
          </nav>
          <TopbarTools
            allow={searchAllow}
            onPick={handleSearchPick}
            onOpenMaintenance={openMaintenance}
            onStartTour={() => setTourActive(true)}
          />
        </div>
        <main className="content">
          <div key={page} className="page-transition">
          {page === 'dashboard' && (
            <DashboardPage
              user={user}
              perms={p}
              onOpenMaintenance={openMaintenance}
              onGoToMaintenances={() => navigate('maintenances')}
              onFilterMaintenances={goToFilteredMaintenances}
              onNavigate={(p) => navigate(p)}
            />
          )}
          {page === 'maintenances' && (
            <MaintenancesPage
              user={user}
              perms={p}
              initialSelectedId={jumpToMaintenance}
              initialStatus={maintFilter?.status}
              initialPlanName={maintFilter?.planName}
            />
          )}
          {page === 'calendar' && isStaff && (
            <CalendarPage perms={p} onOpenMaintenance={openMaintenance} />
          )}
          {page === 'history' && (
            <HistoryPage user={user} perms={p} onOpenMaintenance={openMaintenance} />
          )}
          {page === 'vehicles' && isStaff && (
            <VehiclesPage
              user={user}
              perms={p}
              onOpenMaintenance={openMaintenance}
              initialVehicleId={openVehicleId}
            />
          )}
          {page === 'plans' && isAdmin && <PlansPage />}
          {page === 'tree' && isStaff && <TreeEditorPage user={user} />}
          {page === 'parts' && <PartsCatalogPage canEdit={isStaff} initialSelectedId={openPartId} />}
          {page === 'suggestions' && <SuggestionsPage user={user} />}
          {page === 'kpi' && isStaff && p.view_dashboard_analytics !== false && <KPIPage />}
          {page === 'users' && canManageUsers && <UsersPage currentUser={user} />}
          {page === 'permissions' && isAdmin && <PermissionsPage />}
          {page === 'audit' && isAdmin && <AuditLogPage />}
          {page === 'system' && isAdmin && <SystemStatusPage />}
          {page === 'help' && <HelpPage isAdmin={isAdmin} isStaff={isStaff} />}
          {page === 'profile' && (
            <ProfilePage user={user} theme={theme} onThemeChange={setTheme} onUserUpdate={setUser} />
          )}
          </div>
        </main>
      </div>
    </div>
  )
}

function ChevronDivider() {
  return (
    <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2" style={{ opacity: 0.5 }}>
      <path d="M9 6l6 6-6 6" />
    </svg>
  )
}
