import { useEffect, useMemo, useState } from 'react'
import { api, apiUploadWithFields } from '../api'
import { PageHeader } from '../components/ui'
import { ChevronRight, Paperclip, FileText, ImageIcon, LinkIcon, Download, Trash2, UploadCloud, ShieldAlert } from 'lucide-react'
import Tram3D from '../components/Tram3D'
import type {
  BulkCatalogImportResult,
  MergedTreeNode,
  Model3D,
  Plan,
  TreeNode,
  TreeNodeFlat,
  TreeNodeVehicleException,
  PartHotspot,
  NodeAttachment,
  PartCatalogItem,
  TreeNodePartUsage,
  VehicleModelMergeResult,
  Vehicle,
  User,
} from '../types'

// Parça ağacı (alt parçalar iç içe) iç içe geldiği için, "hangi maddede
// hangi parçadan kaç adet" seçicisinde düz (children'sız) bir liste olarak
// göstermek üzere derinlik öneki ile düzleştirir.
function flattenParts(items: PartCatalogItem[], depth = 0): { id: number; label: string }[] {
  const out: { id: number; label: string }[] = []
  for (const it of items) {
    out.push({ id: it.id, label: `${'— '.repeat(depth)}${it.name}` })
    out.push(...flattenParts(it.children, depth + 1))
  }
  return out
}

// Periyot seçicide "Tümü" (birleşik görünüm) için özel/sentetik değer —
// gerçek bir plan id'si değildir.
const ALL_PLANS_VALUE = '__all__'

// Plan periyodunu türüne göre okunur biçimde göster (km bazlı ↔ süre bazlı ↔
// kaynakta yorumlanamamış/belirsiz — "?" olarak kalmalı, asla 0 km gibi
// yanlış bir sayı gösterilmemeli).
function planPeriodLabel(p: Plan): string {
  if (p.plan_type === 'unknown') return 'Belirsiz (?)'
  if (p.plan_type === 'time') {
    const mo = p.interval_months ?? 0
    return mo % 12 === 0 ? `${mo / 12} yıl` : `${mo} ay`
  }
  return `${fmtKm(p.interval_km)} km`
}

// ---------------------------------------------------------- Madde-parça eşleşmesi (stok)
function NodePartsSection({
  nodeId,
  knownParts,
}: {
  nodeId: number
  knownParts: { id: number; label: string }[]
}) {
  const [usages, setUsages] = useState<TreeNodePartUsage[]>([])
  const [partId, setPartId] = useState('')
  const [qty, setQty] = useState('1')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  function load() {
    api<TreeNodePartUsage[]>(`/tree/${nodeId}/parts`).then(setUsages).catch(() => setUsages([]))
  }
  useEffect(load, [nodeId])

  async function addUsage(e: React.FormEvent) {
    e.preventDefault()
    if (!partId) return
    setBusy(true)
    setError('')
    try {
      await api(`/tree/${nodeId}/parts`, {
        method: 'POST',
        body: { part_id: Number(partId), qty: Math.max(0.001, Number(qty) || 1) },
      })
      setPartId('')
      setQty('1')
      load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function removeUsage(id: number) {
    await api(`/tree/part-usages/${id}`, { method: 'DELETE' })
    load()
  }

  return (
    <div className="node-info-panel" style={{ marginTop: 4, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
      <label className="node-info-label">
        Kullanılan Parçalar (madde tamamlanınca stoktan otomatik düşer)
      </label>
      <div className="muted" style={{ fontSize: 11.5, marginBottom: 6 }}>
        Miktar kesirli olabilir — ör. parçanın birimi "kg" ise ve bu maddede 1 kg'lık üründen
        200 gram kullanılıyorsa 0.2 girin.
      </div>
      {usages.length > 0 && (
        <div className="attach-list" style={{ marginBottom: 8 }}>
          {usages.map((u) => (
            <div key={u.id} className="attach-list-row">
              <span className="attach-chip">
                <span>{u.part_name || `#${u.part_id}`} × {u.qty}</span>
              </span>
              <button className="small danger" onClick={() => removeUsage(u.id)} title="Eşleşmeyi kaldır">
                <Trash2 size={12} />
              </button>
            </div>
          ))}
        </div>
      )}
      <form className="row" onSubmit={addUsage} style={{ flexWrap: 'wrap', gap: 6 }}>
        <select value={partId} onChange={(e) => setPartId(e.target.value)} style={{ minWidth: 200 }}>
          <option value="">Parça seç…</option>
          {knownParts.map((p) => (
            <option key={p.id} value={p.id}>{p.label}</option>
          ))}
        </select>
        <input
          type="number"
          min={0.001}
          step="any"
          placeholder="Miktar"
          value={qty}
          onChange={(e) => setQty(e.target.value)}
          style={{ width: 80 }}
        />
        <button className="small primary" disabled={!partId || busy}>Ekle</button>
      </form>
      {error && <div className="error" style={{ marginTop: 6 }}>{error}</div>}
    </div>
  )
}

// ---------------------------------------------------------- Zorunlu/opsiyonel araç istisnaları
// Genel varsayılan (node.is_mandatory) TÜM araçlar için geçerlidir; burada
// eklenen istisnalar yalnızca TEK BİR belirli araç için o varsayılanı
// geçersiz kılar (ör. genelde zorunlu bir madde, yalnızca 3805 nolu araçta
// zorunlu değil). Admin, mühendis ve amir kullanabilir.
function NodeVehicleExceptionsSection({ nodeId }: { nodeId: number }) {
  const [exceptions, setExceptions] = useState<TreeNodeVehicleException[]>([])
  const [vehicles, setVehicles] = useState<Vehicle[]>([])
  const [vehicleId, setVehicleId] = useState<number | ''>('')
  const [mandatory, setMandatory] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  function load() {
    api<TreeNodeVehicleException[]>(`/tree/${nodeId}/vehicle-exceptions`).then(setExceptions).catch(() => setExceptions([]))
  }
  useEffect(load, [nodeId])
  useEffect(() => {
    api<Vehicle[]>('/vehicles').then(setVehicles).catch(() => setVehicles([]))
  }, [])

  async function addException(e: React.FormEvent) {
    e.preventDefault()
    if (vehicleId === '') return
    setBusy(true)
    setError('')
    try {
      await api(`/tree/${nodeId}/vehicle-exceptions`, {
        method: 'POST',
        body: { vehicle_id: vehicleId, is_mandatory: mandatory },
      })
      setVehicleId('')
      setMandatory(false)
      load()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function removeException(id: number) {
    await api(`/tree/vehicle-exceptions/${id}`, { method: 'DELETE' })
    load()
  }

  const usedIds = useMemo(() => new Set(exceptions.map((e) => e.vehicle.id)), [exceptions])
  const availableVehicles = useMemo(() => vehicles.filter((v) => !usedIds.has(v.id)), [vehicles, usedIds])

  return (
    <div className="node-info-panel" style={{ marginTop: 4, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
      <label className="node-info-label">
        Araç İstisnaları — belirli bir araçta genel zorunlu/zorunlu değil ayarını geçersiz kılar
      </label>
      {exceptions.length > 0 && (
        <div className="attach-list" style={{ marginBottom: 8 }}>
          {exceptions.map((exc) => (
            <div key={exc.id} className="attach-list-row">
              <span className="attach-chip">
                <span>{exc.vehicle.code} — {exc.is_mandatory ? 'Zorunlu' : 'Zorunlu Değil'}</span>
              </span>
              <button className="small danger" onClick={() => removeException(exc.id)} title="İstisnayı kaldır (genel varsayılana döner)">
                <Trash2 size={12} />
              </button>
            </div>
          ))}
        </div>
      )}
      <form className="row" onSubmit={addException} style={{ flexWrap: 'wrap', gap: 6 }}>
        <select
          value={vehicleId}
          onChange={(e) => setVehicleId(e.target.value ? Number(e.target.value) : '')}
          style={{ minWidth: 200 }}
        >
          <option value="">Araç seç…</option>
          {availableVehicles.map((v) => (
            <option key={v.id} value={v.id}>{v.code} — {v.name}</option>
          ))}
        </select>
        <label className="row" style={{ gap: 4, alignItems: 'center' }}>
          <input type="checkbox" checked={mandatory} onChange={(e) => setMandatory(e.target.checked)} />
          Bu araçta zorunlu olsun
        </label>
        <button className="small primary" disabled={vehicleId === '' || busy}>Ekle</button>
      </form>
      {error && <div className="error" style={{ marginTop: 6 }}>{error}</div>}
    </div>
  )
}

// ---------------------------------------------------------- Madde bilgi/ek paneli (Faz 10)
function NodeInfoPanel({
  node,
  onChanged,
  isLeaf,
  knownParts,
}: {
  node: TreeNode
  onChanged: () => void
  isLeaf: boolean
  knownParts: { id: number; label: string }[]
}) {
  const [note, setNote] = useState(node.note ?? '')
  const [savingNote, setSavingNote] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [fileLabel, setFileLabel] = useState('')
  const [linkLabel, setLinkLabel] = useState('')
  const [linkUrl, setLinkUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => setNote(node.note ?? ''), [node.note])

  async function saveNote() {
    setSavingNote(true)
    setError('')
    try {
      await api(`/tree/${node.id}`, { method: 'PATCH', body: { note } })
      onChanged()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSavingNote(false)
    }
  }

  async function uploadFile(e: React.FormEvent) {
    e.preventDefault()
    if (!file) return
    setBusy(true)
    setError('')
    try {
      await apiUploadWithFields(`/tree/${node.id}/attachments`, file, { label: fileLabel })
      setFile(null)
      setFileLabel('')
      const input = document.getElementById(`att-file-${node.id}`) as HTMLInputElement | null
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
      await api(`/tree/${node.id}/attachment-link`, {
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

  async function removeAttachment(a: NodeAttachment) {
    if (!confirm('Bu ek silinsin mi?')) return
    await api(`/attachments/${a.id}`, { method: 'DELETE' })
    onChanged()
  }

  const attIcon = (a: NodeAttachment) =>
    a.kind === 'image' ? <ImageIcon size={13} /> :
    a.kind === 'pdf' ? <FileText size={13} /> :
    a.kind === 'link' ? <LinkIcon size={13} /> : <Download size={13} />

  return (
    <div className="node-info-panel">
      <label className="node-info-label">Açıklama / Talimat (usta görür)</label>
      <textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        rows={2}
        placeholder="Ör. İzolasyon değeri 1 MΩ'un altındaysa raporlayın."
      />
      <div className="row" style={{ marginBottom: 10 }}>
        <button className="small primary" onClick={saveNote} disabled={savingNote}>
          {savingNote ? 'Kaydediliyor…' : 'Açıklamayı Kaydet'}
        </button>
      </div>

      <form className="row" onSubmit={uploadFile} style={{ flexWrap: 'wrap' }}>
        <input
          placeholder="Etiket (opsiyonel)"
          value={fileLabel}
          onChange={(e) => setFileLabel(e.target.value)}
        />
        <input id={`att-file-${node.id}`} type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        <button className="small primary" disabled={!file || busy}>
          Dosya Ekle (foto/PDF/talimat)
        </button>
      </form>

      <form className="row" onSubmit={addLink} style={{ marginTop: 8, flexWrap: 'wrap' }}>
        <input placeholder="Bağlantı etiketi" value={linkLabel} onChange={(e) => setLinkLabel(e.target.value)} />
        <input
          placeholder="https://… (video vb.)"
          value={linkUrl}
          onChange={(e) => setLinkUrl(e.target.value)}
          style={{ minWidth: 220 }}
        />
        <button className="small" disabled={!linkUrl.trim() || busy}>Bağlantı Ekle</button>
      </form>

      {error && <div className="error" style={{ marginTop: 8 }}>{error}</div>}

      {node.attachments && node.attachments.length > 0 && (
        <div className="attach-list">
          {node.attachments.map((a) => {
            const href = a.kind === 'link' ? a.url : `/uploads/attachments/${a.filename}`
            return (
              <div key={a.id} className="attach-list-row">
                <a href={href} target="_blank" rel="noreferrer" className="attach-chip">
                  {attIcon(a)}
                  <span>{a.label || a.original_name || a.url}</span>
                </a>
                <button className="small danger" onClick={() => removeAttachment(a)} title="Eki sil">
                  <Trash2 size={12} />
                </button>
              </div>
            )
          })}
        </div>
      )}

      {isLeaf && <NodePartsSection nodeId={node.id} knownParts={knownParts} />}
    </div>
  )
}

const fmtKm = (n: number) => n.toLocaleString('tr-TR')

function flatten(nodes: TreeNode[], depth = 0): { node: TreeNode; depth: number }[] {
  const out: { node: TreeNode; depth: number }[] = []
  for (const n of nodes) {
    out.push({ node: n, depth })
    out.push(...flatten(n.children, depth + 1))
  }
  return out
}

// "Tümü" (birleşik görünüm) için salt-okunur düğüm — düzenleme
// düğmeleri yok, yaprak maddelerde kart kodunun yanında hangi periyoda ait
// olduğu (period_label) da rozet olarak gösterilir.
function MergedNodeView({ node }: { node: MergedTreeNode }) {
  const [open, setOpen] = useState(true)
  const isLeaf = node.children.length === 0

  return (
    <li className="tree-node">
      <div className={`tree-row ${isLeaf ? 'leaf' : ''}`}>
        {!isLeaf ? (
          <button className={`toggle ${open ? 'expanded' : ''}`} onClick={() => setOpen(!open)}>
            <ChevronRight size={15} />
          </button>
        ) : (
          <span style={{ width: 22, textAlign: 'center' }}>☐</span>
        )}
        <span className="node-name">
          {node.name}
          {node.card_code && <span className="card-code-pill">{node.card_code}</span>}
          {isLeaf && node.period_label && (
            <span className="card-code-pill" style={{ background: 'var(--amber-soft, #fde8c4)', color: 'var(--amber, #92600d)' }}>
              {node.period_label}
              {node.is_catalog_only ? ' · katalog' : ''}
            </span>
          )}
          {isLeaf && node.vehicle_model && (
            <span className="card-code-pill" style={{ background: 'var(--blue-soft, #d6e4ff)', color: 'var(--blue, #1b4da0)' }}>
              {node.vehicle_model}
            </span>
          )}
        </span>
      </div>
      {!isLeaf && (
        <div className={`tree-children ${open ? 'open' : ''}`}>
          <div className="tree-children-inner">
            <ul className="tree-sub">
              {node.children.map((c) => (
                <MergedNodeView key={c.id} node={c} />
              ))}
            </ul>
          </div>
        </div>
      )}
    </li>
  )
}

function EditorNode({
  node,
  onChanged,
  allNodes,
  knownParts,
  isAdmin,
}: {
  node: TreeNode
  onChanged: () => void
  allNodes: { node: TreeNode; depth: number }[]
  knownParts: { id: number; label: string }[]
  isAdmin: boolean
}) {
  const [open, setOpen] = useState(true)
  const [adding, setAdding] = useState(false)
  const [newName, setNewName] = useState('')
  const [renaming, setRenaming] = useState(false)
  const [name, setName] = useState(node.name)
  const [moving, setMoving] = useState(false)
  const [showInfo, setShowInfo] = useState(false)
  const [showExceptions, setShowExceptions] = useState(false)
  const [mandatoryBusy, setMandatoryBusy] = useState(false)
  const attCount = (node.attachments?.length ?? 0) + (node.note ? 1 : 0)

  async function toggleMandatory() {
    setMandatoryBusy(true)
    try {
      await api(`/tree/${node.id}/mandatory`, {
        method: 'PATCH',
        body: { is_mandatory: !node.is_mandatory },
      })
      onChanged()
    } catch (e) {
      alert((e as Error).message)
    } finally {
      setMandatoryBusy(false)
    }
  }

  async function addChild(e: React.FormEvent) {
    e.preventDefault()
    if (!newName.trim()) return
    await api('/tree', { method: 'POST', body: { name: newName.trim(), parent_id: node.id } })
    setNewName('')
    setAdding(false)
    onChanged()
  }

  async function rename(e: React.FormEvent) {
    e.preventDefault()
    await api(`/tree/${node.id}`, { method: 'PATCH', body: { name } })
    setRenaming(false)
    onChanged()
  }

  async function remove() {
    if (!confirm(`"${node.name}" ve tüm alt maddeleri silinsin mi?`)) return
    await api(`/tree/${node.id}`, { method: 'DELETE' })
    onChanged()
  }

  async function move(targetId: string) {
    try {
      await api(`/tree/${node.id}`, {
        method: 'PATCH',
        body: { parent_id: targetId === '' ? null : Number(targetId) },
      })
      setMoving(false)
      onChanged()
    } catch (e) {
      alert((e as Error).message)
    }
  }

  const isLeaf = node.children.length === 0

  return (
    <li className="tree-node">
      <div className={`tree-row ${isLeaf ? 'leaf' : ''}`}>
        {!isLeaf ? (
          <button className={`toggle ${open ? 'expanded' : ''}`} onClick={() => setOpen(!open)}>
            <ChevronRight size={15} />
          </button>
        ) : (
          <span style={{ width: 22, textAlign: 'center' }}>☐</span>
        )}
        {renaming ? (
          <form onSubmit={rename} className="row">
            <input value={name} onChange={(e) => setName(e.target.value)} autoFocus />
            <button className="small primary">Kaydet</button>
            <button className="small" type="button" onClick={() => setRenaming(false)}>
              Vazgeç
            </button>
          </form>
        ) : (
          <span className="node-name">
            {node.name}
            {node.card_code && <span className="card-code-pill">{node.card_code}</span>}
            {isLeaf && (
              <span
                className="card-code-pill"
                style={
                  node.is_mandatory
                    ? undefined
                    : { background: 'var(--amber-soft, #fde8c4)', color: 'var(--amber, #92600d)' }
                }
              >
                {node.is_mandatory ? 'Zorunlu' : 'Zorunlu Değil'}
              </span>
            )}
          </span>
        )}
        {!renaming && (
          <span className="row" style={{ marginLeft: 'auto' }}>
            {isLeaf && (
              <button
                className="small"
                disabled={mandatoryBusy}
                onClick={toggleMandatory}
                title={
                  node.is_mandatory
                    ? 'Bu maddeyi zorunlu değil yap — yeni açılan bakımlarda VE zaten açık olan bakımlarda otomatik tikli gelir'
                    : 'Bu maddeyi tekrar zorunlu yap — zaten açık bakımlarda otomatik tik kaldırılır (usta elle tamamladıysa dokunulmaz)'
                }
              >
                {node.is_mandatory ? 'Zorunlu Değil Yap' : 'Zorunlu Yap'}
              </button>
            )}
            {isLeaf && (
              <button
                className={`small ${showExceptions ? 'primary' : ''}`}
                onClick={() => setShowExceptions(!showExceptions)}
                title="Belirli bir araç için genel ayarı geçersiz kıl"
              >
                Araç İstisnaları
              </button>
            )}
            {isAdmin && <button className="small" onClick={() => setAdding(!adding)}>+ Alt Madde</button>}
            {isAdmin && (
              <button
                className={`small ${showInfo ? 'primary' : ''}`}
                onClick={() => setShowInfo(!showInfo)}
                title="Not / fotoğraf / PDF / talimat ekle"
              >
                <Paperclip size={12} style={{ marginRight: 4, verticalAlign: -2 }} />
                Bilgi/Ek{attCount > 0 ? ` (${attCount})` : ''}
              </button>
            )}
            {isAdmin && <button className="small" onClick={() => setRenaming(true)}>Yeniden Adlandır</button>}
            {isAdmin && <button className="small" onClick={() => setMoving(!moving)}>Taşı</button>}
            {isAdmin && <button className="small danger" onClick={remove}>Sil</button>}
          </span>
        )}
      </div>
      {showExceptions && isLeaf && <NodeVehicleExceptionsSection nodeId={node.id} />}
      {showInfo && isAdmin && (
        <NodeInfoPanel node={node} onChanged={onChanged} isLeaf={isLeaf} knownParts={knownParts} />
      )}
      {moving && (
        <div className="row" style={{ padding: '4px 0 4px 30px' }}>
          <select defaultValue="__none" onChange={(e) => e.target.value !== '__none' && move(e.target.value)}>
            <option value="__none" disabled>Taşınacak yer…</option>
            <option value="">(En üst seviye — bu planın yeni bir ana bölümü)</option>
            {allNodes
              .filter((x) => x.node.id !== node.id)
              .map((x) => (
                <option key={x.node.id} value={x.node.id}>
                  {' '.repeat(x.depth * 3)}{x.node.name}
                </option>
              ))}
          </select>
        </div>
      )}
      {adding && (
        <form onSubmit={addChild} className="row" style={{ padding: '4px 0 4px 30px' }}>
          <input
            placeholder="Yeni madde adı"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            autoFocus
          />
          <button className="small primary">Ekle</button>
        </form>
      )}
      {!isLeaf && (
        <div className={`tree-children ${open ? 'open' : ''}`}>
          <div className="tree-children-inner">
            <ul className="tree-sub">
              {node.children.map((c) => (
                <EditorNode key={c.id} node={c} onChanged={onChanged} allNodes={allNodes} knownParts={knownParts} isAdmin={isAdmin} />
              ))}
            </ul>
          </div>
        </div>
      )}
    </li>
  )
}

// ---------------------------------------------------------- 3D Model editörü
function Model3DEditor({
  model,
  planId,
  flatNodes,
  onDeleteModel,
}: {
  model: Model3D
  planId: number
  flatNodes: TreeNodeFlat[]
  onDeleteModel: () => void
}) {
  const [hotspots, setHotspots] = useState<PartHotspot[]>([])
  const [selectedMesh, setSelectedMesh] = useState<string | null>(null)
  const [nodeId, setNodeId] = useState<number | ''>('')
  const [label, setLabel] = useState('')
  const [error, setError] = useState('')

  const load = () => {
    api<PartHotspot[]>(`/models3d/${model.id}/hotspots?plan_id=${planId}`)
      .then(setHotspots)
      .catch(() => setHotspots([]))
  }
  useEffect(load, [model.id, planId])

  function nodeName(id: number) {
    return flatNodes.find((n) => n.id === id)?.name ?? `#${id}`
  }

  async function saveHotspot(e: React.FormEvent) {
    e.preventDefault()
    if (!selectedMesh || nodeId === '') return
    setError('')
    try {
      await api(`/models3d/${model.id}/hotspots`, {
        method: 'POST',
        body: { plan_id: planId, tree_node_id: nodeId, mesh_name: selectedMesh, label },
      })
      setSelectedMesh(null)
      setNodeId('')
      setLabel('')
      load()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function removeHotspot(id: number) {
    await api(`/model-hotspots/${id}`, { method: 'DELETE' })
    load()
  }

  const tints: Record<string, string> = {}
  for (const h of hotspots) tints[h.mesh_name] = '#1b4da0'
  if (selectedMesh) tints[selectedMesh] = '#b45309'

  return (
    <div style={{ marginBottom: 22, paddingBottom: 18, borderBottom: '1px solid var(--border-soft)' }}>
      <div className="row" style={{ marginBottom: 8 }}>
        <strong>{model.label || 'İsimsiz model'}</strong>
        <span className="muted">({hotspots.length} parça eşleşmesi — bu plan)</span>
        <button className="small danger" style={{ marginLeft: 'auto' }} onClick={onDeleteModel}>
          Modeli Sil
        </button>
      </div>
      <Tram3D
        src={`/uploads/models/${model.filename}`}
        meshTints={tints}
        onMeshClick={(name) => {
          setSelectedMesh(name)
          const existing = hotspots.find((h) => h.mesh_name === name)
          setNodeId(existing ? existing.tree_node_id : '')
          setLabel(existing ? existing.label : '')
        }}
      />
      <p className="muted" style={{ marginTop: 8, fontSize: 12.5 }}>
        Fareyle sürükleyip modeli döndürün, dilek tekerleğiyle yakınlaştırın. Bir parçaya
        tıklayın (turuncu ile vurgulanır), sonra hangi bakım maddesiyle eşleşeceğini seçip
        kaydedin. Mavi vurgulu parçalar bu plan için zaten eşleştirilmiştir.
      </p>
      {error && <div className="error">{error}</div>}
      {selectedMesh && (
        <form className="row" onSubmit={saveHotspot} style={{ marginTop: 10 }}>
          <span className="badge in_progress">{selectedMesh}</span>
          <select
            value={nodeId}
            onChange={(e) => setNodeId(e.target.value ? Number(e.target.value) : '')}
            style={{ minWidth: 240 }}
          >
            <option value="">Hangi ağaç maddesiyle eşleşsin?</option>
            {flatNodes.map((n) => (
              <option key={n.id} value={n.id}>
                {n.name}
              </option>
            ))}
          </select>
          <input
            placeholder="Etiket (opsiyonel)"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
          <button className="small primary" disabled={nodeId === ''}>
            Eşleşmeyi Kaydet
          </button>
          <button className="small" type="button" onClick={() => setSelectedMesh(null)}>
            Vazgeç
          </button>
        </form>
      )}
      {hotspots.length > 0 && (
        <div style={{ marginTop: 12 }}>
          {hotspots.map((h) => (
            <div key={h.id} className="row" style={{ padding: '5px 0' }}>
              <span className="muted" style={{ fontFamily: 'monospace', fontSize: 12 }}>{h.mesh_name}</span>
              <span>→</span>
              <span>{h.label || nodeName(h.tree_node_id)}</span>
              <button className="small danger" style={{ marginLeft: 'auto' }} onClick={() => removeHotspot(h.id)}>
                Sil
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function Model3DSection({ planId, flatNodes }: { planId: number; flatNodes: TreeNodeFlat[] }) {
  const [modelsList, setModelsList] = useState<Model3D[]>([])
  const [label, setLabel] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')

  const load = () => {
    api<Model3D[]>('/models3d').then(setModelsList).catch(() => setModelsList([]))
  }
  useEffect(load, [])

  async function upload(e: React.FormEvent) {
    e.preventDefault()
    if (!file) return
    setUploading(true)
    setError('')
    try {
      await apiUploadWithFields('/models3d', file, { label })
      setLabel('')
      setFile(null)
      const input = document.getElementById('model3d-file-input') as HTMLInputElement | null
      if (input) input.value = ''
      load()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setUploading(false)
    }
  }

  async function removeModel(id: number) {
    if (!confirm('Bu 3D model silinsin mi? Tüm planlardaki parça eşleşmeleri de silinir.')) return
    await api(`/models3d/${id}`, { method: 'DELETE' })
    load()
  }

  return (
    <div className="card">
      <h2 style={{ fontSize: 16, marginBottom: 6 }}>Tıklanabilir 3D Tramvay Modeli</h2>
      <p className="muted" style={{ marginBottom: 14 }}>
        3D model (.glb) tüm planlarda ortaktır (aynı fiziksel tramvay). Parça eşleşmeleri ise
        seçili plana özeldir — çünkü aynı parça farklı periyotlarda farklı kontrol maddesine
        karşılık gelebilir. Modeli döndürüp bir parçaya tıklayarak, o parçayı yukarıdaki ağaçtan
        bir maddeyle eşleştirin. Usta ekranında tamamlanan maddelere bağlı parçalar yeşile
        dönecektir.
      </p>
      <form className="row" onSubmit={upload} style={{ marginBottom: 16 }}>
        <input
          placeholder="Etiket (ör. Tramvay 3D Model)"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        />
        <input
          id="model3d-file-input"
          type="file"
          accept=".glb,.gltf"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <button className="primary" disabled={!file || uploading}>
          {uploading ? 'Yükleniyor…' : 'Model Yükle (.glb)'}
        </button>
      </form>
      {error && <div className="error">{error}</div>}
      {modelsList.map((m) => (
        <Model3DEditor
          key={m.id}
          model={m}
          planId={planId}
          flatNodes={flatNodes}
          onDeleteModel={() => removeModel(m.id)}
        />
      ))}
      {modelsList.length === 0 && <div className="muted">Henüz 3D model yüklenmedi.</div>}
    </div>
  )
}

// ---------------------------------------------------------- Toplu Bakım Kataloğu İçe Aktarma
// KB TOPLU LİSTE formatı: her satır tek bir bakım kalemi —
// ARAÇ | BÖLGE | SİSTEM | EKİPMAN | BAKIM | BAKIM KALEMİ | KART KODU.
// Yukarıdaki "Excel ile Yeni Bakım Tablosu" tek model + sütun-bloklu format
// beklerken, bu ekran ÇOK modelli, satır-bazlı ve hem km hem süre (aylık/
// yıllık, "3M"/"1Y" gibi) periyotları tek dosyada barındıran kaynak
// listeler için — plan+ağaç her benzersiz (model, periyot) için otomatik
// oluşturulur/güncellenir.
function BulkCatalogImportSection({
  knownModels,
  onImported,
}: {
  knownModels: string[]
  onImported: () => void
}) {
  const [open, setOpen] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [vehicleModel, setVehicleModel] = useState('')
  const [uploading, setUploading] = useState(false)
  const [previewing, setPreviewing] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<BulkCatalogImportResult | null>(null)

  async function runUpload(dryRun: boolean) {
    if (!file) return
    if (dryRun) setPreviewing(true)
    else setUploading(true)
    setError('')
    setResult(null)
    try {
      const res = await apiUploadWithFields<BulkCatalogImportResult>('/tree/import-bulk-catalog', file, {
        vehicle_model: vehicleModel.trim(),
        dry_run: dryRun ? 'true' : 'false',
      })
      setResult(res)
      if (!dryRun) {
        setFile(null)
        const input = document.getElementById('bulk-catalog-input') as HTMLInputElement | null
        if (input) input.value = ''
        onImported()
      }
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setPreviewing(false)
      setUploading(false)
    }
  }

  async function preview(e: React.FormEvent) {
    e.preventDefault()
    await runUpload(true)
  }

  async function confirmUpload() {
    await runUpload(false)
  }

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="row" style={{ justifyContent: 'space-between', cursor: 'pointer' }} onClick={() => setOpen(!open)}>
        <strong>
          <UploadCloud size={15} style={{ marginRight: 6, verticalAlign: -3 }} />
          Toplu Bakım Kataloğu İçe Aktar (KB Toplu Liste)
        </strong>
        <button className="small" type="button" onClick={(e) => { e.stopPropagation(); setOpen(!open) }}>
          {open ? 'Gizle' : 'Göster'}
        </button>
      </div>
      {open && (
        <>
      <form className="row" onSubmit={preview} style={{ flexWrap: 'wrap', marginTop: 10 }}>
        <input
          placeholder="Araç modeli (boş = dosyadaki ARAÇ sütunundan otomatik)"
          value={vehicleModel}
          onChange={(e) => setVehicleModel(e.target.value)}
          list="bulk-catalog-known-models"
          style={{ minWidth: 260 }}
        />
        <datalist id="bulk-catalog-known-models">
          {knownModels.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
        <input
          id="bulk-catalog-input"
          type="file"
          accept=".xlsx,.xls"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null)
            setResult(null)
          }}
        />
        <button className="secondary" disabled={!file || uploading || previewing}>
          {previewing ? 'Önizleniyor…' : 'Önizle'}
        </button>
        {result && result.dry_run && (
          <button
            type="button"
            className="primary"
            disabled={!file || uploading || previewing}
            onClick={confirmUpload}
          >
            {uploading ? 'İçe aktarılıyor…' : 'Onayla ve Yükle'}
          </button>
        )}
      </form>
      <p className="muted" style={{ marginTop: 6, fontSize: 12.5 }}>
        Araç modeli seçersen dosyadaki ARAÇ sütunu (varsa) yok sayılır, TÜM satırlar seçtiğin
        modele uygulanır — ARAÇ sütunu olmayan, tek modele özel dosyalar için kullanışlıdır. Boş
        bırakırsan (varsayılan) her satırın modeli dosyadaki ARAÇ sütunundan otomatik okunur.
      </p>
      {error && <div className="error">{error}</div>}
      {result && (
        <div className="muted" style={{ marginTop: 10 }}>
          {result.dry_run && (
            <p
              style={{
                background: 'var(--amber-bg, #fef3c7)',
                color: 'var(--amber, #92400e)',
                fontWeight: 600,
                padding: '6px 10px',
                borderRadius: 6,
                marginBottom: 8,
              }}
            >
              Bu bir önizleme — henüz hiçbir şey kaydedilmedi. Sonuçlar uygunsa "Onayla ve Yükle"
              ile gerçek yüklemeyi yap.
            </p>
          )}
          <p>
            Araç modelleri: <strong>{result.vehicle_models.join(', ') || '—'}</strong> ·{' '}
            {result.plans_created} yeni plan, {result.plans_updated} güncellenen plan,{' '}
            {result.nodes_created} düğüm ({result.leaves_created} madde) oluşturuldu.
          </p>
          {result.unresolved_period.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <span style={{ color: 'var(--blue, #1b4da0)', fontWeight: 600 }}>
                {result.unresolved_period.length} satırda periyot belirsiz — atlanmadı, "Periyodu
                Belirsiz (?)" altında ağaca eklendi, kaynak netleşene kadar "?" olarak kalacak:
              </span>
              <ul style={{ margin: '6px 0 0 18px', padding: 0 }}>
                {result.unresolved_period.map((s, i) => (
                  <li key={i} style={{ fontSize: 12.5 }}>
                    Satır {s.row}: {s.reason}
                    {s.data.length > 0 && (
                      <span> — {s.data.filter(Boolean).slice(0, 5).join(' / ')}</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {result.catalog_only_periods && result.catalog_only_periods.length > 0 && (() => {
            const rows = result.catalog_only_periods!
            return (
              <div style={{ marginTop: 8 }}>
                <span style={{ color: 'var(--green, #1e7a46)', fontWeight: 600 }}>
                  {rows.length} periyot bir temel bakımın katı olduğu için "katalog-amaçlı"
                  işaretlendi — Bakım Planları sayfasında görünmeyecek, kendi başına bakım
                  açmayacak (temel bakım o katlarda zaten tetiklenir), ama Bakım Ağacı
                  sayfasında ("Tümü"/kendi periyodu seçilerek) görülebilir/düzenlenebilir:
                </span>
                <ul style={{ margin: '6px 0 0 18px', padding: 0 }}>
                  {rows.slice(0, 12).map((c, i) => (
                    <li key={i} style={{ fontSize: 12.5 }}>
                      {c.vehicle_model ? `[${c.vehicle_model}] ` : ''}{c.reason}
                    </li>
                  ))}
                  {rows.length > 12 && (
                    <li style={{ fontSize: 12.5 }}>…ve {rows.length - 12} tane daha</li>
                  )}
                </ul>
              </div>
            )
          })()}
          {result.skipped.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <span style={{ color: 'var(--amber, #b45309)', fontWeight: 600 }}>
                {result.skipped.length} satır tamamen atlandı (boş satır/alan):
              </span>
              <ul style={{ margin: '6px 0 0 18px', padding: 0 }}>
                {result.skipped.map((s, i) => (
                  <li key={i} style={{ fontSize: 12.5 }}>
                    Satır {s.row}: {s.reason}
                    {s.data.length > 0 && (
                      <span> — {s.data.filter(Boolean).slice(0, 5).join(' / ')}</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
        </>
      )}
    </div>
  )
}

// ---------------------------------------------------------- Araç modeli birleştirme
// Yanlış yazılmış/eski bir model adının (ör. Excel'den "BOZANKAYA" gelmiş,
// filodaki gerçek modelle "Bozankaya Tip-1" birebir eşleşmeyen kayıtlar)
// gerçek filo modeliyle tek elde toplanmasını sağlar — bkz.
// backend excel_utils.merge_vehicle_model.
function VehicleModelMergePanel({ knownModels, onMerged }: { knownModels: string[]; onMerged: () => void }) {
  const [open, setOpen] = useState(false)
  const [fromModel, setFromModel] = useState('')
  const [toModel, setToModel] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<VehicleModelMergeResult | null>(null)

  async function merge(e: React.FormEvent) {
    e.preventDefault()
    if (!fromModel.trim() || !toModel.trim() || fromModel.trim() === toModel.trim()) return
    if (
      !confirm(
        `"${fromModel}" modelindeki TÜM araçlar ve planlar "${toModel}" modeline taşınacak. Devam edilsin mi?`,
      )
    )
      return
    setBusy(true)
    setError('')
    setResult(null)
    try {
      const res = await api<VehicleModelMergeResult>('/vehicle-models/merge', {
        method: 'POST',
        body: { from_model: fromModel.trim(), to_model: toModel.trim() },
      })
      setResult(res)
      setFromModel('')
      onMerged()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (knownModels.length < 2) return null

  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="row" style={{ justifyContent: 'space-between', cursor: 'pointer' }} onClick={() => setOpen(!open)}>
        <strong>Araç Modeli Birleştir</strong>
        <button className="small" type="button" onClick={(e) => { e.stopPropagation(); setOpen(!open) }}>
          {open ? 'Gizle' : 'Göster'}
        </button>
      </div>
      {open && (
        <>
          <p className="muted" style={{ marginTop: 8, fontSize: 12.5 }}>
            Yanlış yazılmış ya da yinelenen bir model adını (ör. "BOZANKAYA") gerçek filo
            modeliyle (ör. "Bozankaya Tip-1") birleştirir — o modeldeki tüm araçlar taşınır,
            planlar mümkünse yeniden adlandırılır, çakışan periyotların tamamlanmış geçmişi
            korunarak hedef plana taşınır.
          </p>
          <form className="row" onSubmit={merge} style={{ flexWrap: 'wrap', marginTop: 8 }}>
            <select value={fromModel} onChange={(e) => setFromModel(e.target.value)} style={{ minWidth: 200 }}>
              <option value="">Kaynak model seçin…</option>
              {knownModels.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
            <span className="muted">→</span>
            <select value={toModel} onChange={(e) => setToModel(e.target.value)} style={{ minWidth: 200 }}>
              <option value="">Hedef model seçin…</option>
              {knownModels.filter((m) => m !== fromModel).map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
            <button
              className="primary"
              disabled={!fromModel.trim() || !toModel.trim() || fromModel.trim() === toModel.trim() || busy}
            >
              {busy ? 'Birleştiriliyor…' : 'Birleştir'}
            </button>
          </form>
          {error && <div className="error" style={{ marginTop: 8 }}>{error}</div>}
          {result && (
            <p className="muted" style={{ marginTop: 8, fontSize: 12.5 }}>
              {result.vehicles_moved} araç taşındı, {result.plans_renamed.length} plan yeniden
              adlandırıldı{result.plans_merged.length > 0 && `, ${result.plans_merged.length} plan çakışma nedeniyle birleştirildi (tamamlanmış geçmiş korundu)`}.
            </p>
          )}
        </>
      )}
    </div>
  )
}

export default function TreeEditorPage({ user }: { user: User }) {
  const isAdmin = user.role === 'admin'
  const [plans, setPlans] = useState<Plan[]>([])
  const [planId, setPlanId] = useState<number | null>(null)
  const [tree, setTree] = useState<TreeNode[]>([])
  const [flatNodes, setFlatNodes] = useState<TreeNodeFlat[]>([])
  const [newRoot, setNewRoot] = useState('')
  const [modelFilter, setModelFilter] = useState('')
  const [merged, setMerged] = useState<MergedTreeNode[] | null>(null)
  const [showAll, setShowAll] = useState(false)
  const [knownModels, setKnownModels] = useState<string[]>([])
  const [knownParts, setKnownParts] = useState<{ id: number; label: string }[]>([])

  // Seçili planın TÜM ağacını (tek tek kök bölüm silmek yerine) bir kerede
  // boşaltma — plan kaydı ve geçmiş iş emirleri korunur, yalnızca checklist
  // içeriği silinir.
  const [clearTreeBusy, setClearTreeBusy] = useState(false)
  // "Tüm Araçlardan Tüm Ağaçları Sil" — Tehlikeli Bölge (yazarak onay gerekir).
  const [wipeOpen, setWipeOpen] = useState(false)
  const [wipeConfirmText, setWipeConfirmText] = useState('')
  const [wipeBusy, setWipeBusy] = useState(false)
  const [wipeError, setWipeError] = useState('')

  const loadKnownModels = () =>
    api<string[]>('/vehicle-models').then(setKnownModels).catch(() => setKnownModels([]))
  useEffect(() => {
    loadKnownModels()
    api<PartCatalogItem[]>('/parts').then((ps) => setKnownParts(flattenParts(ps))).catch(() => setKnownParts([]))
  }, [])

  // include_catalog_only=true: bir temel periyodun katı olan (ör. 800.000
  // km) planlar da dahil TÜMÜ gelsin — bunlar Bakım Planları sayfasında
  // gizlense de, kaynak katalogdaki içerik burada kaybolmasın diye görünür
  // kalmalı.
  const loadPlans = (selectId?: number, selectModel?: string) =>
    api<Plan[]>('/plans?include_catalog_only=true').then((ps) => {
      setPlans(ps)
      if (selectModel !== undefined) setModelFilter(selectModel)
      if (selectId !== undefined && ps.some((p) => p.id === selectId)) {
        setPlanId(selectId)
      } else if (planId === null && ps.length > 0) {
        setPlanId(ps[0].id)
      }
    })

  useEffect(() => {
    loadPlans()
  }, [])

  // Filodaki araç modelleri arttıkça planları model bazında ayırmak için:
  // her plan "Genel" (vehicle_model="") veya belirli bir modele aittir.
  const planModelOptions = useMemo(() => {
    const set = new Set<string>()
    for (const p of plans) set.add(p.vehicle_model || '')
    return Array.from(set).sort((a, b) => a.localeCompare(b, 'tr'))
  }, [plans])

  // "Genel (mevcut filo)" seçiliyken bu, filodaki tüm araçların/tüm
  // modellerin bakımlarını gösteren bir "hepsi bir arada" görünüme
  // dönüşür — yalnızca vehicle_model="" olan planlar değil, TÜM planlar
  // listelenir (her birinin yanında hangi modele ait olduğu gösterilir,
  // bkz. aşağıdaki <option> render'ı). Belirli bir model seçiliyse
  // (Sirio, Bozankaya Tip-1 vb.) yalnızca o modelin planları gösterilir.
  //
  // is_catalog_only planlar (ör. 1.500.000 km — 500.000 km'nin katı) bu
  // seçici listede AYRI bir madde olarak GÖSTERİLMEZ — zaten temel periyot
  // (ör. 500.000 km Bakımı) o katta da otomatik tetiklenir, listede iki kez
  // (hem 500.000 hem 1.500.000 olarak) görünmesi kafa karıştırır. İçerikleri
  // yine de "Tümü" (birleşik görünüm) içinde, periyot etiketiyle görülebilir.
  const visiblePlans = useMemo(() => {
    const base = modelFilter === '' ? plans : plans.filter((p) => (p.vehicle_model || '') === modelFilter)
    return base.filter((p) => !p.is_catalog_only)
  }, [plans, modelFilter])

  // Seçili model değiştiğinde veya o modelde artık plan kalmadığında,
  // görünür planlardan ilkine geç ("Tümü" seçimini de sıfırla).
  useEffect(() => {
    setShowAll(false)
    if (visiblePlans.length === 0) {
      setPlanId(null)
    } else if (!visiblePlans.some((p) => p.id === planId)) {
      setPlanId(visiblePlans[0].id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelFilter, plans])

  const load = () => {
    if (planId === null || showAll) return
    api<TreeNode[]>(`/tree?plan_id=${planId}`).then(setTree).catch(() => setTree([]))
    // /tree/flat yalnızca 3D model eşleştirme seçicisi (admin-only) için
    // gerekli — bu uç nokta admin'e özel olduğundan mühendis/amir için
    // isteği hiç yapmıyoruz (403'ten kaçınmak için).
    if (isAdmin) {
      api<TreeNodeFlat[]>(`/tree/flat?plan_id=${planId}`).then(setFlatNodes).catch(() => setFlatNodes([]))
    }
  }
  useEffect(load, [planId, showAll])

  // "Tümü" (birleşik görünüm): o modeldeki (ya da "Genel (mevcut filo)"
  // seçiliyken TÜM modellerin — all_models=true) planlarının ağaçlarını,
  // her maddenin yanında periyot (ve tüm-modeller modunda araç modeli)
  // etiketiyle birlikte birleştirip gösteren salt-okunur görünüm.
  useEffect(() => {
    if (!showAll) return
    const qs = modelFilter === '' ? 'all_models=true' : `vehicle_model=${encodeURIComponent(modelFilter)}`
    api<MergedTreeNode[]>(`/tree/merged?${qs}`)
      .then(setMerged)
      .catch(() => setMerged([]))
  }, [showAll, modelFilter])

  async function addRoot(e: React.FormEvent) {
    e.preventDefault()
    if (!newRoot.trim() || planId === null) return
    await api('/tree', { method: 'POST', body: { name: newRoot.trim(), plan_id: planId } })
    setNewRoot('')
    load()
  }

  const currentPlan = visiblePlans.find((p) => p.id === planId) ?? plans.find((p) => p.id === planId) ?? null

  // Seçili planın TÜM ağacını (her kök bölümü tek tek silmek yerine) bir
  // kerede boşaltır — plan kaydı ve geçmiş iş emirleri korunur.
  async function clearCurrentPlanTree() {
    if (planId === null || !currentPlan) return
    if (
      !confirm(
        `"${currentPlan.name}" (${currentPlan.vehicle_model || 'Genel'}) planının TÜM ağacı (tüm bölümler/maddeler) silinsin mi? Bu işlem geri alınamaz.`,
      )
    )
      return
    setClearTreeBusy(true)
    try {
      await api(`/tree/plan/${planId}`, { method: 'DELETE' })
      load()
    } finally {
      setClearTreeBusy(false)
    }
  }

  // Tehlikeli Bölge: TÜM planlardaki/araç modellerindeki bakım ağaçlarını
  // tek seferde tamamen boşaltır (planların kendisi/geçmiş iş emirleri
  // korunur). Yanlışlıkla tıklamayı önlemek için "SİL" yazarak onay gerekir.
  async function wipeAllTrees() {
    if (wipeConfirmText.trim().toUpperCase() !== 'SİL') return
    setWipeBusy(true)
    setWipeError('')
    try {
      await api('/tree', { method: 'DELETE' })
      setWipeOpen(false)
      setWipeConfirmText('')
      load()
      loadPlans()
    } catch (e) {
      setWipeError((e as Error).message)
    } finally {
      setWipeBusy(false)
    }
  }

  const all = flatten(tree)

  return (
    <>
      <PageHeader title="Bakım Ağacı Şablonu" />

      {isAdmin && <BulkCatalogImportSection knownModels={knownModels} onImported={() => loadPlans()} />}

      <div className="card">
        <div className="row" style={{ flexWrap: 'wrap' }}>
          <strong>Araç Modeli:</strong>
          <select
            value={modelFilter}
            onChange={(e) => setModelFilter(e.target.value)}
            style={{ minWidth: 200 }}
          >
            {planModelOptions.map((m) => (
              <option key={m} value={m}>
                {m || 'Genel (mevcut filo)'}
              </option>
            ))}
            {!planModelOptions.includes(modelFilter) && (
              <option value={modelFilter}>{modelFilter || 'Genel (mevcut filo)'}</option>
            )}
          </select>
          <strong style={{ marginLeft: 12 }}>Bakım Planı:</strong>
          <select
            value={showAll ? ALL_PLANS_VALUE : planId ?? ''}
            onChange={(e) => {
              if (e.target.value === ALL_PLANS_VALUE) {
                setShowAll(true)
              } else {
                setShowAll(false)
                setPlanId(Number(e.target.value))
              }
            }}
            style={{ minWidth: 260 }}
          >
            {visiblePlans.length > 0 && (
              <option value={ALL_PLANS_VALUE}>
                {modelFilter === '' ? 'Tümü (tüm modeller, birleşik görünüm)' : 'Tümü (birleşik görünüm)'}
              </option>
            )}
            {visiblePlans.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({planPeriodLabel(p)})
                {modelFilter === '' ? ` — ${p.vehicle_model || 'Genel'}` : ''}
              </option>
            ))}
          </select>
          {visiblePlans.length === 0 && (
            <span className="muted">
              Bu modele ait plan yok — yukarıda "Toplu Bakım Kataloğu İçe Aktar" ile Excel
              yükleyin ya da "Bakım Planları" sayfasından elle oluşturun.
            </span>
          )}
        </div>
      </div>

      {isAdmin && (
        <VehicleModelMergePanel
          knownModels={knownModels}
          onMerged={() => {
            loadKnownModels()
            loadPlans()
          }}
        />
      )}

      {showAll ? (
        <div className="card">
          {merged === null && <div className="muted">Yükleniyor…</div>}
          {merged !== null && merged.length === 0 && (
            <div className="muted">Bu model için henüz hiçbir planda bölüm yok.</div>
          )}
          {merged !== null && merged.length > 0 && (
            <ul className="tree">
              {merged.map((n) => (
                <MergedNodeView key={n.id} node={n} />
              ))}
            </ul>
          )}
        </div>
      ) : (
        planId !== null && (
          <>
            {isAdmin && (
              <div className="card">
                <form className="row" onSubmit={addRoot}>
                  <input
                    placeholder="Yeni ana bölüm (ör. Araç İçi)"
                    value={newRoot}
                    onChange={(e) => setNewRoot(e.target.value)}
                  />
                  <button className="primary">Ana Bölüm Ekle</button>
                  {tree.length > 0 && (
                    <button
                      type="button"
                      className="small danger"
                      style={{ marginLeft: 'auto' }}
                      disabled={clearTreeBusy}
                      onClick={clearCurrentPlanTree}
                      title="Bu planın tüm ağacını (tüm bölüm/maddeleri) siler — plan kaydı ve geçmiş iş emirleri korunur"
                    >
                      <Trash2 size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                      {clearTreeBusy ? 'Siliniyor…' : 'Bu Planın Ağacını Tamamen Sil'}
                    </button>
                  )}
                </form>
              </div>
            )}
            <div className="card">
              <ul className="tree">
                {tree.map((n) => (
                  <EditorNode key={n.id} node={n} onChanged={load} allNodes={all} knownParts={knownParts} isAdmin={isAdmin} />
                ))}
              </ul>
              {tree.length === 0 && (
                <div className="muted">
                  {isAdmin
                    ? 'Bu plan için henüz bölüm yok. Yukarıdan ekleyin.'
                    : 'Bu plan için henüz bölüm yok.'}
                </div>
              )}
            </div>
            {isAdmin && <Model3DSection planId={planId} flatNodes={flatNodes} />}
          </>
        )
      )}

      {isAdmin && (
      <div className="card" style={{ marginTop: 20, borderColor: 'var(--red)', background: 'var(--red-light)' }}>
        <div className="card-head">
          <div>
            <div className="card-title" style={{ color: 'var(--red)' }}>
              <ShieldAlert size={15} style={{ marginRight: 6, verticalAlign: -3 }} />
              Tehlikeli Bölge
            </div>
            <div className="card-subtitle">
              Tüm araç modellerindeki TÜM bakım ağaçları (tüm bölüm/maddeler) kalıcı olarak
              silinir. Planların kendisi ve geçmiş iş emirleri korunur — yalnızca checklist
              içeriği boşaltılır. Bu işlem geri alınamaz.
            </div>
          </div>
        </div>
        {wipeError && <div className="error">{wipeError}</div>}
        {!wipeOpen ? (
          <button className="small danger" onClick={() => setWipeOpen(true)}>
            <Trash2 size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
            Tüm Araçlardan Tüm Ağaçları Sil
          </button>
        ) : (
          <div className="row" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
            <span style={{ fontSize: 13 }}>
              Onaylamak için kutuya <strong>SİL</strong> yazın:
            </span>
            <input
              style={{ width: 100 }}
              value={wipeConfirmText}
              onChange={(e) => setWipeConfirmText(e.target.value)}
              placeholder="SİL"
              autoFocus
            />
            <button
              className="small danger"
              disabled={wipeBusy || wipeConfirmText.trim().toUpperCase() !== 'SİL'}
              onClick={wipeAllTrees}
            >
              {wipeBusy ? 'Siliniyor…' : 'Kalıcı Olarak Sil'}
            </button>
            <button
              className="small"
              type="button"
              onClick={() => { setWipeOpen(false); setWipeConfirmText(''); setWipeError('') }}
            >
              Vazgeç
            </button>
          </div>
        )}
      </div>
      )}
    </>
  )
}
