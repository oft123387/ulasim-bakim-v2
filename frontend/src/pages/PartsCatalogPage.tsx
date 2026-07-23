import { useEffect, useState } from 'react'
import { api, apiUploadWithFields } from '../api'
import { PageHeader, EmptyState } from '../components/ui'
import type { PartCatalogAttachment, PartCatalogItem } from '../types'
import {
  ArrowLeft,
  FileText,
  ImageIcon,
  LinkIcon,
  Download,
  Trash2,
  PlusCircle,
  Video,
  PackageSearch,
  Pencil,
  X,
  GripVertical,
  ChevronRight,
  Boxes,
  Box,
  QrCode,
} from 'lucide-react'

/** Parça ağacında (children iç içe) id'ye göre bir parçayı bulur — seçili
 * parça en üst seviyede olmayabilir (bir alt parça açılmış olabilir). */
function findPartById(items: PartCatalogItem[], id: number): PartCatalogItem | null {
  for (const it of items) {
    if (it.id === id) return it
    const found = findPartById(it.children, id)
    if (found) return found
  }
  return null
}

function fmt(d: string | null | undefined): string {
  if (!d) return '—'
  return new Date(d).toLocaleString('tr-TR', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function attIcon(kind: string) {
  if (kind === 'image') return <ImageIcon size={14} />
  if (kind === 'pdf') return <FileText size={14} />
  if (kind === 'video') return <Video size={14} />
  if (kind === 'cad') return <Box size={14} />
  if (kind === 'link') return <LinkIcon size={14} />
  return <Download size={14} />
}

/** YouTube linklerini gömülebilir (embed) forma çevirir; değilse null döner. */
function youtubeEmbedUrl(url: string): string | null {
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
  } catch {
    /* geçersiz URL */
  }
  return null
}

// ---------------------------------------------------------- Ek görüntüleyici
function AttachmentViewer({ att }: { att: PartCatalogAttachment }) {
  const fileHref = att.filename ? `/uploads/parts/${att.filename}` : ''
  const label = att.label || att.original_name || att.url || 'Ek'

  if (att.kind === 'image') {
    return (
      <a href={fileHref} target="_blank" rel="noreferrer" style={{ display: 'block' }}>
        <img src={fileHref} alt={label} style={{ width: '100%', borderRadius: 8, border: '1px solid var(--border)' }} />
      </a>
    )
  }
  if (att.kind === 'video') {
    return (
      // eslint-disable-next-line jsx-a11y/media-has-caption
      <video controls style={{ width: '100%', borderRadius: 8, background: '#000' }} src={fileHref} />
    )
  }
  if (att.kind === 'pdf') {
    return (
      <iframe
        title={label}
        src={fileHref}
        style={{ width: '100%', height: 420, border: '1px solid var(--border)', borderRadius: 8 }}
      />
    )
  }
  if (att.kind === 'link') {
    const embed = youtubeEmbedUrl(att.url)
    if (embed) {
      return (
        <iframe
          title={label}
          src={embed}
          allowFullScreen
          style={{ width: '100%', aspectRatio: '16/9', border: 'none', borderRadius: 8 }}
        />
      )
    }
  }
  return (
    <a href={att.kind === 'link' ? att.url : fileHref} target="_blank" rel="noreferrer" className="attach-chip">
      {attIcon(att.kind)}
      <span>{label}</span>
    </a>
  )
}

// ---------------------------------------------------------- Alt parçalar (sürükle-bırak sıralama)
function SubPartsSection({
  part,
  canEdit,
  onOpenPart,
  onChanged,
  onAddSubPart,
}: {
  part: PartCatalogItem
  canEdit: boolean
  onOpenPart: (id: number) => void
  onChanged: () => void
  onAddSubPart: () => void
}) {
  const [order, setOrder] = useState<PartCatalogItem[]>(part.children)
  const [dragId, setDragId] = useState<number | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setOrder(part.children)
  }, [part.children])

  async function persistOrder(next: PartCatalogItem[]) {
    setOrder(next)
    setSaving(true)
    try {
      await api('/parts/reorder', {
        method: 'PATCH',
        body: { parent_id: part.id, ordered_ids: next.map((p) => p.id) },
      })
      onChanged()
    } catch {
      // sessizce yut — sıra bir sonraki yüklemede zaten doğrusuna döner
    } finally {
      setSaving(false)
    }
  }

  function handleDrop(targetId: number) {
    if (dragId === null || dragId === targetId) return
    const next = [...order]
    const fromIdx = next.findIndex((p) => p.id === dragId)
    const toIdx = next.findIndex((p) => p.id === targetId)
    if (fromIdx === -1 || toIdx === -1) return
    const [moved] = next.splice(fromIdx, 1)
    next.splice(toIdx, 0, moved)
    setDragId(null)
    persistOrder(next)
  }

  if (order.length === 0 && !canEdit) return null

  return (
    <div className="card">
      <div className="row" style={{ marginBottom: 10 }}>
        <h3 style={{ margin: 0, fontSize: 15, display: 'flex', alignItems: 'center', gap: 6 }}>
          <Boxes size={15} /> Alt Parçalar
        </h3>
        {canEdit && (
          <button className="small" style={{ marginLeft: 'auto' }} onClick={onAddSubPart}>
            <PlusCircle size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
            Alt Parça Ekle
          </button>
        )}
      </div>
      {order.length === 0 ? (
        <div className="muted" style={{ fontSize: 13 }}>
          Henüz alt parça eklenmemiş — {part.name} içindeki bileşenleri (ör. motor, fren sistemi) ayrı
          birer alt parça olarak ekleyip burada listeleyebilirsiniz.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {order.map((child) => (
            <div
              key={child.id}
              draggable={canEdit}
              onDragStart={() => setDragId(child.id)}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => handleDrop(child.id)}
              onDragEnd={() => setDragId(null)}
              className="row"
              style={{
                gap: 10, padding: '8px 10px', border: '1px solid var(--border)', borderRadius: 8,
                cursor: canEdit ? 'grab' : 'pointer', opacity: dragId === child.id ? 0.5 : 1,
                background: 'var(--bg-soft, rgba(0,0,0,0.02))',
              }}
              onClick={() => onOpenPart(child.id)}
            >
              {canEdit && <GripVertical size={14} color="var(--text-tertiary)" />}
              {child.image_filename ? (
                <img
                  src={`/uploads/parts/${child.image_filename}`}
                  alt={child.name}
                  style={{ width: 32, height: 32, objectFit: 'contain', borderRadius: 6, background: '#fff' }}
                />
              ) : (
                <PackageSearch size={16} color="var(--text-tertiary)" />
              )}
              <span style={{ fontWeight: 600, fontSize: 13.5 }}>{child.name}</span>
              {child.children.length > 0 && (
                <span className="muted" style={{ fontSize: 11.5 }}>{child.children.length} alt parça</span>
              )}
              <ChevronRight size={14} style={{ marginLeft: 'auto' }} color="var(--text-tertiary)" />
            </div>
          ))}
        </div>
      )}
      {saving && <div className="muted" style={{ fontSize: 11.5, marginTop: 8 }}>Sıra kaydediliyor…</div>}
    </div>
  )
}

// ---------------------------------------------------------- Parça detayı
function PartDetail({
  part,
  canEdit,
  parentName,
  onBack,
  onOpenPart,
  onChanged,
  onDeleted,
  onAddSubPart,
}: {
  part: PartCatalogItem
  canEdit: boolean
  // Bu parça bir alt parçaysa üst parçanın adı (üstte breadcrumb için).
  parentName: string | null
  onBack: () => void
  onOpenPart: (id: number) => void
  onChanged: () => void
  onDeleted: () => void
  onAddSubPart: () => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [fileLabel, setFileLabel] = useState('')
  const [linkLabel, setLinkLabel] = useState('')
  const [linkUrl, setLinkUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [editingName, setEditingName] = useState(false)
  const [name, setName] = useState(part.name)
  const [description, setDescription] = useState(part.description)
  const [removeBg, setRemoveBg] = useState(true)

  useEffect(() => {
    setName(part.name)
    setDescription(part.description)
  }, [part.id, part.name, part.description])

  async function uploadFile(e: React.FormEvent) {
    e.preventDefault()
    if (!file) return
    setBusy(true)
    setError('')
    try {
      await apiUploadWithFields(`/parts/${part.id}/attachments`, file, { label: fileLabel })
      setFile(null)
      setFileLabel('')
      const input = document.getElementById(`part-att-file-${part.id}`) as HTMLInputElement | null
      if (input) input.value = ''
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function addLink(e: React.FormEvent) {
    e.preventDefault()
    if (!linkUrl.trim()) return
    setBusy(true)
    setError('')
    try {
      await api(`/parts/${part.id}/attachment-link`, {
        method: 'POST',
        body: { label: linkLabel, url: linkUrl.trim() },
      })
      setLinkLabel('')
      setLinkUrl('')
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function removeAttachment(a: PartCatalogAttachment) {
    if (!confirm('Bu ek silinsin mi?')) return
    await api(`/part-attachments/${a.id}`, { method: 'DELETE' })
    onChanged()
  }

  async function uploadImage(f: File) {
    setBusy(true)
    setError('')
    try {
      await apiUploadWithFields(`/parts/${part.id}/image`, f, { remove_bg: removeBg ? 'true' : 'false' })
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function saveInfo() {
    setBusy(true)
    setError('')
    try {
      await api(`/parts/${part.id}`, { method: 'PATCH', body: { name, description } })
      setEditingName(false)
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function removePart() {
    if (!confirm(`"${part.name}" parçası kalıcı olarak silinsin mi? Tüm ekleri de silinir.`)) return
    setBusy(true)
    try {
      await api(`/parts/${part.id}`, { method: 'DELETE' })
      onDeleted()
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }

  return (
    <div className="fade-in">
      <button className="ghost" onClick={onBack} style={{ marginBottom: 14, paddingLeft: 8 }}>
        <ArrowLeft size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
        {parentName ? `${parentName} Parçasına Dön` : 'Parça Bilgi Bankası'}
      </button>

      <div className="card">
        <div className="row" style={{ alignItems: 'flex-start', gap: 16 }}>
          <div style={{ width: 160, flexShrink: 0 }}>
            {part.image_filename ? (
              <img
                src={`/uploads/parts/${part.image_filename}`}
                alt={part.name}
                style={{
                  width: '100%', aspectRatio: '1/1', objectFit: 'contain', borderRadius: 10,
                  border: '1px solid var(--border)', background: 'var(--bg-soft, #f5f6f8)',
                }}
              />
            ) : (
              <div
                style={{
                  width: '100%', aspectRatio: '1/1', borderRadius: 10, border: '1px dashed var(--border)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--muted)',
                }}
              >
                <PackageSearch size={32} />
              </div>
            )}
            {canEdit && (
              <label className="small ghost" style={{ display: 'block', textAlign: 'center', marginTop: 8, cursor: 'pointer' }}>
                Görseli Değiştir
                <input
                  type="file"
                  accept="image/*,.heic,.heif"
                  style={{ display: 'none' }}
                  onChange={(e) => e.target.files?.[0] && uploadImage(e.target.files[0])}
                />
              </label>
            )}
            {canEdit && (
              <label className="muted" style={{ display: 'flex', alignItems: 'center', gap: 6, justifyContent: 'center', marginTop: 8, fontSize: 12, cursor: 'pointer' }}>
                <input type="checkbox" checked={removeBg} onChange={(e) => setRemoveBg(e.target.checked)} />
                Arka planı kaldır
              </label>
            )}
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            {editingName ? (
              <>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  style={{ fontSize: 18, fontWeight: 700, width: '100%', marginBottom: 8 }}
                />
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  rows={2}
                  placeholder="Kısa açıklama (opsiyonel)"
                  style={{ width: '100%' }}
                />
                <div className="row" style={{ marginTop: 8, gap: 8 }}>
                  <button className="small primary" disabled={busy || !name.trim()} onClick={saveInfo}>Kaydet</button>
                  <button className="small ghost" onClick={() => { setEditingName(false); setName(part.name); setDescription(part.description) }}>
                    Vazgeç
                  </button>
                </div>
              </>
            ) : (
              <>
                <div className="row" style={{ gap: 8 }}>
                  <h1 style={{ marginBottom: 0, fontSize: 20 }}>{part.name}</h1>
                  {canEdit && (
                    <button className="small ghost" onClick={() => setEditingName(true)} title="Düzenle">
                      <Pencil size={13} />
                    </button>
                  )}
                  {canEdit && (
                    <button className="small danger" style={{ marginLeft: 'auto' }} disabled={busy} onClick={removePart}>
                      <Trash2 size={13} style={{ marginRight: 5, verticalAlign: -2 }} />
                      Parçayı Sil
                    </button>
                  )}
                </div>
                {part.description && <div className="muted" style={{ marginTop: 6 }}>{part.description}</div>}
              </>
            )}
          </div>
          <div style={{ flexShrink: 0, textAlign: 'center' }}>
            <img
              src={`/api/parts/${part.id}/qrcode`}
              alt="QR kod"
              width={84}
              height={84}
              style={{ borderRadius: 8, border: '1px solid var(--border)' }}
            />
            <a
              href={`/api/parts/${part.id}/qrcode?download=true`}
              className="small ghost"
              style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 4, marginTop: 6 }}
              download
            >
              <QrCode size={12} />
              İndir
            </a>
          </div>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      <div className="card">
        <h3 style={{ marginTop: 0, fontSize: 15 }}>Dokümanlar, Şemalar ve Videolar</h3>
        {part.attachments.length === 0 && (
          <div className="muted" style={{ marginBottom: 12 }}>Henüz bu parçaya ek eklenmemiş.</div>
        )}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 14 }}>
          {part.attachments.map((a) => (
            <div key={a.id} className="card" style={{ margin: 0, padding: 12 }}>
              <div className="row" style={{ marginBottom: 8, gap: 6 }}>
                {attIcon(a.kind)}
                <span style={{ fontWeight: 600, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {a.label || a.original_name || a.url}
                </span>
                {canEdit && (
                  <button className="small danger" style={{ marginLeft: 'auto' }} onClick={() => removeAttachment(a)} title="Eki sil">
                    <Trash2 size={12} />
                  </button>
                )}
              </div>
              <AttachmentViewer att={a} />
              <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>{fmt(a.created_at)}</div>
            </div>
          ))}
        </div>

        {canEdit && (
          <div style={{ marginTop: 16, paddingTop: 16, borderTop: '1px solid var(--border)' }}>
            <form className="row" onSubmit={uploadFile} style={{ flexWrap: 'wrap', marginBottom: 10 }}>
              <input
                placeholder="Etiket (ör. Devre Şeması, Kablo Şeması, Teknik Doküman)"
                value={fileLabel}
                onChange={(e) => setFileLabel(e.target.value)}
                style={{ minWidth: 240 }}
              />
              <input id={`part-att-file-${part.id}`} type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              <button className="small primary" disabled={!file || busy}>
                Dosya Ekle (foto/PDF/video)
              </button>
            </form>
            <form className="row" onSubmit={addLink} style={{ flexWrap: 'wrap' }}>
              <input placeholder="Bağlantı etiketi (ör. Eğitim Videosu)" value={linkLabel} onChange={(e) => setLinkLabel(e.target.value)} />
              <input
                placeholder="https://… (YouTube vb.)"
                value={linkUrl}
                onChange={(e) => setLinkUrl(e.target.value)}
                style={{ minWidth: 240 }}
              />
              <button className="small" disabled={!linkUrl.trim() || busy}>Bağlantı Ekle</button>
            </form>
          </div>
        )}
      </div>

      <SubPartsSection
        part={part}
        canEdit={canEdit}
        onOpenPart={onOpenPart}
        onChanged={onChanged}
        onAddSubPart={onAddSubPart}
      />
    </div>
  )
}

// ---------------------------------------------------------- Kart / Galeri
function PartCard({ part, onOpen }: { part: PartCatalogItem; onOpen: () => void }) {
  return (
    <button
      onClick={onOpen}
      className="card"
      style={{
        margin: 0, padding: 14, textAlign: 'left', cursor: 'pointer', display: 'flex',
        flexDirection: 'column', gap: 10, border: '1px solid var(--border)',
      }}
    >
      {part.image_filename ? (
        <img
          src={`/uploads/parts/${part.image_filename}`}
          alt={part.name}
          style={{
            width: '100%', aspectRatio: '1/1', objectFit: 'contain', borderRadius: 8,
            background: 'var(--bg-soft, #f5f6f8)',
          }}
        />
      ) : (
        <div
          style={{
            width: '100%', aspectRatio: '1/1', borderRadius: 8, background: 'var(--bg-soft, rgba(0,0,0,0.03))',
            display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--muted)',
          }}
        >
          <PackageSearch size={28} />
        </div>
      )}
      <div style={{ fontWeight: 600, fontSize: 13.5 }}>{part.name}</div>
      <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
        {part.attachments.length > 0 && (
          <div className="muted" style={{ fontSize: 11.5 }}>{part.attachments.length} ek</div>
        )}
        {part.children.length > 0 && (
          <div className="muted" style={{ fontSize: 11.5, display: 'flex', alignItems: 'center', gap: 3 }}>
            <Boxes size={11} /> {part.children.length} alt parça
          </div>
        )}
      </div>
    </button>
  )
}

function NewPartModal({
  parentId,
  parentName,
  onClose,
  onCreated,
}: {
  // Belirtilirse yeni parça bu id'nin ALT parçası olarak oluşturulur.
  parentId?: number | null
  parentName?: string | null
  onClose: () => void
  onCreated: () => void
}) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [removeBg, setRemoveBg] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit() {
    if (!name.trim()) return
    setBusy(true)
    setError('')
    try {
      const created = await api<PartCatalogItem>('/parts', {
        method: 'POST',
        body: { name: name.trim(), description, parent_id: parentId ?? null },
      })
      if (file) await apiUploadWithFields(`/parts/${created.id}/image`, file, { remove_bg: removeBg ? 'true' : 'false' })
      onCreated()
    } catch (e) {
      setError((e as Error).message)
      setBusy(false)
    }
  }

  return (
    <div
      style={{ position: 'fixed', inset: 0, background: 'rgba(15, 23, 42, 0.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 200 }}
      onClick={onClose}
    >
      <div className="card" style={{ width: 380, maxWidth: '90vw' }} onClick={(e) => e.stopPropagation()}>
        <div className="row" style={{ marginBottom: 10 }}>
          <h3 style={{ margin: 0, fontSize: 15 }}>{parentName ? `${parentName} — Alt Parça Ekle` : 'Yeni Parça'}</h3>
          <button className="ghost small" style={{ marginLeft: 'auto' }} onClick={onClose}><X size={14} /></button>
        </div>
        <input
          placeholder="Parça adı (ör. Buji, Kuplör)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={{ width: '100%', marginBottom: 8 }}
          autoFocus
        />
        <textarea
          placeholder="Kısa açıklama (opsiyonel)"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={2}
          style={{ width: '100%', marginBottom: 8 }}
        />
        <label className="muted" style={{ fontSize: 12.5 }}>Kapak görseli (opsiyonel)</label>
        <input type="file" accept="image/*,.heic,.heif" onChange={(e) => setFile(e.target.files?.[0] ?? null)} style={{ display: 'block', marginTop: 4 }} />
        {file && (
          <label className="muted" style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 8, fontSize: 12.5, cursor: 'pointer' }}>
            <input type="checkbox" checked={removeBg} onChange={(e) => setRemoveBg(e.target.checked)} />
            Arka planı otomatik kaldır
          </label>
        )}
        {error && <div className="error" style={{ marginTop: 8 }}>{error}</div>}
        <div className="row" style={{ marginTop: 14, gap: 8, justifyContent: 'flex-end' }}>
          <button className="ghost" onClick={onClose}>Vazgeç</button>
          <button className="primary" disabled={busy || !name.trim()} onClick={submit}>Oluştur</button>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------- Sayfa
export default function PartsCatalogPage({
  canEdit,
  initialSelectedId,
}: {
  canEdit: boolean
  initialSelectedId?: number | null
}) {
  const [parts, setParts] = useState<PartCatalogItem[] | null>(null)
  const [error, setError] = useState('')
  const [selectedId, setSelectedId] = useState<number | null>(initialSelectedId ?? null)
  const [showNew, setShowNew] = useState(false)
  const [search, setSearch] = useState('')

  function load() {
    api<PartCatalogItem[]>('/parts')
      .then(setParts)
      .catch((e) => {
        setError((e as Error).message)
        setParts([])
      })
  }

  useEffect(load, [])

  // QR kod / global aramadan gelen derin bağlantı: initialSelectedId
  // değiştiğinde (örn. QR'dan ardışık iki farklı parça açılırsa) seçimi günceller.
  useEffect(() => {
    if (initialSelectedId != null) setSelectedId(initialSelectedId)
  }, [initialSelectedId])

  // Seçili parça en üst seviyede olmayabilir (bir alt parça açılmış
  // olabilir) — ağacın tamamında (children dahil) aranır.
  const selected = selectedId !== null && parts ? findPartById(parts, selectedId) : null
  const parent = selected?.parent_id != null && parts ? findPartById(parts, selected.parent_id) : null

  if (selected) {
    return (
      <>
        <PartDetail
          part={selected}
          canEdit={canEdit}
          parentName={parent?.name ?? null}
          onBack={() => setSelectedId(selected.parent_id)}
          onOpenPart={(id) => setSelectedId(id)}
          onChanged={load}
          onDeleted={() => {
            setSelectedId(selected.parent_id)
            load()
          }}
          onAddSubPart={() => setShowNew(true)}
        />
        {showNew && (
          <NewPartModal
            parentId={selected.id}
            parentName={selected.name}
            onClose={() => setShowNew(false)}
            onCreated={() => {
              setShowNew(false)
              load()
            }}
          />
        )}
      </>
    )
  }

  const filtered = (parts ?? []).filter((p) => p.name.toLowerCase().includes(search.trim().toLowerCase()))

  return (
    <>
      <PageHeader
        title="Parça Bilgi Bankası"
        subtitle="Arıza/bakım anında hızlı referans: parçaların dokümanı, kablo/devre şeması ve eğitim videosu."
        actions={
          canEdit ? (
            <button className="primary" onClick={() => setShowNew(true)}>
              <PlusCircle size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
              Yeni Parça
            </button>
          ) : undefined
        }
      />
      <div className="row" style={{ marginBottom: 14 }}>
        <input
          placeholder="Parça ara…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ minWidth: 220 }}
        />
      </div>
      {error && <div className="error">{error}</div>}
      {parts === null ? (
        <div className="muted">Yükleniyor…</div>
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={<PackageSearch size={28} />}
          title="Henüz parça eklenmemiş"
          description="Sık arızalanan veya bakımı yapılan parçaların görseli ve dokümanlarını buraya ekleyerek bir referans kütüphanesi oluşturabilirsiniz."
        />
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))', gap: 14 }}>
          {filtered.map((p) => (
            <PartCard key={p.id} part={p} onOpen={() => setSelectedId(p.id)} />
          ))}
        </div>
      )}
      {showNew && (
        <NewPartModal
          onClose={() => setShowNew(false)}
          onCreated={() => {
            setShowNew(false)
            load()
          }}
        />
      )}
    </>
  )
}
