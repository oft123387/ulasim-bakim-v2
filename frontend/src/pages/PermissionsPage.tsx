import { useEffect, useState } from 'react'
import { api } from '../api'
import type { PermissionKey, PermissionsMatrix } from '../types'
import { PERMISSION_KEYS, PERMISSION_TR } from '../types'
import { PageHeader } from '../components/ui'
import { ShieldCheck } from 'lucide-react'

const ROLE_COLS: { key: 'engineer' | 'amir' | 'technician'; label: string }[] = [
  { key: 'engineer', label: 'Mühendis' },
  { key: 'amir', label: 'Amir' },
  { key: 'technician', label: 'Usta' },
]

export default function PermissionsPage() {
  const [matrix, setMatrix] = useState<PermissionsMatrix | null>(null)
  const [error, setError] = useState('')
  const [busyKey, setBusyKey] = useState('')

  const load = () =>
    api<PermissionsMatrix>('/permissions')
      .then(setMatrix)
      .catch((e) => setError((e as Error).message))

  useEffect(() => {
    load()
  }, [])

  async function toggle(role: 'engineer' | 'amir' | 'technician', permission: PermissionKey, current: boolean) {
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

  return (
    <>
      <PageHeader
        title="Yetki Yönetimi"
        subtitle="Mühendis, amir ve usta rolleri için görüntüleme, düzenleme, silme, onaylama, Excel aktarma ve kullanıcı ekleme izinlerini ayrı ayrı yönetin. Admin her zaman tüm yetkilere sahiptir ve buradan kısıtlanamaz."
      />

      {error && <div className="error" style={{ marginBottom: 12 }}>{error}</div>}

      <div className="table-card fade-in">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>İzin</th>
                <th style={{ textAlign: 'center' }}>Admin</th>
                {ROLE_COLS.map((r) => (
                  <th key={r.key} style={{ textAlign: 'center' }}>{r.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {matrix === null && !error && (
                <tr><td colSpan={5} className="muted">Yükleniyor…</td></tr>
              )}
              {matrix !== null &&
                PERMISSION_KEYS.map((key) => (
                  <tr key={key}>
                    <td style={{ fontWeight: 600 }}>
                      <div className="row" style={{ gap: 8 }}>
                        <ShieldCheck size={14} color="var(--text-tertiary)" />
                        {PERMISSION_TR[key]}
                      </div>
                    </td>
                    <td style={{ textAlign: 'center' }}>
                      <input type="checkbox" checked disabled title="Admin her zaman tüm yetkilere sahiptir" />
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
                          />
                        </td>
                      )
                    })}
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}
