import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import type { Plan, Suggestion, User } from '../types'
import { SUGGESTION_STATUS_TR } from '../types'
import { PageHeader, Badge, Avatar, EmptyState, SkeletonTableRows } from '../components/ui'
import { Lightbulb, Send, Check, X, Workflow, Trash2, StickyNote, ShieldCheck } from 'lucide-react'

const fmt = (s: string | null) => (s ? new Date(s).toLocaleString('tr-TR') : '—')

const STATUS_TONE: Record<Suggestion['status'], 'pending' | 'in_progress' | 'completed' | 'danger'> = {
  pending: 'pending',
  pending_engineer: 'in_progress',
  approved: 'completed',
  added: 'completed',
  rejected: 'danger',
}

// Faz 30 — hiyerarşik onay: bir önerinin şu an hangi aşamanın kararını
// beklediğini (backend'deki services.suggestion_pending_stage ile birebir
// aynı mantık) döner — null ise öneri zaten sonuçlanmış (amir reddetti ya
// da mühendis karar verdi), artık kimse değerlendiremez.
function pendingStage(s: Suggestion): 'amir' | 'engineer' | null {
  if (s.status === 'pending') return 'amir'
  if (s.status === 'pending_engineer') return 'engineer'
  return null
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

// Onay sürecinin iki aşamasını (amir/mühendis) tek bir hücrede özetler —
// henüz sırası gelmemiş aşama "(sırada değil)" ile gösterilir.
function ApprovalChain({ s }: { s: Suggestion }) {
  const amirLine = s.amir_status === 'pending'
    ? <span className="muted">Amir: bekliyor</span>
    : (
      <span>
        Amir: {s.amir_status === 'approved' ? 'Onayladı' : 'Reddetti'}
        {s.amir_reviewed_by && ` — ${s.amir_reviewed_by.full_name}`}
        {s.amir_reviewed_at && ` · ${fmt(s.amir_reviewed_at)}`}
      </span>
    )
  const engineerLine = s.amir_status !== 'approved'
    ? <span className="muted">Mühendis: sırada değil</span>
    : s.engineer_status === 'pending'
      ? <span className="muted">Mühendis: bekliyor</span>
      : (
        <span>
          Mühendis: {SUGGESTION_STATUS_TR[s.engineer_status === 'added' ? 'added' : s.engineer_status === 'approved' ? 'approved' : 'rejected']}
          {s.engineer_reviewed_by && ` — ${s.engineer_reviewed_by.full_name}`}
          {s.engineer_reviewed_at && ` · ${fmt(s.engineer_reviewed_at)}`}
        </span>
      )
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12.5 }}>
      <div>{amirLine}</div>
      {s.amir_note && (
        <div className="muted" style={{ fontSize: 11, display: 'flex', gap: 4, alignItems: 'flex-start' }}>
          <StickyNote size={11} style={{ marginTop: 1, flexShrink: 0 }} /><TruncatedText text={s.amir_note} />
        </div>
      )}
      <div>{engineerLine}</div>
      {s.engineer_note && (
        <div className="muted" style={{ fontSize: 11, display: 'flex', gap: 4, alignItems: 'flex-start' }}>
          <StickyNote size={11} style={{ marginTop: 1, flexShrink: 0 }} /><TruncatedText text={s.engineer_note} />
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

  // Yeni öneri formu — Faz 30: belirli bir araç değil, araç MODELİ seçilir
  // (Bozankaya/Sirio vb. — o modeldeki tüm araçları ilgilendirir).
  const [vehicleModel, setVehicleModel] = useState('')
  const [planId, setPlanId] = useState<number | ''>('')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)

  // Değerlendirme: hangi öneri için not/karar paneli açık.
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

  async function saveReview(status: 'approved' | 'rejected' | 'added') {
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
  const reviewing = reviewId !== null ? all.find((s) => s.id === reviewId) ?? null : null
  const reviewingStage = reviewing ? pendingStage(reviewing) : null
  // Bu kullanıcı, önerinin İÇİNDE BULUNDUĞU aşamada karar verebilir mi?
  // (backend'deki review_suggestion ile aynı kural: rol aşamayla eşleşmeli
  // ya da admin olmalı.)
  const canActOnStage = (stage: 'amir' | 'engineer' | null) =>
    stage !== null && (user.role === stage || user.role === 'admin')
  const canDelete = (s: Suggestion) =>
    user.role === 'admin' || user.role === 'amir' || user.role === 'engineer' || s.created_by?.id === user.id

  return (
    <>
      <PageHeader
        title="Öneriler"
        subtitle="Öneri önce amire düşer; amir onaylarsa mühendise geçer ve mühendisin kararı nihaidir. Amir reddederse öneri mühendise hiç düşmez."
      />

      <div className="card">
        <div className="card-head"><div className="card-title">Yeni Öneri</div></div>
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
            <option value="pending">Beklemede (Amir)</option>
            <option value="pending_engineer">Beklemede (Mühendis)</option>
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
                <th>Araç Modeli</th>
                <th>Periyot</th>
                <th>Öneri</th>
                <th>Öneren</th>
                <th>Tarih</th>
                <th>Durum</th>
                <th>Onay Süreci</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {list === null && <SkeletonTableRows columns={8} />}
              {list !== null && all.map((s) => {
                const stage = pendingStage(s)
                return (
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
                    <ApprovalChain s={s} />
                  </td>
                  <td>
                    <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                      {canActOnStage(stage) && (
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
                )
              })}
            </tbody>
          </table>
          {list !== null && all.length === 0 && (
            <EmptyState
              icon={<Lightbulb size={22} />}
              title="Öneri bulunamadı"
              description="Henüz hiç öneri gönderilmemiş."
            />
          )}
        </div>
      </div>

      {reviewing && reviewingStage && (
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
              {reviewingStage === 'amir' ? 'Amir Değerlendirmesi' : 'Mühendis Değerlendirmesi'}
            </h3>
            <p className="muted" style={{ fontSize: 12.5, marginTop: -4 }}>
              {reviewingStage === 'amir'
                ? 'Onaylarsanız öneri mühendise düşer; reddederseniz mühendise hiç düşmez.'
                : 'Bu aşamadaki karar nihaidir.'}
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
              <button className="small" disabled={reviewBusy} onClick={() => saveReview('approved')}>
                <Check size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                Onayla
              </button>
              {reviewingStage === 'engineer' && (
                <button className="primary" disabled={reviewBusy} onClick={() => saveReview('added')}>
                  <Workflow size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                  Ağaca Eklendi Olarak İşaretle
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  )
}
