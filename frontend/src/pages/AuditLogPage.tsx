import { Fragment, useEffect, useMemo, useState } from 'react'
import { api, apiDownload } from '../api'
import type { AuditPage, BackupInfo, BackupRunResult, ExportFormat, User } from '../types'
import { AUDIT_ACTION_TR, AUDIT_ENTITY_TR } from '../types'
import { PageHeader, Badge, EmptyState, SkeletonTableRows, SearchInput } from '../components/ui'
import { ListFilter, ScrollText, DatabaseBackup, Download, RefreshCw, ChevronDown, ChevronRight, Globe, Monitor, ArrowRight } from 'lucide-react'

const fmt = (s: string) => new Date(s).toLocaleString('tr-TR')
const PAGE_SIZE = 50

const ACTION_TONE: Record<string, 'pending' | 'in_progress' | 'completed' | 'danger' | 'neutral'> = {
  create: 'completed',
  update: 'in_progress',
  delete: 'danger',
  complete: 'completed',
  unlock: 'pending',
  import: 'neutral',
  export: 'neutral',
  backup: 'completed',
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** Otomatik veritabanı yedekleri paneli — günlük otomatik yedek zaten
 * arka planda alınır; burada mevcut yedekleri görüp indirebilir (sunucu
 * dışına bir kopya almak için) veya elle ek bir yedek tetikleyebilirsiniz. */
function BackupsPanel() {
  const [backups, setBackups] = useState<BackupInfo[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const load = () => api<BackupInfo[]>('/backups').then(setBackups).catch(() => setBackups([]))
  useEffect(() => {
    load()
  }, [])

  async function runNow() {
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const r = await api<BackupRunResult>('/backups/run', { method: 'POST' })
      setMessage(r.message)
      load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card slide-up" style={{ marginBottom: 16 }}>
      <div className="card-head">
        <div>
          <div className="card-title">
            <DatabaseBackup size={16} style={{ verticalAlign: -3, marginRight: 6 }} />
            Veritabanı Yedekleri
          </div>
          <div className="card-subtitle">Günlük yedek alınır; son 30 yedek saklanır.</div>
        </div>
        <button className="small" disabled={busy} onClick={runNow}>
          <RefreshCw size={13} style={{ marginRight: 5, verticalAlign: -2 }} />
          Şimdi Yedekle
        </button>
      </div>
      {error && <div className="error">{error}</div>}
      {message && <div className="muted" style={{ color: 'var(--green)', fontWeight: 600 }}>{message}</div>}
      <div className="table-scroll" style={{ marginTop: 10 }}>
        <table>
          <thead>
            <tr>
              <th>Dosya</th>
              <th>Boyut</th>
              <th>Tarih</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {backups === null && <SkeletonTableRows columns={4} rows={3} />}
            {backups !== null && backups.map((b) => (
              <tr key={b.filename}>
                <td style={{ fontFamily: 'monospace', fontSize: 12.5 }}>{b.filename}</td>
                <td>{fmtSize(b.size_bytes)}</td>
                <td>{fmt(b.created_at)}</td>
                <td>
                  <button className="small" onClick={() => apiDownload(`/backups/${b.filename}/download`, b.filename)}>
                    <Download size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                    İndir
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {backups !== null && backups.length === 0 && (
          <EmptyState
            icon={<DatabaseBackup size={20} />}
            title="Henüz yedek yok"
            description="İlk otomatik yedek kısa süre içinde alınacak, veya 'Şimdi Yedekle' ile hemen oluşturabilirsiniz."
          />
        )}
      </div>
    </div>
  )
}

/** İşlem Kayıtları (Faz 17): değiştirilemez audit log. Yalnızca Admin.
 * Aksiyon, varlık türü, personel, tarih aralığı ve serbest metinle filtrelenir. */
export default function AuditLogPage() {
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [action, setAction] = useState('')
  const [entity, setEntity] = useState('')
  const [actorId, setActorId] = useState('')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(0)
  const [data, setData] = useState<AuditPage | null>(null)
  const [users, setUsers] = useState<User[]>([])
  const [exportFormat, setExportFormat] = useState<ExportFormat>('xlsx')

  useEffect(() => {
    api<User[]>('/users').then(setUsers).catch(() => setUsers([]))
  }, [])

  const query = useMemo(() => {
    const p = new URLSearchParams()
    if (action) p.set('action', action)
    if (entity) p.set('entity_type', entity)
    if (actorId) p.set('actor_id', actorId)
    if (dateFrom) p.set('date_from', dateFrom)
    if (dateTo) p.set('date_to', dateTo)
    if (q.trim()) p.set('q', q.trim())
    p.set('limit', String(PAGE_SIZE))
    p.set('offset', String(page * PAGE_SIZE))
    return p.toString()
  }, [action, entity, actorId, dateFrom, dateTo, q, page])

  // Filtre değişince ilk sayfaya dön
  useEffect(() => setPage(0), [action, entity, actorId, dateFrom, dateTo, q])

  useEffect(() => {
    setData(null)
    api<AuditPage>(`/audit?${query}`).then(setData).catch(() => setData({ total: 0, items: [] }))
  }, [query])

  const items = data?.items ?? []
  const total = data?.total ?? 0
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const hasFilter = action || entity || actorId || dateFrom || dateTo || q

  const exportQuery = useMemo(() => {
    const p = new URLSearchParams()
    if (action) p.set('action', action)
    if (entity) p.set('entity_type', entity)
    if (actorId) p.set('actor_id', actorId)
    if (dateFrom) p.set('date_from', dateFrom)
    if (dateTo) p.set('date_to', dateTo)
    if (q.trim()) p.set('q', q.trim())
    p.set('format', exportFormat)
    return p.toString()
  }, [action, entity, actorId, dateFrom, dateTo, q, exportFormat])

  return (
    <>
      <PageHeader
        title="İşlem Kayıtları"
      />

      <BackupsPanel />

      <div className="table-card fade-in">
        <div className="table-toolbar" style={{ flexWrap: 'wrap' }}>
          <ListFilter size={15} color="var(--text-tertiary)" />
          <select value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="">Tüm İşlemler</option>
            {Object.entries(AUDIT_ACTION_TR).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
          <select value={entity} onChange={(e) => setEntity(e.target.value)}>
            <option value="">Tüm Kayıt Türleri</option>
            {Object.entries(AUDIT_ENTITY_TR).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
          <select value={actorId} onChange={(e) => setActorId(e.target.value)}>
            <option value="">Tüm Personel</option>
            {users.map((u) => (
              <option key={u.id} value={u.id}>{u.full_name}</option>
            ))}
          </select>
          <label className="filter-inline">
            <span>Tarih</span>
            <input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
            <span>–</span>
            <input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
          </label>
          <SearchInput value={q} onChange={setQ} placeholder="Kayıtlarda ara…" style={{ minWidth: 200 }} />
          {hasFilter && (
            <button
              className="small ghost"
              onClick={() => { setAction(''); setEntity(''); setActorId(''); setDateFrom(''); setDateTo(''); setQ('') }}
            >
              Filtreleri Temizle
            </button>
          )}
          <span className="muted" style={{ marginLeft: 'auto' }}>{total} kayıt</span>
          <select value={exportFormat} onChange={(e) => setExportFormat(e.target.value as ExportFormat)}>
            <option value="xlsx">Excel</option>
            <option value="pdf">PDF</option>
            <option value="csv">CSV</option>
            <option value="docx">Word</option>
          </select>
          <button
            className="small"
            onClick={() => apiDownload(`/audit/export?${exportQuery}`, `islem_kayitlari.${exportFormat}`)}
          >
            <Download size={13} style={{ marginRight: 5, verticalAlign: -2 }} />
            Dışa Aktar
          </button>
        </div>

        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th></th>
                <th>Tarih / Saat</th>
                <th>Personel</th>
                <th>İşlem</th>
                <th>Kayıt Türü</th>
                <th>Açıklama</th>
              </tr>
            </thead>
            <tbody>
              {data === null && <SkeletonTableRows columns={6} />}
              {data !== null &&
                items.map((a) => {
                  const hasDetail = a.old_value || a.new_value || a.ip_address || a.user_agent
                  const isOpen = expandedId === a.id
                  return (
                    <Fragment key={a.id}>
                      <tr
                        className={hasDetail ? 'clickable-row' : undefined}
                        onClick={() => hasDetail && setExpandedId(isOpen ? null : a.id)}
                      >
                        <td style={{ width: 24 }}>
                          {hasDetail && (isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />)}
                        </td>
                        <td style={{ whiteSpace: 'nowrap' }}>{fmt(a.created_at)}</td>
                        <td>{a.actor_name}</td>
                        <td><Badge tone={ACTION_TONE[a.action] ?? 'neutral'}>{AUDIT_ACTION_TR[a.action] ?? a.action}</Badge></td>
                        <td>{AUDIT_ENTITY_TR[a.entity_type] ?? a.entity_type}</td>
                        <td>
                          <div>{a.summary}</div>
                          {a.detail && <div className="muted" style={{ fontSize: 12 }}>{a.detail}</div>}
                        </td>
                      </tr>
                      {isOpen && hasDetail && (
                        <tr key={`${a.id}-detail`}>
                          <td></td>
                          <td colSpan={5} style={{ background: 'var(--bg-soft, rgba(0,0,0,0.02))', padding: '10px 16px' }}>
                            <div className="row" style={{ gap: 24, flexWrap: 'wrap', fontSize: 12.5 }}>
                              {(a.old_value || a.new_value) && (
                                <span className="row" style={{ gap: 6 }}>
                                  <span className="muted">Değişim:</span>
                                  <span>{a.old_value || '—'}</span>
                                  <ArrowRight size={12} />
                                  <span style={{ fontWeight: 600 }}>{a.new_value || '—'}</span>
                                </span>
                              )}
                              {a.ip_address && (
                                <span className="row" style={{ gap: 5 }}>
                                  <Globe size={12} />
                                  <span className="muted">IP:</span>
                                  <span style={{ fontFamily: 'monospace' }}>{a.ip_address}</span>
                                </span>
                              )}
                              {a.user_agent && (
                                <span className="row" style={{ gap: 5 }}>
                                  <Monitor size={12} />
                                  <span className="muted">Tarayıcı:</span>
                                  <span>{a.user_agent}</span>
                                </span>
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
            </tbody>
          </table>
          {data !== null && items.length === 0 && (
            <EmptyState
              icon={<ScrollText size={22} />}
              title="Kayıt bulunamadı"
              description="Seçtiğiniz filtrelere uyan bir işlem kaydı yok."
            />
          )}
        </div>

        {pageCount > 1 && (
          <div className="row" style={{ justifyContent: 'center', padding: 12, gap: 12 }}>
            <button className="small" disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>
              ← Önceki
            </button>
            <span className="muted">{page + 1} / {pageCount}</span>
            <button className="small" disabled={page + 1 >= pageCount} onClick={() => setPage((p) => p + 1)}>
              Sonraki →
            </button>
          </div>
        )}
      </div>
    </>
  )
}
