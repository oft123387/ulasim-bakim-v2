import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { Shift, User } from '../types'
import { ROLE_TR } from '../types'
import { PageHeader, Badge, Avatar, SearchInput, EmptyState, SkeletonTableRows } from '../components/ui'
import { showToast } from '../components/toast'
import { PlusCircle, Users as UsersIcon, ListFilter, Trash2 } from 'lucide-react'

const PAGE_SIZE = 12

export default function UsersPage({ currentUser }: { currentUser: User }) {
  const isAdmin = currentUser.role === 'admin'
  const [list, setList] = useState<User[] | null>(null)
  const [shifts, setShifts] = useState<Shift[]>([])
  const [username, setUsername] = useState('')
  const [fullName, setFullName] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState('technician')
  const [error, setError] = useState('')

  const [search, setSearch] = useState('')
  const [roleFilter, setRoleFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [page, setPage] = useState(1)

  const load = () => api<User[]>('/users').then(setList).catch(() => setList([]))
  useEffect(() => {
    load()
    api<Shift[]>('/shifts').then(setShifts).catch(() => setShifts([]))
  }, [])

  async function changeShift(u: User, shiftId: number | null) {
    if (shiftId === u.shift_id) return
    try {
      await api(`/users/${u.id}`, { method: 'PATCH', body: { shift_id: shiftId } })
      load()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function add(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    try {
      await api('/users', { method: 'POST', body: { username, full_name: fullName, password, role } })
      setUsername('')
      setFullName('')
      setPassword('')
      load()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function toggle(u: User) {
    await api(`/users/${u.id}`, { method: 'PATCH', body: { is_active: !u.is_active } })
    load()
  }

  // Pasife almaktan farklı olarak hesabı kalıcı olarak siler — geri
  // alınamaz. Geçmiş bakım/not/işlem kayıtları kaybolmaz (yalnızca "kim
  // yaptı" bağlantısı kalkar), bu yüzden onay metni bunu açıkça belirtir.
  async function remove(u: User) {
    if (
      !confirm(
        `"${u.full_name}" (@${u.username}) hesabı KALICI OLARAK silinecek. Bu işlem geri alınamaz.\n\n` +
          `Geçmiş bakım/not kayıtları kaybolmaz, yalnızca bu kullanıcıya olan bağlantıları kalkar. ` +
          `Hesabı yalnızca geçici olarak devre dışı bırakmak istiyorsanız bunun yerine "Pasife Al" kullanın.\n\n` +
          `Devam edilsin mi?`,
      )
    )
      return
    setError('')
    try {
      await api(`/users/${u.id}`, { method: 'DELETE' })
      load()
      showToast(`"${u.full_name}" silindi`, 'info')
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function changeRole(u: User, newRole: string) {
    if (newRole === u.role) return
    if (!confirm(`${u.full_name} kullanıcısının rolü "${ROLE_TR[newRole]}" olarak değiştirilsin mi?`)) return
    try {
      await api(`/users/${u.id}`, { method: 'PATCH', body: { role: newRole } })
      load()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const all = list ?? []
  const counts = useMemo(
    () => ({
      total: all.length,
      active: all.filter((u) => u.is_active).length,
      pending: all.filter((u) => !u.is_active).length,
      admin: all.filter((u) => u.role === 'admin').length,
      engineer: all.filter((u) => u.role === 'engineer').length,
      technician: all.filter((u) => u.role === 'technician').length,
      amir: all.filter((u) => u.role === 'amir').length,
    }),
    [all],
  )

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return all
      .filter((u) => !roleFilter || u.role === roleFilter)
      .filter((u) => !statusFilter || (statusFilter === 'active' ? u.is_active : !u.is_active))
      .filter((u) => !q || u.full_name.toLowerCase().includes(q) || u.username.toLowerCase().includes(q))
      .sort((a, b) => {
        // Onay bekleyen (pasif) kullanıcılar önce, sonra ada göre alfabetik.
        if (a.is_active !== b.is_active) return a.is_active ? 1 : -1
        return a.full_name.localeCompare(b.full_name, 'tr')
      })
  }, [all, search, roleFilter, statusFilter])

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const pageSafe = Math.min(page, pageCount)
  const paged = filtered.slice((pageSafe - 1) * PAGE_SIZE, pageSafe * PAGE_SIZE)

  useEffect(() => {
    setPage(1)
  }, [search, roleFilter, statusFilter])

  return (
    <>
      <PageHeader title="Kullanıcılar" />

      <div className="row" style={{ gap: 10, marginBottom: 16, flexWrap: 'wrap' }}>
        <StatChip label="Toplam" value={counts.total} />
        <StatChip label="Aktif" value={counts.active} tone="completed" />
        <StatChip label="Onay Bekleyen / Pasif" value={counts.pending} tone={counts.pending > 0 ? 'pending' : 'neutral'} />
        <StatChip label="Admin" value={counts.admin} />
        <StatChip label="Mühendis" value={counts.engineer} />
        <StatChip label="Amir" value={counts.amir} />
        <StatChip label="Usta" value={counts.technician} />
      </div>

      <div className="card">
        <div className="card-head"><div className="card-title">Yeni Kullanıcı Ekle</div></div>
        <form className="row" onSubmit={add}>
          <input placeholder="Kullanıcı adı" value={username} onChange={(e) => setUsername(e.target.value)} required />
          <input placeholder="Ad Soyad" value={fullName} onChange={(e) => setFullName(e.target.value)} required />
          <input placeholder="Şifre (en az 8 karakter, harf ve rakam)" type="password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={8} required />
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="technician">Usta</option>
            <option value="engineer">Mühendis</option>
            <option value="amir">Amir</option>
            {isAdmin && <option value="admin">Admin</option>}
          </select>
          <button className="primary"><PlusCircle size={14} style={{ marginRight: 6, verticalAlign: -2 }} />Kullanıcı Ekle</button>
        </form>
        {error && <div className="error">{error}</div>}
      </div>

      <div className="table-card fade-in">
        <div className="table-toolbar">
          <ListFilter size={15} color="var(--text-tertiary)" />
          <SearchInput value={search} onChange={setSearch} placeholder="Ada veya kullanıcı adına göre ara…" />
          <select value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)}>
            <option value="">Tüm Roller</option>
            <option value="admin">Admin</option>
            <option value="engineer">Mühendis</option>
            <option value="amir">Amir</option>
            <option value="technician">Usta</option>
          </select>
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">Tüm Durumlar</option>
            <option value="active">Aktif</option>
            <option value="pending">Pasif / Onay Bekleyen</option>
          </select>
          {(search || roleFilter || statusFilter) && (
            <button className="small ghost" onClick={() => { setSearch(''); setRoleFilter(''); setStatusFilter('') }}>
              Filtreleri Temizle
            </button>
          )}
          <span className="muted" style={{ marginLeft: 'auto' }}>{filtered.length} kullanıcı</span>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Kullanıcı</th>
                <th>Rol</th>
                <th>Vardiya</th>
                <th>Durum</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {list === null && <SkeletonTableRows columns={5} />}
              {list !== null && paged.map((u) => (
                <tr key={u.id}>
                  <td>
                    <div className="row" style={{ gap: 10 }}>
                      <Avatar name={u.full_name} size={30} />
                      <span>
                        <div style={{ fontWeight: 600 }}>{u.full_name}</div>
                        <div className="muted" style={{ fontSize: 12 }}>@{u.username}</div>
                      </span>
                    </div>
                  </td>
                  <td>
                    {isAdmin && u.id !== currentUser.id ? (
                      <select value={u.role} onChange={(e) => changeRole(u, e.target.value)} style={{ fontSize: 12.5, padding: '5px 8px' }}>
                        <option value="technician">Usta</option>
                        <option value="engineer">Mühendis</option>
                        <option value="amir">Amir</option>
                        <option value="admin">Admin</option>
                      </select>
                    ) : (
                      ROLE_TR[u.role]
                    )}
                  </td>
                  <td>
                    {isAdmin ? (
                      <select
                        value={u.shift_id ?? ''}
                        onChange={(e) => changeShift(u, e.target.value === '' ? null : Number(e.target.value))}
                        style={{ fontSize: 12.5, padding: '5px 8px' }}
                      >
                        <option value="">Atanmamış</option>
                        {shifts.map((s) => (
                          <option key={s.id} value={s.id}>{s.name}</option>
                        ))}
                      </select>
                    ) : (
                      shifts.find((s) => s.id === u.shift_id)?.name ?? '—'
                    )}
                  </td>
                  <td>
                    <Badge tone={u.is_active ? 'completed' : 'pending'}>
                      {u.is_active ? 'Aktif' : 'Pasif / Onay Bekliyor'}
                    </Badge>
                  </td>
                  <td>
                    {u.id !== currentUser.id && (
                      <div className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
                        <button className="small" onClick={() => toggle(u)}>
                          {u.is_active ? 'Pasife Al' : 'Aktifleştir'}
                        </button>
                        <button className="small danger" title="Hesabı kalıcı olarak sil" onClick={() => remove(u)}>
                          <Trash2 size={12} style={{ marginRight: 4, verticalAlign: -2 }} />
                          Sil
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {list !== null && filtered.length === 0 && (
            <EmptyState
              icon={<UsersIcon size={22} />}
              title="Kullanıcı bulunamadı"
              description="Arama veya filtre kriterlerinize uyan bir kullanıcı yok."
            />
          )}
        </div>
        {pageCount > 1 && (
          <div className="row" style={{ justifyContent: 'center', gap: 8, padding: '12px 0' }}>
            <button className="small ghost" disabled={pageSafe <= 1} onClick={() => setPage(pageSafe - 1)}>
              Önceki
            </button>
            <span className="muted" style={{ fontSize: 12.5 }}>{pageSafe} / {pageCount}</span>
            <button className="small ghost" disabled={pageSafe >= pageCount} onClick={() => setPage(pageSafe + 1)}>
              Sonraki
            </button>
          </div>
        )}
      </div>
    </>
  )
}

function StatChip({
  label,
  value,
  tone,
}: {
  label: string
  value: number
  tone?: 'completed' | 'pending' | 'neutral'
}) {
  return (
    <div className={`user-stat-chip ${tone ? `tone-${tone}` : ''}`}>
      <span className="user-stat-value">{value}</span>
      <span className="user-stat-label">{label}</span>
    </div>
  )
}
