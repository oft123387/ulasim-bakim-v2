import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { Plan, Suggestion, User, Vehicle } from '../types'
import { SUGGESTION_STATUS_TR } from '../types'
import { PageHeader, Badge, Avatar, EmptyState, SkeletonTableRows } from '../components/ui'
import { Lightbulb, Send, Check, X, Workflow, Trash2, StickyNote } from 'lucide-react'

const fmt = (s: string | null) => (s ? new Date(s).toLocaleString('tr-TR') : '—')

const STATUS_TONE: Record<Suggestion['status'], 'pending' | 'in_progress' | 'completed' | 'danger'> = {
  pending: 'pending',
  approved: 'in_progress',
  added: 'completed',
  rejected: 'danger',
}

// Öneri metni uzun olabilir; tabloda satır satır alta aksın diye normal
// kaydırma kullanılır, belli bir uzunluğu geçince de "Devamını gör" ile
// tamamı açılıp okunabilir.
const TEXT_PREVIEW_LEN = 140

function TruncatedText({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false)
  const isLong = text.length > TEXT_PREVIEW_LEN
  const shown = expanded || !isLong ? text : text.slice(0, TEXT_PREVIEW_LEN).trimEnd() + '…'
  return (
    <div style={{ whiteSpace: 'normal', wordBreak: 'break-word' }}>
      <span>{shown}</span>
      {isLong && (
        <button
          className="ghost small"
          onClick={() => setExpanded((v) => !v)}
          style={{ display: 'block', marginTop: 4, padding: '2px 0', fontSize: 12 }}
        >
          {expanded ? 'Daha az göster' : 'Devamını gör'}
        </button>
      )}
    </div>
  )
}

export default function SuggestionsPage({ user, canReview }: { user: User; canReview: boolean }) {
  const [list, setList] = useState<Suggestion[] | null>(null)
  const [vehicles, setVehicles] = useState<Vehicle[]>([])
  const [plans, setPlans] = useState<Plan[]>([])
  const [error, setError] = useState('')
  const [statusFilter, setStatusFilter] = useState('')

  // Yeni öneri formu
  const [vehicleId, setVehicleId] = useState<number | ''>('')
  const [planId, setPlanId] = useState<number | ''>('')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)

  // Admin değerlendirme: hangi öneri için not/durum düzenleme paneli açık
  const [reviewId, setReviewId] = useState<number | null>(null)
  const [reviewNote, setReviewNote] = useState('')
  const [reviewBusy, setReviewBusy] = useState(false)

  const load = useCallback(() => {
    setList(null)
    const q = statusFilter ? `?status=${statusFilter}` : ''
    api<Suggestion[]>(`/suggestions${q}`).then(setList).catch((e) => {
      setError((e as Error).message)
      setList([])
    })
  }, [statusFilter])

  useEffect(load, [load])

  useEffect(() => {
    api<Vehicle[]>('/vehicles').then(setVehicles).catch(() => setVehicles([]))
    api<Plan[]>('/plans').then(setPlans).catch(() => setPlans([]))
  }, [])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (vehicleId === '' || !text.trim()) return
    setError('')
    setBusy(true)
    try {
      await api('/suggestions', {
        method: 'POST',
        body: { vehicle_id: vehicleId, plan_id: planId === '' ? null : planId, text: text.trim() },
      })
      setVehicleId('')
      setPlanId('')
      setText('')
      load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  function openReview(s: Suggestion) {
    setReviewId(s.id)
    setReviewNote(s.admin_note)
  }

  async function saveReview(status: Suggestion['status']) {
    if (reviewId === null) return
    setReviewBusy(true)
    setError('')
    try {
      await api(`/suggestions/${reviewId}`, {
        method: 'PATCH',
        body: { status, admin_note: reviewNote.trim() },
      })
      setReviewId(null)
      load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setReviewBusy(false)
    }
  }

  async function remove(s: Suggestion) {
    if (!confirm('Bu öneri kalıcı olarak silinsin mi?')) return
    setError('')
    try {
      await api(`/suggestions/${s.id}`, { method: 'DELETE' })
      load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const all = list ?? []

  return (
    <>
      <PageHeader
        title="Öneriler"
        subtitle={
          canReview
            ? 'Mühendis, usta ve amirlerden gelen tüm önerileri değerlendirin: onaylayın, reddedin veya Bakım Ağacı’na ekleyip işaretleyin.'
            : 'Sahada fark ettiğiniz bir eksikliği veya iyileştirmeyi bir araç ve isteğe bağlı bir bakım periyoduyla ilişkilendirip bildirin.'
        }
      />

      <div className="card">
        <div className="card-head"><div className="card-title">Yeni Öneri</div></div>
        <form className="row" onSubmit={submit} style={{ flexWrap: 'wrap', gap: 8, alignItems: 'flex-start' }}>
          <select value={vehicleId} onChange={(e) => setVehicleId(e.target.value ? Number(e.target.value) : '')} required style={{ minWidth: 140 }}>
            <option value="">Araç seçin…</option>
            {vehicles.map((v) => (
              <option key={v.id} value={v.id}>{v.code}</option>
            ))}
          </select>
          <select value={planId} onChange={(e) => setPlanId(e.target.value ? Number(e.target.value) : '')} style={{ minWidth: 160 }}>
            <option value="">Periyot (isteğe bağlı)</option>
            {plans.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Örn: Kapı sensöründe zaman zaman gecikme oluyor, kontrol listesine eklenmeli…"
            rows={2}
            required
            style={{ flex: '1 1 260px', minWidth: 220, fontSize: 13, padding: '8px 10px' }}
          />
          <button className="primary" disabled={busy || vehicleId === '' || !text.trim()}>
            <Send size={13} style={{ marginRight: 6, verticalAlign: -2 }} />
            Gönder
          </button>
        </form>
        {error && <div className="error">{error}</div>}
      </div>

      <div className="table-card fade-in">
        <div className="table-toolbar">
          <Lightbulb size={15} color="var(--text-tertiary)" />
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="">Tüm Durumlar</option>
            <option value="pending">Beklemede</option>
            <option value="approved">Onaylandı</option>
            <option value="added">Ağaca Eklendi</option>
            <option value="rejected">Reddedildi</option>
          </select>
          <span className="muted" style={{ marginLeft: 'auto' }}>{all.length} öneri</span>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Araç</th>
                <th>Periyot</th>
                <th>Öneri</th>
                <th>Öneren</th>
                <th>Tarih</th>
                <th>Durum</th>
                {canReview && <th>Değerlendirme</th>}
                <th></th>
              </tr>
            </thead>
            <tbody>
              {list === null && <SkeletonTableRows columns={canReview ? 8 : 7} />}
              {list !== null && all.map((s) => (
                <tr key={s.id}>
                  <td style={{ whiteSpace: 'nowrap' }}><span className="vehicle-code">{s.vehicle.code}</span></td>
                  <td style={{ whiteSpace: 'nowrap' }}>{s.plan?.name ?? <span className="muted">Genel</span>}</td>
                  <td style={{ minWidth: 220, maxWidth: 360 }}>
                    <TruncatedText text={s.text} />
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <div className="row" style={{ gap: 6 }}>
                      <Avatar name={s.created_by?.full_name ?? '?'} size={20} />
                      {s.created_by?.full_name ?? 'Bilinmiyor'}
                    </div>
                  </td>
                  <td style={{ whiteSpace: 'nowrap' }}>{fmt(s.created_at)}</td>
                  <td style={{ whiteSpace: 'nowrap' }}><Badge tone={STATUS_TONE[s.status]}>{SUGGESTION_STATUS_TR[s.status]}</Badge></td>
                  {canReview && (
                    <td style={{ fontSize: 12.5, minWidth: 160, maxWidth: 260, whiteSpace: 'normal', wordBreak: 'break-word' }}>
                      {s.admin_note ? (
                        <span className="row" style={{ gap: 4, alignItems: 'flex-start' }}>
                          <StickyNote size={12} style={{ marginTop: 2, flexShrink: 0 }} />
                          <TruncatedText text={s.admin_note} />
                        </span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                      {s.reviewed_by && (
                        <div className="muted" style={{ fontSize: 11 }}>
                          {s.reviewed_by.full_name} · {fmt(s.reviewed_at)}
                        </div>
                      )}
                    </td>
                  )}
                  <td>
                    <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                      {canReview && (
                        <button className="small" onClick={() => openReview(s)}>
                          Değerlendir
                        </button>
                      )}
                      {(canReview || s.created_by?.id === user.id) && (
                        <button className="small danger" onClick={() => remove(s)} title="Sil">
                          <Trash2 size={12} />
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {list !== null && all.length === 0 && (
            <EmptyState
              icon={<Lightbulb size={22} />}
              title="Öneri bulunamadı"
              description={canReview ? 'Henüz hiç öneri gönderilmemiş.' : 'Henüz bir öneri göndermediniz.'}
            />
          )}
        </div>
      </div>

      {reviewId !== null && (
        <div
          style={{
            position: 'fixed', inset: 0, background: 'rgba(15, 23, 42, 0.45)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 200,
          }}
          onClick={() => !reviewBusy && setReviewId(null)}
        >
          <div className="card" style={{ width: 420, maxWidth: '90vw' }} onClick={(e) => e.stopPropagation()}>
            <h3 style={{ marginTop: 0, fontSize: 15, display: 'flex', alignItems: 'center', gap: 6 }}>
              <Lightbulb size={15} /> Öneriyi Değerlendir
            </h3>
            <textarea
              autoFocus
              value={reviewNote}
              onChange={(e) => setReviewNote(e.target.value)}
              placeholder="Değerlendirme notu (isteğe bağlı) — ör. nasıl ele alındığı, red gerekçesi…"
              rows={3}
              style={{ width: '100%', fontSize: 13, padding: '8px 10px', marginBottom: 12 }}
            />
            <div className="row" style={{ gap: 8, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
              <button className="ghost" disabled={reviewBusy} onClick={() => setReviewId(null)}>Vazgeç</button>
              <button className="small danger" disabled={reviewBusy} onClick={() => saveReview('rejected')}>
                <X size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                Reddet
              </button>
              <button className="small" disabled={reviewBusy} onClick={() => saveReview('approved')}>
                <Check size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                Onayla
              </button>
              <button className="primary" disabled={reviewBusy} onClick={() => saveReview('added')}>
                <Workflow size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                Ağaca Eklendi Olarak İşaretle
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
