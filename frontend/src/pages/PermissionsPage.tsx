import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { PermissionDef, PermissionKey, PermissionsMatrix } from '../types'
import { PERMISSION_KEYS, PERMISSION_TR } from '../types'
import { PageHeader, EmptyState, SearchInput, Avatar } from '../components/ui'
import {
  ShieldCheck,
  Wrench,
  FileDown,
  Users,
  LayoutDashboard,
  Lock,
  RotateCcw,
  Info,
} from 'lucide-react'

type EditableRole = 'engineer' | 'amir' | 'technician'

const ROLE_COLS: { key: EditableRole; label: string }[] = [
  { key: 'engineer', label: 'Mühendis' },
  { key: 'amir', label: 'Amir' },
  { key: 'technician', label: 'Usta' },
]

const CATEGORY_ICON: Record<string, React.ReactNode> = {
  'Bakım İşlemleri': <Wrench size={14} />,
  'Veri ve Dışa Aktarma': <FileDown size={14} />,
  'Kullanıcı Yönetimi': <Users size={14} />,
  'Dashboard Görünürlüğü': <LayoutDashboard size={14} />,
}

// defs endpoint henüz yüklenmemişse (ya da eski sunucu sürümünde yoksa) tüm
// izinleri tek bir "Genel" kategorisinde göstererek sayfanın çalışmaya devam
// etmesini sağlayan yedek.
function fallbackDefs(): PermissionDef[] {
  return PERMISSION_KEYS.map((key) => ({ key, category: 'Genel', description: '' }))
}

export default function PermissionsPage() {
  const [matrix, setMatrix] = useState<PermissionsMatrix | null>(null)
  const [defs, setDefs] = useState<PermissionDef[] | null>(null)
  const [error, setError] = useState('')
  const [busyKey, setBusyKey] = useState('')
  const [resettingRole, setResettingRole] = useState('')
  const [search, setSearch] = useState('')

  const load = () => {
    api<PermissionsMatrix>('/permissions')
      .then(setMatrix)
      .catch((e) => setError((e as Error).message))
    api<PermissionDef[]>('/permissions/defs')
      .then(setDefs)
      .catch(() => setDefs(fallbackDefs()))
  }

  useEffect(() => {
    load()
  }, [])

  async function toggle(role: EditableRole, permission: PermissionKey, current: boolean) {
    const key = `${role}-${permission}`
    setBusyKey(key)
    setError('')
    // İyimser güncelleme: anında geri bildirim, hata olursa geri al.
    setMatrix((m) => (m ? { ...m, [role]: { ...m[role], [permission]: !current } } : m))
    try {
      const updated = await api<PermissionsMatrix>('/permissions', {
        method: 'PATCH',
        body: { role, permission, allowed: !current },
      })
      setMatrix(updated)
    } catch (e) {
      setError((e as Error).message)
      setMatrix((m) => (m ? { ...m, [role]: { ...m[role], [permission]: current } } : m))
    } finally {
      setBusyKey('')
    }
  }

  async function resetRole(role: EditableRole, label: string) {
    if (!confirm(`${label} rolünün TÜM izinleri fabrika varsayılanlarına döndürülsün mü?`)) return
    setResettingRole(role)
    setError('')
    try {
      const updated = await api<PermissionsMatrix>('/permissions/reset', {
        method: 'POST',
        body: { role },
      })
      setMatrix(updated)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setResettingRole('')
    }
  }

  const grouped = useMemo(() => {
    const q = search.trim().toLowerCase()
    const list = (defs ?? fallbackDefs()).filter((d) => PERMISSION_KEYS.includes(d.key as PermissionKey))
    const filtered = q
      ? list.filter(
          (d) =>
            (PERMISSION_TR[d.key as PermissionKey] ?? d.key).toLowerCase().includes(q) ||
            d.description.toLowerCase().includes(q) ||
            d.category.toLowerCase().includes(q),
        )
      : list
    const byCat = new Map<string, PermissionDef[]>()
    for (const d of filtered) {
      if (!byCat.has(d.category)) byCat.set(d.category, [])
      byCat.get(d.category)!.push(d)
    }
    return Array.from(byCat.entries())
  }, [defs, search])

  const loading = matrix === null && !error

  return (
    <>
      <PageHeader
        title="Yetki Yönetimi"
        subtitle="Mühendis, Amir ve Usta rollerinin neler yapabildiğini buradan yönetin. Değişiklikler anında kaydedilir."
      />

      <div
        className="card"
        style={{
          marginBottom: 14, display: 'flex', alignItems: 'flex-start', gap: 10,
          background: 'var(--surface-alt, rgba(0,0,0,0.02))',
        }}
      >
        <Info size={16} color="var(--text-tertiary)" style={{ flexShrink: 0, marginTop: 1 }} />
        <div className="muted" style={{ fontSize: 12.5 }}>
          Admin her zaman tüm izinlere sahiptir ve buradan kısıtlanamaz. "Dashboard Görünürlüğü"
          gibi bazı izinler bir aksiyonu değil, arayüzdeki bir bölümün görünüp görünmeyeceğini
          kontrol eder — ileride yeni görünürlük izinleri (ör. başka bir dashboard kartı) aynı
          şekilde buraya eklenebilir.
        </div>
      </div>

      <SearchInput value={search} onChange={setSearch} placeholder="İzin ara…" style={{ marginBottom: 14 }} />

      {error && <div className="error" style={{ marginBottom: 12 }}>{error}</div>}

      {loading && <div className="muted">Yükleniyor…</div>}

      {!loading && matrix !== null && grouped.length === 0 && (
        <EmptyState icon={<ShieldCheck size={28} />} title="Eşleşen izin bulunamadı" description="Farklı bir arama terimi deneyin." />
      )}

      {!loading &&
        matrix !== null &&
        grouped.map(([category, items]) => (
          <div key={category} className="table-card fade-in" style={{ marginBottom: 16 }}>
            <div className="table-toolbar">
              <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                {CATEGORY_ICON[category] ?? <ShieldCheck size={14} />}
                {category}
                <span className="muted" style={{ fontWeight: 400, fontSize: 12 }}>({items.length})</span>
              </div>
            </div>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>İzin</th>
                    <th style={{ textAlign: 'center', width: 90 }}>
                      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
                        <Avatar name="Admin" size={26} />
                        <span style={{ fontSize: 11.5 }}>Admin</span>
                      </div>
                    </th>
                    {ROLE_COLS.map((r) => (
                      <th key={r.key} style={{ textAlign: 'center', width: 100 }}>
                        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
                          <Avatar name={r.label} size={26} />
                          <span style={{ fontSize: 11.5 }}>{r.label}</span>
                          <button
                            className="ghost"
                            style={{ fontSize: 10.5, padding: '2px 6px', height: 'auto' }}
                            disabled={resettingRole === r.key}
                            onClick={() => resetRole(r.key, r.label)}
                            title={`${r.label} izinlerini varsayılana sıfırla`}
                          >
                            <RotateCcw size={10} style={{ marginRight: 3, verticalAlign: -1 }} />
                            {resettingRole === r.key ? 'Sıfırlanıyor…' : 'Sıfırla'}
                          </button>
                        </div>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {items.map((d) => {
                    const key = d.key as PermissionKey
                    return (
                      <tr key={d.key}>
                        <td>
                          <div style={{ fontWeight: 600, fontSize: 13.5 }}>{PERMISSION_TR[key] ?? d.key}</div>
                          {d.description && (
                            <div className="muted" style={{ fontSize: 11.5, marginTop: 2 }}>{d.description}</div>
                          )}
                        </td>
                        <td style={{ textAlign: 'center' }}>
                          <span title="Admin her zaman tüm yetkilere sahiptir" style={{ color: 'var(--text-tertiary)' }}>
                            <Lock size={14} />
                          </span>
                        </td>
                        {ROLE_COLS.map((r) => {
                          const value = matrix[r.key]?.[key] ?? false
                          const busy = busyKey === `${r.key}-${key}`
                          return (
                            <td key={r.key} style={{ textAlign: 'center' }}>
                              <input
                                type="checkbox"
                                checked={value}
                                disabled={busy}
                                onChange={() => toggle(r.key, key, value)}
                                style={{ width: 17, height: 17, cursor: busy ? 'wait' : 'pointer' }}
                              />
                            </td>
                          )
                        })}
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        ))}
    </>
  )
}
