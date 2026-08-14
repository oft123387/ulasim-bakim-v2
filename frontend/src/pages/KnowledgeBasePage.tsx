import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, apiUploadWithFields } from '../api'
import { PageHeader, EmptyState, SearchInput } from '../components/ui'
import type { KnowledgeHit, NodeAttachment, Plan, TreeNode } from '../types'
import {
  BookOpen,
  ChevronRight,
  ChevronDown,
  FileText,
  ImageIcon,
  LinkIcon,
  Download,
  Video,
  Box,
  Trash2,
  Pencil,
  PlusCircle,
  X,
  ArrowLeft,
  Search,
} from 'lucide-react'

/* =============================================================================
 * Bakım Bilgi Bankası (Faz 27)
 *
 * Bakım Ağacı EDİTÖRÜ (TreeEditorPage) yalnızca yetkili rollere açıktır ve
 * amacı ağacın yapısını (madde ekle/sil/taşı) yönetmektir. Bu sayfa ise aynı
 * ağacın SALT-OKUNUR, "bunu nasıl yaparım" odaklı görünümüdür: usta bir
 * maddeye girince o maddenin açıklaması + eğitim videosu / PDF talimatı /
 * referans fotoğrafı tek ekranda karşısına çıkar.
 *
 * Bakım sırasındaki madde tikleme akışı (MaintenancesPage) DEĞİŞMEDİ — orada
 * ekler eskisi gibi satır içinde görünmeye devam eder; ek olarak her maddeye
 * bu sayfayı açan bir "Bilgi" düğmesi eklendi.
 *
 * İçerik ayrı bir tabloda tutulmaz: açıklama TreeNode.note, materyaller
 * NodeAttachment'tır — yani Bakım Ağacı'na girilmiş bilgi burada okunur.
 * ========================================================================== */

function attIcon(kind: string) {
  if (kind === 'image') return <ImageIcon size={14} />
  if (kind === 'pdf') return <FileText size={14} />
  if (kind === 'video') return <Video size={14} />
  if (kind === 'cad') return <Box size={14} />
  if (kind === 'link') return <LinkIcon size={14} />
  return <Download size={14} />
}

const KIND_LABEL: Record<string, string> = {
  image: 'Görsel',
  pdf: 'PDF Talimat',
  video: 'Video',
  cad: 'Teknik Çizim',
  link: 'Bağlantı',
  file: 'Dosya',
}

/** YouTube/Vimeo bağlantılarını gömülebilir (embed) forma çevirir; gömülemiyorsa
 * null döner ve bağlantı normal bir dış link olarak gösterilir. */
function embedUrl(url: string): string | null {
  try {
    const u = new URL(url)
    if (u.hostname.includes('youtu.be')) {
      const id = u.pathname.replace('/', '')
      return id ? `https://www.youtube.com/embed/${id}` : null
    }
    if (u.hostname.includes('youtube.com')) {
      const id = u.searchParams.get('v')
      if (id) return `https://www.youtube.com/embed/${id}`
      if (u.pathname.startsWith('/embed/')) return url
    }
    if (u.hostname.includes('vimeo.com')) {
      const id = u.pathname.split('/').filter(Boolean)[0]
      if (id && /^\d+$/.test(id)) return `https://player.vimeo.com/video/${id}`
    }
  } catch {
    /* geçersiz URL — normal link olarak gösterilir */
  }
  return null
}

/** Bir ek video mu? (dosya olarak yüklenmiş video ya da tanınan video bağlantısı) */
function isVideoAtt(att: NodeAttachment): boolean {
  return att.kind === 'video' || (att.kind === 'link' && !!embedUrl(att.url))
}

// ------------------------------------------------------------ Ek görüntüleyici
function AttachmentViewer({ att, onDelete }: { att: NodeAttachment; onDelete?: () => void }) {
  const fileHref = att.filename ? `/uploads/attachments/${att.filename}` : ''
  const label = att.label || att.original_name || att.url || 'Ek'
  const embed = att.url ? embedUrl(att.url) : null

  let body
  if (att.kind === 'image') {
    body = (
      <a href={fileHref} target="_blank" rel="noreferrer" style={{ display: 'block' }}>
        <img
          src={fileHref}
          alt={label}
          style={{ width: '100%', borderRadius: 8, border: '1px solid var(--border)' }}
        />
      </a>
    )
  } else if (att.kind === 'video' && att.filename) {
    body = (
      // eslint-disable-next-line jsx-a11y/media-has-caption
      <video controls preload="metadata" style={{ width: '100%', borderRadius: 8, background: '#000' }} src={fileHref} />
    )
  } else if (embed) {
    body = (
      <iframe
        title={label}
        src={embed}
        allowFullScreen
        style={{ width: '100%', aspectRatio: '16/9', border: 'none', borderRadius: 8 }}
      />
    )
  } else if (att.kind === 'pdf') {
    body = (
      <iframe
        title={label}
        src={fileHref}
        style={{ width: '100%', height: 460, border: '1px solid var(--border)', borderRadius: 8 }}
      />
    )
  } else {
    body = (
      <a
        href={att.url || fileHref}
        target="_blank"
        rel="noreferrer"
        className="attach-chip"
        style={{ display: 'inline-flex' }}
      >
        {attIcon(att.kind)}
        <span>{label}</span>
      </a>
    )
  }

  return (
    <div className="kb-att">
      <div className="kb-att-head">
        <span className="kb-att-title">
          {attIcon(att.kind)}
          <span>{label}</span>
        </span>
        <span className="row" style={{ gap: 6, alignItems: 'center' }}>
          <span className="kb-att-kind">{KIND_LABEL[att.kind] ?? att.kind}</span>
          {onDelete && (
            <button className="small ghost" title="Bu materyali sil" onClick={onDelete}>
              <Trash2 size={12} />
            </button>
          )}
        </span>
      </div>
      {body}
    </div>
  )
}

// ------------------------------------------------------------ Ağaç satırı
function TreeRow({
  node,
  depth,
  selectedId,
  expanded,
  onToggle,
  onSelect,
}: {
  node: TreeNode
  depth: number
  selectedId: number | null
  expanded: Set<number>
  onToggle: (id: number) => void
  onSelect: (node: TreeNode) => void
}) {
  const hasKids = node.children.length > 0
  const open = expanded.has(node.id)
  // Bilgi bankasında "içerik var mı" göstergesi: açıklama metni ya da ek.
  const hasInfo = !!(node.note && node.note.trim()) || node.attachments.length > 0
  const hasVideo = node.attachments.some(isVideoAtt)

  return (
    <>
      <div
        className={`kb-tree-row${selectedId === node.id ? ' active' : ''}`}
        style={{ paddingLeft: 8 + depth * 15 }}
        onClick={() => {
          onSelect(node)
          if (hasKids && !open) onToggle(node.id)
        }}
      >
        <button
          className="kb-tree-caret"
          disabled={!hasKids}
          onClick={(e) => {
            e.stopPropagation()
            if (hasKids) onToggle(node.id)
          }}
          aria-label={open ? 'Kapat' : 'Aç'}
        >
          {hasKids ? (open ? <ChevronDown size={14} /> : <ChevronRight size={14} />) : <span />}
        </button>
        <span className="kb-tree-name">{node.name}</span>
        {node.card_code && <span className="card-code-pill">{node.card_code}</span>}
        {hasVideo && (
          <span className="kb-dot kb-dot-video" title="Video var">
            <Video size={11} />
          </span>
        )}
        {!hasVideo && hasInfo && <span className="kb-dot" title="Bilgi var" />}
      </div>
      {open &&
        node.children.map((c) => (
          <TreeRow
            key={c.id}
            node={c}
            depth={depth + 1}
            selectedId={selectedId}
            expanded={expanded}
            onToggle={onToggle}
            onSelect={onSelect}
          />
        ))}
    </>
  )
}

export default function KnowledgeBasePage({
  canEdit,
  initialNodeId,
}: {
  /** admin / mühendis / amir — açıklama yazabilir, materyal ekleyip silebilir. */
  canEdit: boolean
  /** Bakım detayındaki "Bilgi" düğmesinden gelindiyse doğrudan açılacak madde. */
  initialNodeId?: number | null
}) {
  const [plans, setPlans] = useState<Plan[]>([])
  const [planId, setPlanId] = useState<number | ''>('')
  const [roots, setRoots] = useState<TreeNode[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const [selected, setSelected] = useState<TreeNode | null>(null)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<KnowledgeHit[] | null>(null)
  const [searching, setSearching] = useState(false)

  // Düzenleme (yalnızca canEdit)
  const [noteDraft, setNoteDraft] = useState<string | null>(null)
  const [noteBusy, setNoteBusy] = useState(false)
  const [linkOpen, setLinkOpen] = useState(false)
  const [linkLabel, setLinkLabel] = useState('')
  const [linkUrl, setLinkUrl] = useState('')
  const [uploadBusy, setUploadBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  // Mobilde tek ekrana iki panel sığmadığı için: liste ⇄ detay geçişi.
  const [mobileDetail, setMobileDetail] = useState(false)

  useEffect(() => {
    api<Plan[]>('/plans?include_catalog_only=true')
      .then((ps) => {
        setPlans(ps)
        setPlanId((cur) => (cur === '' && ps.length > 0 ? ps[0].id : cur))
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Periyotlar yüklenemedi'))
  }, [])

  const loadTree = useCallback(() => {
    if (planId === '') {
      setRoots([])
      setLoading(false)
      return
    }
    setLoading(true)
    api<TreeNode[]>(`/tree?plan_id=${planId}`)
      .then((rs) => {
        setRoots(rs)
        setError('')
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Bakım ağacı yüklenemedi'))
      .finally(() => setLoading(false))
  }, [planId])

  useEffect(() => {
    loadTree()
  }, [loadTree])

  // Ağaç yeniden yüklendiğinde seçili maddenin güncel hâlini yakala (ek
  // eklendikten sonra sağ panelin tazelenmesi için).
  useEffect(() => {
    if (!selected) return
    const found = findNode(roots, selected.id)
    if (found && found !== selected) setSelected(found)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roots])

  // Deep-link: bakım detayındaki "Bilgi" düğmesinden gelinen madde.
  const jumpedRef = useRef<number | null>(null)
  useEffect(() => {
    if (!initialNodeId || jumpedRef.current === initialNodeId) return
    jumpedRef.current = initialNodeId
    openNodeById(initialNodeId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialNodeId, plans])

  // Arama — 300 ms debounce, 2 karakterden kısa sorguda kapalı.
  useEffect(() => {
    const term = query.trim()
    if (term.length < 2) {
      setHits(null)
      setSearching(false)
      return
    }
    setSearching(true)
    const t = setTimeout(() => {
      api<KnowledgeHit[]>(`/knowledge/search?q=${encodeURIComponent(term)}`)
        .then(setHits)
        .catch(() => setHits([]))
        .finally(() => setSearching(false))
    }, 300)
    return () => clearTimeout(t)
  }, [query])

  function findNode(list: TreeNode[], id: number): TreeNode | null {
    for (const n of list) {
      if (n.id === id) return n
      const f = findNode(n.children, id)
      if (f) return f
    }
    return null
  }

  /** Bir maddeyi (hangi periyotta olduğunu bilmesek de) bulup açar: önce
   * yüklü ağaçta arar, bulamazsa maddenin periyodunu arama ucundan öğrenip
   * o periyodun ağacını yükler. */
  async function openNodeById(nodeId: number) {
    const local = findNode(roots, nodeId)
    if (local) {
      selectNode(local)
      return
    }
    // Ağaçlar periyot bazlı yüklendiği için, madde başka bir periyotta
    // olabilir: tüm periyotları sırayla tarayıp maddeyi içereni seçeriz.
    for (const p of plans) {
      try {
        const rs = await api<TreeNode[]>(`/tree?plan_id=${p.id}`)
        const found = findNode(rs, nodeId)
        if (found) {
          setPlanId(p.id)
          setRoots(rs)
          selectNode(found, rs)
          return
        }
      } catch {
        /* bu periyot okunamadı, diğerine geç */
      }
    }
  }

  /** Maddeyi seçer ve ağaçta ona giden tüm üst düğümleri açar. */
  function selectNode(node: TreeNode, tree: TreeNode[] = roots) {
    setSelected(node)
    setNoteDraft(null)
    setMobileDetail(true)
    const chain: number[] = []
    const walk = (list: TreeNode[], trail: number[]): boolean => {
      for (const n of list) {
        if (n.id === node.id) {
          chain.push(...trail)
          return true
        }
        if (walk(n.children, [...trail, n.id])) return true
      }
      return false
    }
    walk(tree, [])
    setExpanded((prev) => new Set([...prev, ...chain, node.id]))
  }

  function toggle(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  async function saveNote() {
    if (!selected || noteDraft === null) return
    setNoteBusy(true)
    try {
      await api(`/tree/${selected.id}/kb-note`, { method: 'PATCH', body: { note: noteDraft } })
      setNoteDraft(null)
      loadTree()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Açıklama kaydedilemedi')
    } finally {
      setNoteBusy(false)
    }
  }

  async function uploadFile(file: File) {
    if (!selected) return
    setUploadBusy(true)
    try {
      await apiUploadWithFields(`/tree/${selected.id}/attachments`, file, { label: '' })
      loadTree()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Dosya yüklenemedi')
    } finally {
      setUploadBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  async function addLink() {
    if (!selected || !linkUrl.trim()) return
    setUploadBusy(true)
    try {
      await api(`/tree/${selected.id}/attachment-link`, {
        method: 'POST',
        body: { label: linkLabel.trim(), url: linkUrl.trim() },
      })
      setLinkOpen(false)
      setLinkLabel('')
      setLinkUrl('')
      loadTree()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Bağlantı eklenemedi')
    } finally {
      setUploadBusy(false)
    }
  }

  async function deleteAtt(id: number) {
    if (!confirm('Bu materyal silinsin mi?')) return
    try {
      await api(`/attachments/${id}`, { method: 'DELETE' })
      loadTree()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Materyal silinemedi')
    }
  }

  const videos = useMemo(() => (selected?.attachments ?? []).filter(isVideoAtt), [selected])
  const others = useMemo(() => (selected?.attachments ?? []).filter((a) => !isVideoAtt(a)), [selected])

  return (
    <>
      <PageHeader
        title="Bakım Bilgi Bankası"
        subtitle="Bir bakım maddesine girip nasıl yapıldığını izleyin: eğitim videosu, talimat PDF'i, referans görseli ve açıklama."
      />

      {error && <div className="error">{error}</div>}

      <div className="card fade-in" style={{ marginBottom: 14 }}>
        <div className="row" style={{ gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="Madde, kart kodu veya açıklamada ara…"
          />
          <select value={planId} onChange={(e) => setPlanId(e.target.value ? Number(e.target.value) : '')}>
            {plans.length === 0 && <option value="">Periyot yok</option>}
            {plans.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
                {p.vehicle_model ? ` · ${p.vehicle_model}` : ''}
              </option>
            ))}
          </select>
          {query.trim().length >= 2 && (
            <button className="small ghost" onClick={() => setQuery('')}>
              <X size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
              Aramayı Temizle
            </button>
          )}
        </div>

        {query.trim().length >= 2 && (
          <div className="kb-hits">
            {searching && <div className="muted" style={{ fontSize: 13 }}>Aranıyor…</div>}
            {!searching && hits && hits.length === 0 && (
              <div className="muted" style={{ fontSize: 13 }}>
                <Search size={13} style={{ marginRight: 6, verticalAlign: -2 }} />
                "{query.trim()}" için sonuç bulunamadı.
              </div>
            )}
            {!searching &&
              hits?.map((h) => (
                <button key={h.id} className="kb-hit" onClick={() => openNodeById(h.id)}>
                  <span className="kb-hit-name">
                    {h.name}
                    {h.card_code && <span className="card-code-pill">{h.card_code}</span>}
                    {h.has_video && (
                      <span className="kb-dot kb-dot-video" title="Video var">
                        <Video size={11} />
                      </span>
                    )}
                  </span>
                  <span className="kb-hit-path">
                    {h.plan_name ? `${h.plan_name} · ` : ''}
                    {h.path.join(' › ')}
                  </span>
                </button>
              ))}
          </div>
        )}
      </div>

      <div className={`kb-layout${mobileDetail ? ' show-detail' : ''}`}>
        <div className="card kb-tree-pane fade-in">
          <div className="card-head">
            <div>
              <div className="card-title">Bakım Ağacı</div>
              <div className="card-subtitle">Bilgi almak istediğiniz maddeye tıklayın.</div>
            </div>
          </div>
          {loading ? (
            <div className="muted" style={{ fontSize: 13 }}>Yükleniyor…</div>
          ) : roots.length === 0 ? (
            <div className="muted" style={{ fontSize: 13 }}>Bu periyot için tanımlı bir bakım ağacı yok.</div>
          ) : (
            <div className="kb-tree">
              {roots.map((r) => (
                <TreeRow
                  key={r.id}
                  node={r}
                  depth={0}
                  selectedId={selected?.id ?? null}
                  expanded={expanded}
                  onToggle={toggle}
                  onSelect={(n) => selectNode(n)}
                />
              ))}
            </div>
          )}
        </div>

        <div className="card kb-detail-pane fade-in">
          {!selected ? (
            <EmptyState
              icon={<BookOpen size={22} />}
              title="Bir madde seçin"
              description="Soldaki bakım ağacından bir maddeye tıklayın; o maddenin videosu, talimatı ve açıklaması burada açılır."
            />
          ) : (
            <>
              <button className="small ghost kb-back" onClick={() => setMobileDetail(false)}>
                <ArrowLeft size={13} style={{ marginRight: 5, verticalAlign: -2 }} />
                Ağaca dön
              </button>
              <div className="card-head">
                <div>
                  <div className="card-title">{selected.name}</div>
                  <div className="card-subtitle">
                    {selected.card_code ? `Kart kodu: ${selected.card_code}` : 'Bakım maddesi bilgi kartı'}
                  </div>
                </div>
              </div>

              {/* --- Açıklama / talimat metni --- */}
              <section className="kb-section">
                <div className="kb-section-head">
                  <h4>Nasıl Yapılır</h4>
                  {canEdit && noteDraft === null && (
                    <button className="small ghost" onClick={() => setNoteDraft(selected.note ?? '')}>
                      <Pencil size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                      Düzenle
                    </button>
                  )}
                </div>
                {noteDraft !== null ? (
                  <>
                    <textarea
                      rows={7}
                      style={{ width: '100%' }}
                      value={noteDraft}
                      onChange={(e) => setNoteDraft(e.target.value)}
                      placeholder="Bu madde nasıl yapılır? Adım adım anlatın; usta bakım sırasında bunu görecek."
                    />
                    <div className="row" style={{ gap: 8, marginTop: 8 }}>
                      <button className="small primary" disabled={noteBusy} onClick={saveNote}>
                        {noteBusy ? 'Kaydediliyor…' : 'Kaydet'}
                      </button>
                      <button className="small ghost" disabled={noteBusy} onClick={() => setNoteDraft(null)}>
                        Vazgeç
                      </button>
                    </div>
                  </>
                ) : selected.note && selected.note.trim() ? (
                  <p className="kb-note">{selected.note}</p>
                ) : (
                  <p className="muted" style={{ fontSize: 13 }}>
                    Bu madde için henüz açıklama girilmemiş.
                  </p>
                )}
              </section>

              {/* --- Videolar --- */}
              <section className="kb-section">
                <div className="kb-section-head">
                  <h4>
                    <Video size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
                    Eğitim Videoları
                  </h4>
                </div>
                {videos.length === 0 ? (
                  <p className="muted" style={{ fontSize: 13 }}>Bu madde için video eklenmemiş.</p>
                ) : (
                  <div className="kb-att-grid">
                    {videos.map((a) => (
                      <AttachmentViewer key={a.id} att={a} onDelete={canEdit ? () => deleteAtt(a.id) : undefined} />
                    ))}
                  </div>
                )}
              </section>

              {/* --- Diğer materyaller --- */}
              <section className="kb-section">
                <div className="kb-section-head">
                  <h4>
                    <FileText size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
                    Talimat ve Görseller
                  </h4>
                </div>
                {others.length === 0 ? (
                  <p className="muted" style={{ fontSize: 13 }}>Bu madde için materyal eklenmemiş.</p>
                ) : (
                  <div className="kb-att-grid">
                    {others.map((a) => (
                      <AttachmentViewer key={a.id} att={a} onDelete={canEdit ? () => deleteAtt(a.id) : undefined} />
                    ))}
                  </div>
                )}
              </section>

              {/* --- İçerik ekleme (admin / mühendis / amir) --- */}
              {canEdit && (
                <section className="kb-section kb-editor">
                  <div className="kb-section-head">
                    <h4>İçerik Ekle</h4>
                  </div>
                  <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
                    <button className="small" disabled={uploadBusy} onClick={() => fileRef.current?.click()}>
                      <PlusCircle size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                      {uploadBusy ? 'Yükleniyor…' : 'Dosya Ekle (video / PDF / görsel)'}
                    </button>
                    <button className="small ghost" disabled={uploadBusy} onClick={() => setLinkOpen((v) => !v)}>
                      <LinkIcon size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                      Video Bağlantısı Ekle
                    </button>
                    <input
                      ref={fileRef}
                      type="file"
                      style={{ display: 'none' }}
                      onChange={(e) => {
                        const f = e.target.files?.[0]
                        if (f) uploadFile(f)
                      }}
                    />
                  </div>
                  {linkOpen && (
                    <div className="row" style={{ gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
                      <input
                        placeholder="Başlık (ör. Balata değişimi)"
                        value={linkLabel}
                        onChange={(e) => setLinkLabel(e.target.value)}
                      />
                      <input
                        style={{ minWidth: 260, flex: 1 }}
                        placeholder="https://www.youtube.com/watch?v=…"
                        value={linkUrl}
                        onChange={(e) => setLinkUrl(e.target.value)}
                      />
                      <button className="small primary" disabled={uploadBusy || !linkUrl.trim()} onClick={addLink}>
                        Ekle
                      </button>
                    </div>
                  )}
                </section>
              )}
            </>
          )}
        </div>
      </div>
    </>
  )
}
