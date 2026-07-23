import { useEffect, useMemo, useState } from 'react'
import { api, apiUploadWithFields } from '../api'
import { PageHeader } from '../components/ui'
import { ChevronRight, Paperclip, FileText, ImageIcon, LinkIcon, Download, Trash2 } from 'lucide-react'
import Tram3D from '../components/Tram3D'
import type {
  ImportTreeResult,
  Model3D,
  Plan,
  TreeNode,
  TreeNodeFlat,
  PartHotspot,
  NodeAttachment,
} from '../types'

// ---------------------------------------------------------- Madde bilgi/ek paneli (Faz 10)
function NodeInfoPanel({ node, onChanged }: { node: TreeNode; onChanged: () => void }) {
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

function EditorNode({
  node,
  onChanged,
  allNodes,
}: {
  node: TreeNode
  onChanged: () => void
  allNodes: { node: TreeNode; depth: number }[]
}) {
  const [open, setOpen] = useState(true)
  const [adding, setAdding] = useState(false)
  const [newName, setNewName] = useState('')
  const [renaming, setRenaming] = useState(false)
  const [name, setName] = useState(node.name)
  const [moving, setMoving] = useState(false)
  const [showInfo, setShowInfo] = useState(false)
  const attCount = (node.attachments?.length ?? 0) + (node.note ? 1 : 0)

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
          <span className="node-name">{node.name}</span>
        )}
        {!renaming && (
          <span className="row" style={{ marginLeft: 'auto' }}>
            <button className="small" onClick={() => setAdding(!adding)}>+ Alt Madde</button>
            <button
              className={`small ${showInfo ? 'primary' : ''}`}
              onClick={() => setShowInfo(!showInfo)}
              title="Not / fotoğraf / PDF / talimat ekle"
            >
              <Paperclip size={12} style={{ marginRight: 4, verticalAlign: -2 }} />
              Bilgi/Ek{attCount > 0 ? ` (${attCount})` : ''}
            </button>
            <button className="small" onClick={() => setRenaming(true)}>Yeniden Adlandır</button>
            <button className="small" onClick={() => setMoving(!moving)}>Taşı</button>
            <button className="small danger" onClick={remove}>Sil</button>
          </span>
        )}
      </div>
      {showInfo && <NodeInfoPanel node={node} onChanged={onChanged} />}
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
      {!isLeaf && open && (
        <ul>
          {node.children.map((c) => (
            <EditorNode key={c.id} node={c} onChanged={onChanged} allNodes={allNodes} />
          ))}
        </ul>
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

// ---------------------------------------------------------- Excel ile yeni tablo/plan oluşturma
function ExcelImportSection({
  knownModels,
  onImported,
}: {
  knownModels: string[]
  onImported: (planIds: number[], vehicleModel: string) => void
}) {
  const [file, setFile] = useState<File | null>(null)
  const [vehicleModel, setVehicleModel] = useState('')
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<ImportTreeResult | null>(null)

  async function upload(e: React.FormEvent) {
    e.preventDefault()
    if (!file) return
    setUploading(true)
    setError('')
    setResult(null)
    try {
      const res = await apiUploadWithFields<ImportTreeResult>('/tree/import-excel', file, {
        vehicle_model: vehicleModel.trim(),
      })
      setResult(res)
      setFile(null)
      const input = document.getElementById('tree-excel-input') as HTMLInputElement | null
      if (input) input.value = ''
      onImported(res.plans.map((p) => p.id), vehicleModel.trim())
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="card">
      <h2 style={{ fontSize: 16, marginBottom: 6 }}>Excel ile Yeni Bakım Tablosu Oluştur</h2>
      <p className="muted" style={{ marginBottom: 12 }}>
        Farklı araç modelleri için ayrı bakım ağaçları tanımlayabilirsiniz. Araç modeli alanını
        boş bırakırsanız plan "Genel" sayılır ve modelinden bağımsız TÜM araçlara uygulanır. Bir
        model adı girerseniz (ör. "CAF Urbos 100"), bu Excel'den oluşan planlar yalnızca o
        modeldeki araçlara uygulanır — aynı model adıyla tekrar yüklemek o modelin ağacını
        tamamen günceller (üzerine yazar), diğer modellere dokunmaz.
      </p>
      <form className="row" onSubmit={upload} style={{ flexWrap: 'wrap' }}>
        <input
          placeholder="Araç modeli (boş = Genel / mevcut filo)"
          value={vehicleModel}
          onChange={(e) => setVehicleModel(e.target.value)}
          list="tree-known-models"
          style={{ minWidth: 220 }}
        />
        <datalist id="tree-known-models">
          {knownModels.map((m) => (
            <option key={m} value={m} />
          ))}
        </datalist>
        <input
          id="tree-excel-input"
          type="file"
          accept=".xlsx,.xls"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <button className="primary" disabled={!file || uploading}>
          {uploading ? 'İçe aktarılıyor…' : 'Excel Yükle ve Tablo Oluştur'}
        </button>
      </form>
      {error && <div className="error">{error}</div>}
      {result && (
        <p className="muted" style={{ marginTop: 10 }}>
          {result.plans.length} plan, {result.nodes_created} düğüm oluşturuldu/güncellendi (
          {vehicleModel.trim() || 'Genel'}):{' '}
          {result.plans.map((p) => p.name).join(', ')}
          {result.maintenances_reset > 0 && (
            <>
              {' — '}
              <span style={{ color: 'var(--amber, #b45309)', fontWeight: 600 }}>
                {result.maintenances_reset} açık iş emri sıfırlandı
              </span>
              , güncel ağaçtan baştan açılacaklar.
            </>
          )}
        </p>
      )}
    </div>
  )
}

export default function TreeEditorPage() {
  const [plans, setPlans] = useState<Plan[]>([])
  const [planId, setPlanId] = useState<number | null>(null)
  const [tree, setTree] = useState<TreeNode[]>([])
  const [flatNodes, setFlatNodes] = useState<TreeNodeFlat[]>([])
  const [newRoot, setNewRoot] = useState('')
  const [modelFilter, setModelFilter] = useState('')
  const [knownModels, setKnownModels] = useState<string[]>([])

  useEffect(() => {
    api<string[]>('/vehicle-models').then(setKnownModels).catch(() => setKnownModels([]))
  }, [])

  const loadPlans = (selectId?: number, selectModel?: string) =>
    api<Plan[]>('/plans').then((ps) => {
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

  const visiblePlans = useMemo(
    () => plans.filter((p) => (p.vehicle_model || '') === modelFilter),
    [plans, modelFilter],
  )

  // Seçili model değiştiğinde veya o modelde artık plan kalmadığında,
  // görünür planlardan ilkine geç.
  useEffect(() => {
    if (visiblePlans.length === 0) {
      setPlanId(null)
    } else if (!visiblePlans.some((p) => p.id === planId)) {
      setPlanId(visiblePlans[0].id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelFilter, plans])

  const load = () => {
    if (planId === null) return
    api<TreeNode[]>(`/tree?plan_id=${planId}`).then(setTree).catch(() => setTree([]))
    api<TreeNodeFlat[]>(`/tree/flat?plan_id=${planId}`).then(setFlatNodes).catch(() => setFlatNodes([]))
  }
  useEffect(load, [planId])

  async function addRoot(e: React.FormEvent) {
    e.preventDefault()
    if (!newRoot.trim() || planId === null) return
    await api('/tree', { method: 'POST', body: { name: newRoot.trim(), plan_id: planId } })
    setNewRoot('')
    load()
  }

  const all = flatten(tree)

  return (
    <>
      <PageHeader
        title="Bakım Ağacı Şablonu"
        subtitle="Her bakım planının kendi bakım ağacı vardır. Bu şablon, o plana göre yeni açılan her bakıma kopyalanır; değişiklikler zaten açık olan bakımları etkilemez. Her seviyede sınırsız ekleme, yeniden adlandırma, taşıma ve silme yapabilirsiniz."
      />

      <ExcelImportSection
        knownModels={knownModels}
        onImported={(ids, vehicleModel) => loadPlans(ids[0], vehicleModel)}
      />

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
            value={planId ?? ''}
            onChange={(e) => setPlanId(Number(e.target.value))}
            style={{ minWidth: 220 }}
          >
            {visiblePlans.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({fmtKm(p.interval_km)} km)
              </option>
            ))}
          </select>
          {visiblePlans.length === 0 && (
            <span className="muted">
              Bu modele ait plan yok — yukarıdan bu model için Excel yükleyin ya da "Bakım
              Planları" sayfasından elle oluşturun.
            </span>
          )}
        </div>
      </div>

      {planId !== null && (
        <>
          <div className="card">
            <form className="row" onSubmit={addRoot}>
              <input
                placeholder="Yeni ana bölüm (ör. Araç İçi)"
                value={newRoot}
                onChange={(e) => setNewRoot(e.target.value)}
              />
              <button className="primary">Ana Bölüm Ekle</button>
            </form>
          </div>
          <div className="card">
            <ul className="tree">
              {tree.map((n) => (
                <EditorNode key={n.id} node={n} onChanged={load} allNodes={all} />
              ))}
            </ul>
            {tree.length === 0 && (
              <div className="muted">Bu plan için henüz bölüm yok. Yukarıdan ekleyin.</div>
            )}
          </div>
          <Model3DSection planId={planId} flatNodes={flatNodes} />
        </>
      )}
    </>
  )
}
