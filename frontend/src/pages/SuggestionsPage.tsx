import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { Plan, Suggestion, User } from '../types'
import { SUGGESTION_STATUS_TR } from '../types'
import { PageHeader, Badge, Avatar, EmptyState, SkeletonTableRows } from '../components/ui'
import { Lightbulb, Send, Check, X, Trash2, StickyNote, ShieldCheck } from 'lucide-react'

const fmt = (s: string | null) => (s ? new Date(s).toLocaleString('tr-TR') : '—')

const STATUS_TONE: Record<Suggestion['status'], 'pending' | 'in_progress' | 'completed' | 'danger'> = {
  pending: 'pending',
  pending_engineer: 'in_progress',
  approved: 'completed',
  added: 'completed',
  rejected: 'danger',
}

// Onay TEK aşamalı (amir -> mühendis zinciri kaldırıldı). Talep ya hâlâ karar
// bekliyordur ya da sonuçlanmıştır. 'pending_engineer' yalnızca eski
// kayıtlarda görülebilir; o da normal bir bekleyen talep gibi ele alınır
// (backend'deki services.suggestion_is_open ile birebir aynı mantık).
function isOpen(s: Suggestion): boolean {
  return s.status === 'pending' || s.status === 'pending_engineer'
}

// Talep metni uzun olabilir; tabloda satır satır alta aksın diye normal
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

// Talebin kim tarafından, ne zaman ve hangi notla sonuçlandırıldığını
// özetler. Zincir kalktığı için tek bir karar satırı gösterilir; eski (iki
// aşamalı dönemden kalan) kayıtlarda aşama alanları doluysa oradan okunur.
function ReviewSummary({ s }: { s: Suggestion }) {
  if (isOpen(s)) return <span className="muted" style={{ fontSize: 12.5 }}>Karar bekliyor</span>

  // Yeni kayıtlarda reviewed_by/at doludur; çok eski kayıtlarda boşsa
  // mühendis/amir aşama alanlarına düşülür.
  const who = s.reviewed_by ?? s.engineer_reviewed_by ?? s.amir_reviewed_by
  const when = s.reviewed_at ?? s.engineer_reviewed_at ?? s.amir_reviewed_at
  const note = s.admin_note || s.engineer_note || s.amir_note

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12.5 }}>
      <span>
        {SUGGESTION_STATUS_TR[s.status]}
        {who && ` — ${who.full_name}`}
        {when && ` · ${fmt(when)}`}
      </span>
      {note && (
        <div className="muted" style={{ fontSize: 11, display: 'flex', gap: 4, alignItems: 'flex-start' }}>
          <StickyNote size={11} style={{ marginTop: 1, flexShrink: 0 }} /><TruncatedText text={note} />
        </div>
      )}
    </div>
  )
}

export default function SuggestionsPage({ user }: { user: User }) {
  const [list, setList] = useState<Suggestion[] | null>(null)
  const [knownModels, setKnownModels] = useState<string[]>([])
  const [plans, setPlans] = useState<Plan[]>([])
  const [error, setError] = useState('')
  const [statusFilter, setStatusFilter] = useState('')

  // Yeni talep formu — belirli bir araç değil, araç MODELİ seçilir
  // (Bozankaya/Sirio vb. — o modeldeki tüm araçları ilgilendirir).
  const [vehicleModel, setVehicleModel] = useState('')
  const [planId, setPlanId] = useState<number | ''>('')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)

  // Değerlendirme: hangi talep için not/karar paneli açık.
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
    api<string[]>('/vehicle-models').then(setKnownModels).catch(() => setKnownModels([]))
    api<Plan[]>('/plans').then(setPlans).catch(() => setPlans([]))
  }, [])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!vehicleModel || !text.trim()) return
    setError('')
    setBusy(true)
    try {
      await api('/suggestions', {
        method: 'POST',
        body: { vehicle_model: vehicleModel, plan_id: planId === '' ? null : planId, text: text.trim() },
      })
      setVehicleModel('')
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
    setReviewNote('')
  }

  async function saveReview(status: 'approved' | 'rejected') {
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
    if (!confirm('Bu talep kalıcı olarak silinsin mi?')) return
    setError('')
    try {
      await api(`/suggestions/${s.id}`, { method: 'DELETE' })
      load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const all = list ?? []
  const reviewing = reviewId !== null ? all.find((s) => s.id === reviewId) ?? null : null
  // Faz 31 — tek aşamalı onay: mühendis, amir ve admin (backend'deki
  // services.can_review_suggestion ile aynı liste) bir talebi doğrudan
  // onaylar ya da reddeder; verilen ilk karar nihaidir.
  const canReview = user.role === 'admin' || user.role === 'engineer' || user.role === 'amir'
  const canDelete = (s: Suggestion) => canReview || s.created_by?.id === user.id

  return (
    <>
      <PageHeader
        title="İyileştirme Talepleri"
        subtitle="Herkesin talebi herkese açıktır. Mühendis, amir veya admin talebi onaylar ya da reddeder; verilen karar nihaidir."
      />

      <div className="card">
        <div className="card-head"><div className="card-title">Yeni Talep</div></div>
        <form className="row" onSubmit={submit} style={{ flexWrap: 'wrap', gap: 8, alignItems: 'flex-start' }}>
          <select
            value={vehicleModel}
            onChange={(e) => setVehicleModel(e.target.value)}
            required
            style={{ minWidth: 180 }}
          >
            <option value="">Araç modeli seçin…</option>
            {knownModels.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
          <select value={planId} onChange={(e) => setPlanId(e.target.value ? Number(e.target.value) : '')} style={{ minWidth: 160 }}>
            <option value="">Periyot / bakım (isteğe bağlı)</option>
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
          <button className="primary" disabled={busy || !vehicleModel || !text.trim()}>
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
            <option value="rejected">Reddedildi</option>
          </select>
          <span className="muted" style={{ marginLeft: 'auto' }}>{all.length} talep</span>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Araç Modeli</th>
                <th>Periyot</th>
                <th>Talep</th>
                <th>Öneren</th>
                <th>Tarih</th>
                <th>Durum</th>
                <th>Değerlendirme</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {list === null && <SkeletonTableRows columns={8} />}
              {list !== null && all.map((s) => (
                <tr key={s.id}>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {s.vehicle_model || (s.vehicle ? `${s.vehicle.code} (eski kayıt)` : <span className="muted">—</span>)}
                  </td>
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
                  <td style={{ minWidth: 200, maxWidth: 280 }}>
                    <ReviewSummary s={s} />
                  </td>
                  <td>
                    <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                      {canReview && isOpen(s) && (
                        <button className="small" onClick={() => openReview(s)}>
                          <ShieldCheck size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                          Değerlendir
                        </button>
                      )}
                      {canDelete(s) && (
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
              title="Talep bulunamadı"
              description="Henüz hiç iyileştirme talebi gönderilmemiş."
            />
          )}
        </div>
      </div>

      {reviewing && isOpen(reviewing) && (
        <div
          style={{
            position: 'fixed', inset: 0, background: 'rgba(15, 23, 42, 0.45)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 200,
          }}
          onClick={() => !reviewBusy && setReviewId(null)}
        >
          <div className="card" style={{ width: 420, maxWidth: '90vw' }} onClick={(e) => e.stopPropagation()}>
            <h3 style={{ marginTop: 0, fontSize: 15, display: 'flex', alignItems: 'center', gap: 6 }}>
              <Lightbulb size={15} />
              Talebi Değerlendir
            </h3>
            <p className="muted" style={{ fontSize: 12.5, marginTop: -4 }}>
              Verdiğiniz karar nihaidir — talep başka bir aşamaya düşmez.
            </p>
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
              <button className="primary" disabled={reviewBusy} onClick={() => saveReview('approved')}>
                <Check size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                Onayla
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
