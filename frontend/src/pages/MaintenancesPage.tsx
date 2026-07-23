import { useCallback, useEffect, useRef, useState } from 'react'
import { api, apiDownload } from '../api'
import Tram3D from '../components/Tram3D'
import type { ExportFormat, Item, Maintenance, MaintenanceNote, Model3DWithStatus, NodeAttachment, Shift, ShiftModeConfig, User } from '../types'
import { STATUS_TR } from '../types'
import {
  PageHeader,
  Badge,
  Avatar,
  EmptyState,
  SkeletonTableRows,
} from '../components/ui'
import {
  ArrowLeft,
  ChevronRight,
  Lock,
  LockOpen,
  ListFilter,
  Wrench,
  Download,
  FileText,
  ImageIcon,
  LinkIcon,
  StickyNote,
  UserPlus,
  UserMinus,
  Send,
  AlertTriangle,
  Users,
} from 'lucide-react'

// Paylaşılan tablet üzerinde vardiya seçimi, aynı tarayıcıda sayfa
// yenilense/gezinilse de hatırlansın diye localStorage'da tutulur.
const ACTIVE_SHIFT_STORAGE_KEY = 'ulasim_bakim_active_shift_id'

// ---------------------------------------------------------- Madde ekleri (Faz 10)
function AttachmentChip({ att }: { att: NodeAttachment }) {
  const href = att.kind === 'link' ? att.url : `/uploads/attachments/${att.filename}`
  const icon =
    att.kind === 'image' ? <ImageIcon size={13} /> :
    att.kind === 'pdf' ? <FileText size={13} /> :
    att.kind === 'link' ? <LinkIcon size={13} /> : <Download size={13} />
  const text = att.label || att.original_name || att.url || 'Ek'
  return (
    <a className="attach-chip" href={href} target="_blank" rel="noreferrer" title={text}>
      {icon}
      <span>{text}</span>
    </a>
  )
}

function ItemExtras({ item }: { item: Item }) {
  const hasNote = !!item.note
  const images = item.attachments.filter((a) => a.kind === 'image')
  const others = item.attachments.filter((a) => a.kind !== 'image')
  if (!hasNote && item.attachments.length === 0) return null
  return (
    <div className="item-extras">
      {hasNote && (
        <div className="item-note">
          <StickyNote size={13} />
          <span>{item.note}</span>
        </div>
      )}
      {images.length > 0 && (
        <div className="attach-thumbs">
          {images.map((a) => (
            <a
              key={a.id}
              href={`/uploads/attachments/${a.filename}`}
              target="_blank"
              rel="noreferrer"
              title={a.label || a.original_name}
            >
              <img src={`/uploads/attachments/${a.filename}`} alt={a.label || a.original_name} />
            </a>
          ))}
        </div>
      )}
      {others.length > 0 && (
        <div className="attach-chips">
          {others.map((a) => (
            <AttachmentChip key={a.id} att={a} />
          ))}
        </div>
      )}
    </div>
  )
}

const fmt = (s: string | null) => (s ? new Date(s).toLocaleString('tr-TR') : '—')
const fmtKm = (n: number) => n.toLocaleString('tr-TR')
const fmtDate = (d: Date) => d.toLocaleDateString('tr-TR')

// Takvim/Dashboard'daki projeksiyonlarla aynı varsayım: günde ortalama 300 km.
const DAILY_KM = 300

/** Bir iş emri için tahmini bakım PENCERESİ — planın alt (-%lower) ve üst
 * (+%upper) tolerans sınırlarının, aracın güncel km'sinden günde ortalama
 * DAILY_KM ilerlediği varsayımıyla kaç gün sonra geleceğini hesaplar.
 * "En erken" tarih, aracın alt tolerans sınırına ulaşacağı (bakıma
 * başlanabileceği) tarihtir; "en geç" tarih üst tolerans sınırının
 * (planın otomatik açma penceresinin sonu) tarihidir. Yalnızca henüz
 * tamamlanmamış iş emirleri için anlamlıdır. */
function estimateWindow(
  m: Maintenance,
): { earliest: Date; latest: Date; earliestReached: boolean; overdue: boolean } | null {
  if (m.status === 'completed') return null
  const lowerKm = m.target_km - (m.plan.interval_km * m.plan.lower_pct) / 100
  const upperKm = m.target_km + (m.plan.interval_km * m.plan.upper_pct) / 100
  const remainingLower = lowerKm - m.vehicle.current_km
  const remainingUpper = upperKm - m.vehicle.current_km
  const earliestDays = Math.max(0, Math.ceil(remainingLower / DAILY_KM))
  const latestDays = Math.max(0, Math.ceil(remainingUpper / DAILY_KM))
  const earliest = new Date()
  earliest.setDate(earliest.getDate() + earliestDays)
  const latest = new Date()
  latest.setDate(latest.getDate() + latestDays)
  return { earliest, latest, earliestReached: remainingLower <= 0, overdue: remainingUpper <= 0 }
}

function countLeaves(items: Item[]): [number, number] {
  let done = 0
  let total = 0
  const walk = (list: Item[]) => {
    for (const i of list) {
      if (i.children.length === 0) {
        total++
        if (i.is_completed) done++
      } else walk(i.children)
    }
  }
  walk(items)
  return [done, total]
}

// ---------------------------------------------------------- Ağaç düğümü
function TreeItem({
  item,
  canEdit,
  canUnlock,
  onComplete,
  onUnlock,
  onEditNote,
  highlightId,
  showTierBadges,
  expandIds,
}: {
  item: Item
  canEdit: boolean
  canUnlock: boolean
  onComplete: (id: number) => void
  onUnlock: (id: number) => void
  onEditNote: (id: number) => void
  highlightId?: number | null
  showTierBadges?: boolean
  // Notlu Maddeler panelinden bir maddeye atlanınca, ona giden yoldaki tüm
  // üst başlıkların id'lerini taşır — bu düğümler tek tek elle açılmadan
  // otomatik genişler ki madde tek tek alt kısımlara girmeden görülebilsin.
  expandIds?: Set<number>
}) {
  const [open, setOpen] = useState(false)
  const isLeaf = item.children.length === 0
  const rowRef = useRef<HTMLDivElement>(null)
  const isHighlighted = highlightId === item.id
  const forcedOpen = expandIds?.has(item.id) ?? false
  const effectiveOpen = open || forcedOpen
  const [branchDone, branchTotal] = isLeaf ? [0, 0] : countLeaves(item.children)

  useEffect(() => {
    if (isHighlighted) {
      rowRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      setOpen(true)
    }
  }, [isHighlighted])

  return (
    <li className="tree-node" id={`item-${item.id}`}>
      <div
        ref={rowRef}
        className={`tree-row ${isLeaf ? 'leaf' : ''} ${item.is_completed ? 'done' : ''} ${isHighlighted ? 'flash' : ''}`}
      >
        {isLeaf ? (
          <input
            type="checkbox"
            className="tree-checkbox"
            checked={item.is_completed}
            disabled={item.is_locked || !canEdit}
            onChange={() => onComplete(item.id)}
            title={
              item.is_locked
                ? 'Kilitli — onay yetkisi olan açabilir'
                : !canEdit
                  ? 'Bakım maddelerini işaretleme yetkiniz yok'
                  : 'Tamamla'
            }
          />
        ) : (
          <button className={`toggle ${effectiveOpen ? 'expanded' : ''}`} onClick={() => setOpen(!effectiveOpen)}>
            <ChevronRight size={15} />
          </button>
        )}
        <span className="node-name">{item.name}</span>
        {isLeaf && showTierBadges && item.tier_kms && item.tier_kms.length > 0 && (
          <span className="tier-badges">
            {item.tier_kms.map((km) => (
              <span key={km} className="tier-badge" title={`${(km / 1000).toLocaleString('tr-TR')} bin km bakımında da var`}>
                {km >= 1000 ? `${km / 1000}b` : km}
              </span>
            ))}
          </span>
        )}
        {item.is_locked && (
          <span className="lock-pill">
            <Lock size={11} /> Kilitli
          </span>
        )}
        {!isLeaf && branchTotal > 0 && (
          <span className="branch-progress">
            <span className="progressbar tone-green"><div style={{ width: `${(branchDone / branchTotal) * 100}%` }} /></span>
            <span className="pct">{branchDone}/{branchTotal}</span>
          </span>
        )}
        {item.exec_note && (
          <span
            className="note-warn-pill"
            title={`Not (${item.exec_note_by?.full_name ?? 'Bilinmiyor'} · ${fmt(item.exec_note_at)}): ${item.exec_note}`}
          >
            <AlertTriangle size={12} /> Not var
          </span>
        )}
        {item.completed_by && (
          <span className="who">
            <Avatar name={item.completed_by.full_name} size={20} />
            {item.completed_by.full_name} · {fmt(item.completed_at)}
          </span>
        )}
        {isLeaf && canEdit && (
          <button
            className="small ghost"
            title={item.exec_note ? 'Notu düzenle' : 'Not ekle'}
            onClick={() => onEditNote(item.id)}
          >
            <StickyNote size={12} style={{ marginRight: 4, verticalAlign: -2 }} />
            {item.exec_note ? 'Notu Düzenle' : 'Not Ekle'}
          </button>
        )}
        {canUnlock && item.is_locked && (
          <button className="small" onClick={() => onUnlock(item.id)}>
            <LockOpen size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
            Kilidi Aç
          </button>
        )}
      </div>
      {item.exec_note && (
        <div className="item-note item-exec-note">
          <AlertTriangle size={13} />
          <span>
            {item.exec_note}
            <span className="muted" style={{ marginLeft: 6, fontSize: 11 }}>
              — {item.exec_note_by?.full_name ?? 'Bilinmiyor'} · {fmt(item.exec_note_at)}
            </span>
          </span>
        </div>
      )}
      <ItemExtras item={item} />
      {!isLeaf && (
        <div className={`tree-children ${effectiveOpen ? 'open' : ''}`}>
          <div className="tree-children-inner">
            <ul className="tree-sub">
              {item.children.map((c) => (
                <TreeItem
                  key={c.id}
                  item={c}
                  canEdit={canEdit}
                  canUnlock={canUnlock}
                  onComplete={onComplete}
                  onUnlock={onUnlock}
                  onEditNote={onEditNote}
                  highlightId={highlightId}
                  showTierBadges={showTierBadges}
                  expandIds={expandIds}
                />
              ))}
            </ul>
          </div>
        </div>
      )}
    </li>
  )
}

/** Kademeli bakımda her madde `tier_kms` ile hangi periyot(lar)a ait olduğunu
 * taşır. "Tümü" görünümünde birleşik (tekilleştirilmiş) ağaç; bir periyot
 * seçilince ağaç yalnızca o periyoda ait maddelere göre süzülür. */
function tierLabel(km: number): string {
  return `${km.toLocaleString('tr-TR')} km`
}

/** Ağaçta id'ye göre bir maddeyi bulur (not düzenleme diyaloğunu mevcut
 * metinle önceden doldurmak için kullanılır). */
function findItemById(items: Item[], itemId: number): Item | null {
  for (const it of items) {
    if (it.id === itemId) return it
    const found = findItemById(it.children, itemId)
    if (found) return found
  }
  return null
}

/** Bir maddenin, ağaçtaki ana başlıktan kendisine kadar olan üst
 * düğümlerinin id listesini döner (madde hariç) — "Notlu Maddeler"
 * panelinden bir maddeye atlanınca ilgili üst başlıkları otomatik açmak
 * için kullanılır. Bulunamazsa null döner. */
function findAncestorIds(items: Item[], targetId: number, trail: number[] = []): number[] | null {
  for (const it of items) {
    if (it.id === targetId) return trail
    const found = findAncestorIds(it.children, targetId, [...trail, it.id])
    if (found) return found
  }
  return null
}

interface NotedItemEntry {
  id: number
  path: string
  text: string
  byName: string
  at: string | null
}

/** Ağacı gezip usta/mühendis notu olan tüm maddeleri, hangi ana başlığın
 * hangi alt bölümünde olduklarını gösteren bir "yol" (breadcrumb) ile
 * birlikte düz bir listeye toplar — tek tek alt kısımlara girmeden hangi
 * maddede not olduğunu görebilmek için. */
function collectNotedItems(items: Item[], trail: string[] = []): NotedItemEntry[] {
  const out: NotedItemEntry[] = []
  for (const it of items) {
    if (it.exec_note) {
      out.push({
        id: it.id,
        path: [...trail, it.name].join(' > '),
        text: it.exec_note,
        byName: it.exec_note_by?.full_name ?? 'Bilinmiyor',
        at: it.exec_note_at,
      })
    }
    out.push(...collectNotedItems(it.children, [...trail, it.name]))
  }
  return out
}

/** Ağacı, yalnızca verilen periyoda (km) ait maddeleri bırakacak şekilde
 * süzer; alt maddesi eşleşen üst düğümler korunur. */
function filterByTier(items: Item[], km: number): Item[] {
  const out: Item[] = []
  for (const it of items) {
    const kids = filterByTier(it.children, km)
    const self = it.tier_kms?.includes(km)
    if (self || kids.length) out.push({ ...it, children: kids })
  }
  return out
}

// ---------------------------------------------------------- 3D Model görüntüleyici
function Model3DPanel({
  models,
  onPartClick,
}: {
  models: Model3DWithStatus[]
  onPartClick: (itemId: number) => void
}) {
  if (models.length === 0) return null
  return (
    <div className="card">
      <div className="card-head">
        <div>
          <div className="card-title">Tramvay 3D Modeli</div>
          <div className="card-subtitle">
            Yeşil parçalar tamamlanmış, turuncu parçalar bekleyen maddeleri gösterir. Bir parçaya
            tıklayarak ilgili kontrol maddesine gidin.
          </div>
        </div>
      </div>
      {models.map((mo) => {
        const tints: Record<string, string> = {}
        for (const hs of mo.hotspots) {
          tints[hs.hotspot.mesh_name] = hs.is_completed ? '#12805c' : '#b45309'
        }
        return (
          <div key={mo.id} style={{ marginBottom: 18 }}>
            {mo.label && <div className="muted" style={{ marginBottom: 6, fontWeight: 600 }}>{mo.label}</div>}
            <Tram3D
              src={`/uploads/models/${mo.filename}`}
              meshTints={tints}
              onMeshClick={(name) => {
                const hs = mo.hotspots.find((h) => h.hotspot.mesh_name === name)
                if (hs?.item_id) onPartClick(hs.item_id)
              }}
            />
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------- Bakım detayı
function MaintenanceDetail({
  id,
  user,
  perms,
  onBack,
  onDeleted,
  shiftModeEnabled,
  activeShiftId,
  activeShiftName,
  shiftMembersVersion,
}: {
  id: number
  user: User
  perms: Record<string, boolean>
  onBack: () => void
  onDeleted: () => void
  // Vardiyalı çalışma kapalıyken madde tamamlama/not ekleme seçici
  // göstermeden doğrudan oturum sahibine (user) atfedilir. Açıkken seçici
  // yalnızca activeShiftId'deki kişileri listeler.
  shiftModeEnabled: boolean
  activeShiftId: number | null
  activeShiftName: string | null
  // Vardiya üyeliği değişince (Bakımlar sayfasındaki vardiya barından üye
  // ekle/çıkar) isim seçici listesini hemen tazelemek için sayaç.
  shiftMembersVersion: number
}) {
  const [m, setM] = useState<Maintenance | null>(null)
  const [models3d, setModels3d] = useState<Model3DWithStatus[]>([])
  const [error, setError] = useState('')
  const [highlightId, setHighlightId] = useState<number | null>(null)
  // Notlu Maddeler panelinden bir maddeye atlanınca, o maddeye giden
  // yoldaki tüm üst başlıkları otomatik açık tutmak için id kümesi.
  const [expandIds, setExpandIds] = useState<Set<number>>(new Set())
  const [tierFilter, setTierFilter] = useState<number | 'all'>('all')
  const [deleting, setDeleting] = useState(false)
  const [assignableUsers, setAssignableUsers] = useState<User[]>([])
  const [assignBusy, setAssignBusy] = useState(false)
  // Vardiyalı çalışma AÇIKKEN "kim tikledi / kim not ekledi" seçicilerinde
  // yalnızca o an seçili vardiyadaki kişiler listelenir (assignableUsers'tan
  // ayrı tutulur — "Atanan" (iş emri sorumlusu) dropdown'u vardiyadan
  // bağımsız olarak her zaman herkesi gösterir).
  const [pickerUsers, setPickerUsers] = useState<User[]>([])
  // Paylaşılan tablet senaryosu: bir madde tiklendiğinde, tikleyen kişi
  // kendi adını seçer (oturum sahibinden bağımsız). Seçim yapılana kadar
  // madde tamamlanmaz; bu yüzden itemId burada "onay bekliyor" anlamına gelir.
  const [pickerItemId, setPickerItemId] = useState<number | null>(null)
  const [pickerUserId, setPickerUserId] = useState<number | ''>('')
  const [noteText, setNoteText] = useState('')
  const [noteAuthorId, setNoteAuthorId] = useState<number | ''>('')
  const [noteBusy, setNoteBusy] = useState(false)
  // Usta/mühendisin, bakım sırasında tikledikleri bir alt madde için
  // eklediği/düzenlediği not (madde şablonundaki admin notundan bağımsız).
  const [itemNoteId, setItemNoteId] = useState<number | null>(null)
  const [itemNoteText, setItemNoteText] = useState('')
  const [itemNoteAuthorId, setItemNoteAuthorId] = useState<number | ''>('')
  const [itemNoteBusy, setItemNoteBusy] = useState(false)
  const [itemNoteHadExisting, setItemNoteHadExisting] = useState(false)

  const isStaff = user.role === 'admin' || user.role === 'engineer' || user.role === 'amir'
  const isTechnician = user.role === 'technician'

  const load = useCallback(() => {
    api<Maintenance>(`/maintenances/${id}`).then(setM).catch((e) => setError(e.message))
    api<Model3DWithStatus[]>(`/maintenances/${id}/model3d`).then(setModels3d).catch(() => setModels3d([]))
  }, [id])

  useEffect(load, [load])

  useEffect(() => {
    // Teknisyenlerin de kendi aralarındaki isim listesini görebilmesi
    // gerekir ("Atanan" dropdown'u için — vardiyadan bağımsız, herkes).
    api<User[]>('/users/assignable').then(setAssignableUsers).catch(() => setAssignableUsers([]))
  }, [])

  useEffect(() => {
    // Vardiyalı çalışma kapalıyken bu listeye gerek yok (seçici zaten
    // gösterilmiyor). Açıkken, seçili vardiya değiştikçe yeniden çekilir.
    if (!shiftModeEnabled || activeShiftId == null) {
      setPickerUsers([])
      return
    }
    api<User[]>(`/users/assignable?shift_id=${activeShiftId}`)
      .then(setPickerUsers)
      .catch(() => setPickerUsers([]))
  }, [shiftModeEnabled, activeShiftId, shiftMembersVersion])

  async function assignTo(userId: number | null) {
    setError('')
    setAssignBusy(true)
    try {
      await api(`/maintenances/${id}/assign`, { method: 'PATCH', body: { assigned_to_id: userId } })
      load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setAssignBusy(false)
    }
  }

  // Vardiyalı çalışma KAPALIYKEN madde tamamlama doğrudan oturum sahibine
  // (user) atfedilir, seçici gösterilmez — paylaşılan tablette tek kişi
  // çalışıyorsa isim seçmeye gerek yoktur (backend zaten completed_by_id
  // verilmezse oturum sahibini kullanır). AÇIKKEN ise eskisi gibi bir
  // seçici açılır, ama yalnızca seçili vardiyadaki kişiler arasından;
  // tamamlanan madde kilitlendiği için bu seçim sonradan değiştirilemez.
  async function requestComplete(itemId: number) {
    setError('')
    if (!shiftModeEnabled) {
      try {
        await api(`/items/${itemId}/complete`, { method: 'POST' })
        load()
      } catch (e) {
        setError((e as Error).message)
      }
      return
    }
    if (activeShiftId == null) {
      setError('Lütfen önce üstteki listeden çalıştığınız vardiyayı seçin.')
      return
    }
    setPickerUserId('')
    setPickerItemId(itemId)
  }

  async function confirmComplete() {
    if (pickerItemId === null || pickerUserId === '') return
    setError('')
    try {
      await api(`/items/${pickerItemId}/complete`, {
        method: 'POST',
        body: { completed_by_id: pickerUserId },
      })
      setPickerItemId(null)
      load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  // Bir alt maddeye bakım sırasında not eklemek/düzenlemek için diyaloğu
  // açar; var olan notu (varsa) önceden doldurur. Vardiyalı çalışma
  // kapalıyken not sahibi otomatik oturum sahibidir, ayrıca seçilmez.
  function openItemNote(itemId: number) {
    setError('')
    const existing = m ? findItemById(m.items ?? [], itemId) : null
    setItemNoteText(existing?.exec_note ?? '')
    setItemNoteHadExisting(!!existing?.exec_note)
    setItemNoteAuthorId('')
    setItemNoteId(itemId)
  }

  async function saveItemNote() {
    if (itemNoteId === null) return
    if (shiftModeEnabled && itemNoteAuthorId === '') return
    setItemNoteBusy(true)
    setError('')
    try {
      await api(`/items/${itemNoteId}/note`, {
        method: 'PATCH',
        body: shiftModeEnabled
          ? { text: itemNoteText.trim(), author_id: itemNoteAuthorId }
          : { text: itemNoteText.trim() },
      })
      setItemNoteId(null)
      load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setItemNoteBusy(false)
    }
  }

  async function removeItemNote() {
    if (itemNoteId === null) return
    if (shiftModeEnabled && itemNoteAuthorId === '') return
    setItemNoteBusy(true)
    setError('')
    try {
      await api(`/items/${itemNoteId}/note`, {
        method: 'PATCH',
        body: shiftModeEnabled ? { text: '', author_id: itemNoteAuthorId } : { text: '' },
      })
      setItemNoteId(null)
      load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setItemNoteBusy(false)
    }
  }

  async function addNote() {
    if (!noteText.trim()) return
    if (shiftModeEnabled && noteAuthorId === '') return
    setNoteBusy(true)
    setError('')
    try {
      await api(`/maintenances/${id}/notes`, {
        method: 'POST',
        body: shiftModeEnabled
          ? { text: noteText.trim(), author_id: noteAuthorId }
          : { text: noteText.trim() },
      })
      setNoteText('')
      load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setNoteBusy(false)
    }
  }

  async function unlock(itemId: number) {
    if (!confirm('Kilit kaldırılsın mı? Madde ve altındaki tüm işaretler sıfırlanır.')) return
    setError('')
    try {
      await api(`/items/${itemId}/unlock`, { method: 'POST' })
      load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function remove() {
    if (!m) return
    if (!confirm(`İş Emri #${m.id} (${m.vehicle.code} — ${m.plan.name}) kalıcı olarak silinsin mi?`)) return
    setError('')
    setDeleting(true)
    try {
      await api(`/maintenances/${m.id}`, { method: 'DELETE' })
      onDeleted()
    } catch (e) {
      setError((e as Error).message)
      setDeleting(false)
    }
  }

  function jumpToItem(itemId: number) {
    const ancestors = m ? findAncestorIds(m.items ?? [], itemId) : null
    if (ancestors) setExpandIds((prev) => new Set([...prev, ...ancestors]))
    setHighlightId(itemId)
    setTimeout(() => setHighlightId(null), 2200)
  }

  if (!m) return <div className="card"><div className="muted">Yükleniyor…</div></div>
  const covered = m.covered_tiers ?? []
  const isMulti = covered.length > 1

  const allItems = m.items ?? []
  const displayedItems =
    isMulti && tierFilter !== 'all' ? filterByTier(allItems, tierFilter) : allItems
  const [done, total] = countLeaves(displayedItems)
  // "Tümü" görünümünde ve birden çok periyot varsa, her maddenin hangi
  // periyotlara ait olduğunu küçük rozetlerle göster.
  const showTierBadges = isMulti && tierFilter === 'all'
  // Hangi ana başlığın hangi alt maddesinde not olduğunu tek tek açmadan
  // görebilmek için tüm notlu maddeleri yol (breadcrumb) bilgisiyle topla.
  const notedItems = collectNotedItems(allItems)

  return (
    <div className="fade-in">
      <button className="ghost" onClick={onBack} style={{ marginBottom: 14, paddingLeft: 8 }}>
        <ArrowLeft size={14} style={{ marginRight: 6, verticalAlign: -2 }} /> Bakım Listesi
      </button>
      <div className="card">
        <div className="row">
          <h1 style={{ marginBottom: 0, fontSize: 20 }}>
            {m.vehicle.code} — {m.plan.name}
          </h1>
          <Badge tone={m.status}>{STATUS_TR[m.status]}</Badge>
          {perms.delete_maintenance && m.status !== 'completed' && (
            <button
              className="small danger"
              style={{ marginLeft: 'auto' }}
              disabled={deleting}
              onClick={remove}
            >
              İş Emrini Sil
            </button>
          )}
        </div>
        <div className="muted" style={{ margin: '10px 0' }}>
          İş Emri #{m.id} · {fmtKm(m.target_km)} km'de açıldı · Oluşturma: {fmt(m.created_at)} ·
          Başlangıç: {fmt(m.started_at)} · Bitiş: {fmt(m.completed_at)} · Açılış KM: {fmtKm(m.km_at_open)}
        </div>
        <div className="row" style={{ marginBottom: 10, gap: 8 }}>
          <span className="muted" style={{ fontSize: 13 }}>Atanan:</span>
          {isStaff ? (
            <select
              value={m.assigned_to?.id ?? ''}
              disabled={assignBusy}
              onChange={(e) => assignTo(e.target.value ? Number(e.target.value) : null)}
              style={{ fontSize: 12.5, padding: '5px 8px' }}
            >
              <option value="">Atanmamış</option>
              {assignableUsers.map((u) => (
                <option key={u.id} value={u.id}>{u.full_name}</option>
              ))}
            </select>
          ) : m.assigned_to ? (
            <>
              <span style={{ fontWeight: 600, fontSize: 13 }}>{m.assigned_to.full_name}</span>
              {isTechnician && m.assigned_to.id === user.id && (
                <button className="small ghost" disabled={assignBusy} onClick={() => assignTo(null)}>
                  <UserMinus size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                  Bırak
                </button>
              )}
            </>
          ) : (
            <>
              <span className="muted" style={{ fontSize: 13 }}>Atanmamış</span>
              {isTechnician && (
                <button className="small" disabled={assignBusy} onClick={() => assignTo(user.id)}>
                  <UserPlus size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                  Bana Ata
                </button>
              )}
            </>
          )}
        </div>
        {isMulti && (
          <div className="row" style={{ marginBottom: 10, flexWrap: 'wrap', gap: 6 }}>
            <span className="muted">Bu bakım şu periyotları birleştirir:</span>
            {covered.map((t) => (
              <Badge key={t} tone="in_progress">{tierLabel(t)}</Badge>
            ))}
            <span className="row" style={{ marginLeft: 'auto', gap: 6, flexWrap: 'wrap' }}>
              <span className="muted">Görünüm:</span>
              <button
                className={`small ${tierFilter === 'all' ? 'primary' : ''}`}
                onClick={() => setTierFilter('all')}
              >
                Tümü (birleşik)
              </button>
              {covered.map((t) => (
                <button
                  key={t}
                  className={`small ${tierFilter === t ? 'primary' : ''}`}
                  onClick={() => setTierFilter(t)}
                >
                  {tierLabel(t)}
                </button>
              ))}
            </span>
          </div>
        )}
        <div className="row">
          <div className="progressbar tone-green" style={{ flex: 1 }}>
            <div style={{ width: total ? `${(done / total) * 100}%` : '0%' }} />
          </div>
          <span className="muted" style={{ fontWeight: 600 }}>
            {done} / {total} madde
            {isMulti && tierFilter !== 'all' && <> · {tierLabel(tierFilter)} kapsamı</>}
          </span>
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      {notedItems.length > 0 && (
        <div className="card">
          <h3 style={{ marginTop: 0, fontSize: 15, display: 'flex', alignItems: 'center', gap: 6 }}>
            <AlertTriangle size={15} color="var(--amber, #b45309)" /> Notlu Maddeler ({notedItems.length})
          </h3>
          <div className="muted" style={{ fontSize: 12.5, marginBottom: 10 }}>
            Hangi ana başlığın hangi alt maddesinde not olduğunu buradan görüp tek tıkla o maddeye
            gidebilirsiniz.
          </div>
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
            {notedItems.map((n) => (
              <li key={n.id}>
                <button
                  className="noted-item-row"
                  onClick={() => jumpToItem(n.id)}
                >
                  <span className="noted-item-path">{n.path}</span>
                  <span className="noted-item-text">{n.text}</span>
                  <span className="muted" style={{ fontSize: 11 }}>— {n.byName} · {fmt(n.at)}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="card">
        <ul className="tree">
          {displayedItems.map((i) => (
            <TreeItem
              key={i.id}
              item={i}
              canEdit={!!perms.edit_maintenance}
              canUnlock={!!perms.approve_maintenance}
              onComplete={requestComplete}
              onUnlock={unlock}
              onEditNote={openItemNote}
              highlightId={highlightId}
              showTierBadges={showTierBadges}
              expandIds={expandIds}
            />
          ))}
        </ul>
        {displayedItems.length === 0 && (
          <div className="muted">Bu görünüm için madde bulunamadı.</div>
        )}
      </div>
      {/* 3D model görüntüleyici şimdilik iş emri ekranında gizlendi (kullanıcı
          isteği) — Bakım Ağacı sayfasındaki "3D Model Ekle" ve yönetim kısmı
          (yükleme/önizleme/silme) olduğu gibi çalışmaya devam ediyor; burada
          yalnızca gösterim kapatıldı, tekrar açmak için tek satır yeter. */}
      {false && <Model3DPanel models={models3d} onPartClick={jumpToItem} />}

      {!!perms.edit_maintenance && (
        <div className="card">
          <h3 style={{ marginTop: 0, fontSize: 15, display: 'flex', alignItems: 'center', gap: 6 }}>
            <StickyNote size={15} /> Ek Not / Bulgu
          </h3>
          <div className="muted" style={{ fontSize: 12.5, marginBottom: 10 }}>
            Kontrol listesinde olmayan ek bir arıza veya bakım ihtiyacı fark ettiyseniz buraya not düşün.
          </div>
          {(m.notes ?? []).length > 0 && (
            <ul style={{ listStyle: 'none', margin: '0 0 12px', padding: 0, display: 'flex', flexDirection: 'column', gap: 8 }}>
              {(m.notes ?? []).map((n: MaintenanceNote) => (
                <li key={n.id} style={{ padding: '8px 10px', background: 'var(--bg-soft, rgba(0,0,0,0.03))', borderRadius: 8, fontSize: 13 }}>
                  <div style={{ marginBottom: 4 }}>{n.text}</div>
                  <div className="muted" style={{ fontSize: 11.5, display: 'flex', alignItems: 'center', gap: 5 }}>
                    <Avatar name={n.author?.full_name ?? '?'} size={16} />
                    {n.author?.full_name ?? 'Bilinmiyor'} · {fmt(n.created_at)}
                  </div>
                </li>
              ))}
            </ul>
          )}
          <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'flex-start' }}>
            <textarea
              value={noteText}
              onChange={(e) => setNoteText(e.target.value)}
              placeholder="Örn: Sağ ön fren balatası aşınmış, ayrıca kontrol edilmeli…"
              rows={2}
              style={{ flex: '1 1 260px', minWidth: 220, fontSize: 13, padding: '8px 10px' }}
            />
            {shiftModeEnabled && (
              <select
                value={noteAuthorId}
                onChange={(e) => setNoteAuthorId(e.target.value ? Number(e.target.value) : '')}
                style={{ fontSize: 12.5, padding: '8px 8px' }}
              >
                <option value="">Notu ekleyen…</option>
                {pickerUsers.map((u) => (
                  <option key={u.id} value={u.id}>{u.full_name}</option>
                ))}
              </select>
            )}
            <button
              className="primary"
              disabled={noteBusy || !noteText.trim() || (shiftModeEnabled && noteAuthorId === '')}
              onClick={addNote}
            >
              <Send size={13} style={{ marginRight: 6, verticalAlign: -2 }} />
              Ekle
            </button>
          </div>
        </div>
      )}

      {pickerItemId !== null && (
        <div
          style={{
            position: 'fixed', inset: 0, background: 'rgba(15, 23, 42, 0.45)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 200,
          }}
          onClick={() => setPickerItemId(null)}
        >
          <div
            className="card"
            style={{ width: 340, maxWidth: '90vw' }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginTop: 0, fontSize: 15 }}>Bu maddeyi kim tamamladı?</h3>
            <div className="muted" style={{ fontSize: 12.5, marginBottom: 12 }}>
              {activeShiftName ? `${activeShiftName} — ` : ''}
              Bu seçim tek seferliktir; onayladıktan sonra değiştirilemez.
            </div>
            <select
              autoFocus
              value={pickerUserId}
              onChange={(e) => setPickerUserId(e.target.value ? Number(e.target.value) : '')}
              style={{ width: '100%', fontSize: 13, padding: '8px 10px' }}
            >
              <option value="">İsminizi seçin…</option>
              {pickerUsers.map((u) => (
                <option key={u.id} value={u.id}>{u.full_name}</option>
              ))}
            </select>
            {pickerUsers.length === 0 && (
              <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
                Bu vardiyaya henüz kimse atanmamış. Kullanıcılar sayfasından vardiya ataması yapabilirsiniz.
              </div>
            )}
            <div className="row" style={{ marginTop: 14, gap: 8, justifyContent: 'flex-end' }}>
              <button className="ghost" onClick={() => setPickerItemId(null)}>Vazgeç</button>
              <button className="primary" disabled={pickerUserId === ''} onClick={confirmComplete}>
                Onayla ve Tamamla
              </button>
            </div>
          </div>
        </div>
      )}

      {itemNoteId !== null && (
        <div
          style={{
            position: 'fixed', inset: 0, background: 'rgba(15, 23, 42, 0.45)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 200,
          }}
          onClick={() => !itemNoteBusy && setItemNoteId(null)}
        >
          <div
            className="card"
            style={{ width: 380, maxWidth: '90vw' }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginTop: 0, fontSize: 15, display: 'flex', alignItems: 'center', gap: 6 }}>
              <StickyNote size={15} /> Madde Notu
            </h3>
            <div className="muted" style={{ fontSize: 12.5, marginBottom: 12 }}>
              Bu maddeye özel bir bulgu veya açıklama ekleyin. Notu olan maddeler ağaçta ünlemle
              işaretlenir.
            </div>
            <textarea
              autoFocus
              value={itemNoteText}
              onChange={(e) => setItemNoteText(e.target.value)}
              placeholder="Örn: Balata aşınmış, bir sonraki bakımda değişmeli…"
              rows={3}
              style={{ width: '100%', fontSize: 13, padding: '8px 10px', marginBottom: 10 }}
            />
            {shiftModeEnabled && (
              <select
                value={itemNoteAuthorId}
                onChange={(e) => setItemNoteAuthorId(e.target.value ? Number(e.target.value) : '')}
                style={{ width: '100%', fontSize: 13, padding: '8px 10px' }}
              >
                <option value="">İsminizi seçin…</option>
                {pickerUsers.map((u) => (
                  <option key={u.id} value={u.id}>{u.full_name}</option>
                ))}
              </select>
            )}
            <div className="row" style={{ marginTop: 14, gap: 8, justifyContent: 'flex-end' }}>
              <button className="ghost" disabled={itemNoteBusy} onClick={() => setItemNoteId(null)}>
                Vazgeç
              </button>
              {itemNoteHadExisting && itemNoteText === '' && (
                <button
                  className="danger"
                  disabled={itemNoteBusy || (shiftModeEnabled && itemNoteAuthorId === '')}
                  onClick={removeItemNote}
                >
                  Notu Kaldır
                </button>
              )}
              <button
                className="primary"
                disabled={itemNoteBusy || (shiftModeEnabled && itemNoteAuthorId === '') || !itemNoteText.trim()}
                onClick={saveItemNote}
              >
                Kaydet
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------- Bakım listesi
export default function MaintenancesPage({
  user,
  perms,
  initialSelectedId,
  initialStatus,
  initialPlanName,
}: {
  user: User
  perms: Record<string, boolean>
  initialSelectedId?: number | null
  initialStatus?: string
  initialPlanName?: string
}) {
  const [list, setList] = useState<Maintenance[] | null>(null)
  const [listError, setListError] = useState('')
  const [status, setStatus] = useState(initialStatus ?? '')
  const [vehicleFilter, setVehicleFilter] = useState('')
  const [planFilter, setPlanFilter] = useState(initialPlanName ?? '')
  const [exportFormat, setExportFormat] = useState<ExportFormat>('xlsx')
  const [selected, setSelected] = useState<number | null>(initialSelectedId ?? null)

  // Vardiyalı çalışma: kapalıyken madde tamamlama/not ekleme otomatik
  // oturum sahibine atfedilir (seçici gösterilmez); açıkken "Vardiya 1/2/3"
  // seçimine göre yalnızca o vardiyadaki kişiler arasından seçim yapılır.
  const [shiftMode, setShiftMode] = useState<ShiftModeConfig | null>(null)
  const [shifts, setShifts] = useState<Shift[]>([])
  const [activeShiftId, setActiveShiftId] = useState<number | null>(() => {
    const saved = localStorage.getItem(ACTIVE_SHIFT_STORAGE_KEY)
    return saved ? Number(saved) : null
  })
  const [shiftBusy, setShiftBusy] = useState(false)
  const isStaff = user.role === 'admin' || user.role === 'engineer' || user.role === 'amir'

  // Vardiya üyelerini (kimin hangi vardiyada olduğunu) buradan yönetebilmek
  // için tüm atanabilir kullanıcıların listesi — "Kullanıcılar" sayfasına
  // gitmeye gerek kalmadan, seçili vardiyaya doğrudan buradan üye
  // eklenip/çıkarılabilir.
  const [allUsers, setAllUsers] = useState<User[]>([])
  const [memberBusy, setMemberBusy] = useState(false)
  const [addMemberId, setAddMemberId] = useState<number | ''>('')
  // Vardiya üyeliği burada değiştirildiğinde, MaintenanceDetail içindeki
  // isim seçici listesinin (pickerUsers) hemen yeniden çekilmesini
  // sağlamak için sayaç — activeShiftId aynı kalsa bile efekt yeniden çalışır.
  const [shiftMembersVersion, setShiftMembersVersion] = useState(0)

  const loadAllUsers = useCallback(() => {
    api<User[]>('/users/assignable').then(setAllUsers).catch(() => setAllUsers([]))
  }, [])

  useEffect(() => {
    api<ShiftModeConfig>('/shift-mode').then(setShiftMode).catch(() => setShiftMode(null))
    api<Shift[]>('/shifts').then(setShifts).catch(() => setShifts([]))
    loadAllUsers()
  }, [loadAllUsers])

  function selectShift(shiftId: number) {
    setActiveShiftId(shiftId)
    localStorage.setItem(ACTIVE_SHIFT_STORAGE_KEY, String(shiftId))
    setAddMemberId('')
  }

  async function setMemberShift(userId: number, shiftId: number | null) {
    setMemberBusy(true)
    try {
      await api(`/users/${userId}`, { method: 'PATCH', body: { shift_id: shiftId } })
      loadAllUsers()
      setShiftMembersVersion((v) => v + 1)
    } catch {
      // sessizce yut — küçük bir üye yönetim kutusu
    } finally {
      setMemberBusy(false)
    }
  }

  async function toggleShiftMode() {
    if (!shiftMode) return
    setShiftBusy(true)
    try {
      const updated = await api<ShiftModeConfig>('/shift-mode', {
        method: 'PATCH',
        body: { enabled: !shiftMode.enabled },
      })
      setShiftMode(updated)
    } catch {
      // sessizce yut — küçük bir ayar butonu, ayrı hata kutusu gerekmiyor
    } finally {
      setShiftBusy(false)
    }
  }

  const activeShiftName = shifts.find((s) => s.id === activeShiftId)?.name ?? null

  const shiftBar = (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="row" style={{ gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <span className="muted" style={{ fontWeight: 600, fontSize: 13, display: 'flex', alignItems: 'center', gap: 5 }}>
          <Users size={14} />
          Vardiyalı Çalışma:
        </span>
        {isStaff ? (
          <button className={`small ${shiftMode?.enabled ? 'primary' : ''}`} disabled={shiftBusy || !shiftMode} onClick={toggleShiftMode}>
            {shiftMode?.enabled ? 'Açık' : 'Kapalı'}
          </button>
        ) : (
          <Badge tone={shiftMode?.enabled ? 'in_progress' : 'neutral'}>{shiftMode?.enabled ? 'Açık' : 'Kapalı'}</Badge>
        )}
        {shiftMode?.enabled && (
          <>
            <span className="muted" style={{ fontSize: 13, marginLeft: 6 }}>Çalıştığınız vardiya:</span>
            {shifts.map((s) => (
              <button
                key={s.id}
                className={`small ${activeShiftId === s.id ? 'primary' : ''}`}
                onClick={() => selectShift(s.id)}
              >
                {s.name}
              </button>
            ))}
          </>
        )}
      </div>
      <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
        {shiftMode?.enabled
          ? 'Bir madde tikleyen/not ekleyen kişi, seçili vardiyadaki isimler arasından seçilir.'
          : 'Kapalıyken bir madde tikleyen/not ekleyen kişi otomatik olarak o an giriş yapmış hesap olur — isim seçme penceresi çıkmaz.'}
      </div>
      {shiftMode?.enabled && activeShiftId != null && (
        <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border, rgba(0,0,0,0.08))' }}>
          <div className="muted" style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 8 }}>
            {activeShiftName} Üyeleri:
          </div>
          <div className="row" style={{ gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            {allUsers.filter((u) => u.shift_id === activeShiftId).map((u) => (
              <span key={u.id} className="badge" style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12.5, padding: '4px 8px' }}>
                <Avatar name={u.full_name} size={16} />
                {u.full_name}
                {isStaff && (
                  <button
                    className="ghost"
                    disabled={memberBusy}
                    title="Vardiyadan çıkar"
                    onClick={() => setMemberShift(u.id, null)}
                    style={{ padding: 0, minWidth: 0, lineHeight: 1 }}
                  >
                    <UserMinus size={12} />
                  </button>
                )}
              </span>
            ))}
            {allUsers.filter((u) => u.shift_id === activeShiftId).length === 0 && (
              <span className="muted" style={{ fontSize: 12.5 }}>Bu vardiyaya henüz kimse atanmamış.</span>
            )}
          </div>
          {isStaff && (
            <div className="row" style={{ gap: 8, marginTop: 10, alignItems: 'center' }}>
              <select
                value={addMemberId}
                onChange={(e) => setAddMemberId(e.target.value ? Number(e.target.value) : '')}
                style={{ fontSize: 12.5, padding: '5px 8px' }}
              >
                <option value="">Üye ekle…</option>
                {allUsers.filter((u) => u.shift_id !== activeShiftId).map((u) => (
                  <option key={u.id} value={u.id}>{u.full_name}</option>
                ))}
              </select>
              <button
                className="small"
                disabled={memberBusy || addMemberId === ''}
                onClick={() => {
                  if (addMemberId === '') return
                  setMemberShift(addMemberId, activeShiftId)
                  setAddMemberId('')
                }}
              >
                <UserPlus size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                Ekle
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )

  useEffect(() => {
    if (selected !== null) return
    setList(null)
    setListError('')
    const q = status ? `?status=${status}` : ''
    api<Maintenance[]>(`/maintenances${q}`)
      .then(setList)
      .catch((e) => {
        setListError((e as Error).message)
        setList([])
      })
  }, [status, selected])

  if (selected !== null)
    return (
      <>
        {shiftBar}
        <MaintenanceDetail
          id={selected}
          user={user}
          perms={perms}
          onBack={() => setSelected(null)}
          onDeleted={() => {
            setSelected(null)
            setList(null)
          }}
          shiftModeEnabled={!!shiftMode?.enabled}
          activeShiftId={activeShiftId}
          activeShiftName={activeShiftName}
          shiftMembersVersion={shiftMembersVersion}
        />
      </>
    )

  if (perms.view_maintenance === false)
    return (
      <>
        <PageHeader title="Bakımlar" subtitle="Açılan tüm bakım iş emirlerini periyot, araç ve durum bazında filtreleyip inceleyin." />
        <EmptyState
          icon={<Wrench size={22} />}
          title="Görüntüleme yetkiniz yok"
          description="Bakım kayıtlarını görüntülemek için gereken izne sahip değilsiniz. Yöneticinizle iletişime geçin."
        />
      </>
    )

  const rawList = list ?? []

  // Aynı araç için birden fazla açık (Bekliyor/Devam Ediyor) bakım varsa,
  // yalnızca hedef km'si en büyük olanı listelenir — küçük periyotlar
  // (ör. 25.000/50.000 km) zaten o büyük bakımın içine (kombine checklist)
  // dahil edilmiştir, ayrı satır olarak tekrar gösterilmez. Tamamlanan
  // bakımlar (geçmiş kayıt oldukları için) bu kısıtlamadan etkilenmez.
  const maxOpenTargetByVehicle = new Map<number, number>()
  for (const m of rawList) {
    if (m.status === 'completed') continue
    const cur = maxOpenTargetByVehicle.get(m.vehicle.id) ?? -Infinity
    if (m.target_km > cur) maxOpenTargetByVehicle.set(m.vehicle.id, m.target_km)
  }
  const all = rawList.filter(
    (m) => m.status === 'completed' || m.target_km === maxOpenTargetByVehicle.get(m.vehicle.id),
  )

  const vehicleCodes = Array.from(new Set(all.map((m) => m.vehicle.code))).sort()
  const planNames = Array.from(new Set(all.map((m) => m.plan.name))).sort(
    (a, b) => (all.find((m) => m.plan.name === a)?.plan.interval_km ?? 0) -
      (all.find((m) => m.plan.name === b)?.plan.interval_km ?? 0),
  )

  // Tahmini bakım aralığının bitiş tarihine (en geç yapılması gereken
  // tarihe) göre en erkenden en gece doğru sıralanır — süresi geçmiş
  // (gecikmiş) bakımlar bu tarih geçmişte olduğu için otomatik en üstte
  // çıkar. Tamamlanmış bakımların tahmini aralığı olmadığı için en sona
  // (en son tamamlanan en üstte olacak şekilde) eklenir.
  const filtered = all
    .filter(
      (m) =>
        (!vehicleFilter || m.vehicle.code === vehicleFilter) &&
        (!planFilter || m.plan.name === planFilter),
    )
    .sort((a, b) => {
      const wa = estimateWindow(a)
      const wb = estimateWindow(b)
      if (wa && wb) return wa.latest.getTime() - wb.latest.getTime()
      if (wa && !wb) return -1
      if (!wa && wb) return 1
      const ta = a.completed_at ? new Date(a.completed_at).getTime() : 0
      const tb = b.completed_at ? new Date(b.completed_at).getTime() : 0
      return tb - ta
    })

  return (
    <>
      <PageHeader
        title="Bakımlar"
        subtitle="Açılan tüm bakım iş emirlerini periyot, araç ve durum bazında filtreleyip inceleyin."
      />
      {shiftBar}
      {listError && <div className="error" style={{ marginBottom: 12 }}>{listError}</div>}

      <div className="table-card fade-in">
        <div className="table-toolbar">
          <ListFilter size={15} color="var(--text-tertiary)" />
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Tüm Durumlar</option>
            <option value="pending">Bekliyor</option>
            <option value="in_progress">Devam Ediyor</option>
            <option value="completed">Tamamlandı</option>
          </select>
          <select value={vehicleFilter} onChange={(e) => setVehicleFilter(e.target.value)}>
            <option value="">Tüm Araçlar</option>
            {vehicleCodes.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <select value={planFilter} onChange={(e) => setPlanFilter(e.target.value)}>
            <option value="">Tüm Periyotlar</option>
            {planNames.map((n) => (
              <option key={n} value={n}>{n}</option>
            ))}
          </select>
          {(vehicleFilter || planFilter || status) && (
            <button className="small ghost" onClick={() => { setVehicleFilter(''); setPlanFilter(''); setStatus('') }}>
              Filtreleri Temizle
            </button>
          )}
          <span className="muted" style={{ marginLeft: 'auto' }}>
            {filtered.length} kayıt
          </span>
          {perms.export_excel && (
            <>
              <select value={exportFormat} onChange={(e) => setExportFormat(e.target.value as ExportFormat)}>
                <option value="xlsx">Excel</option>
                <option value="pdf">PDF</option>
                <option value="csv">CSV</option>
                <option value="docx">Word</option>
              </select>
              <button
                className="small"
                onClick={() => {
                  const qs = new URLSearchParams()
                  if (status) qs.set('status', status)
                  qs.set('format', exportFormat)
                  const base = status === 'completed' ? 'bakim_gecmisi' : 'bakimlar'
                  apiDownload(`/maintenances/export?${qs.toString()}`, `${base}.${exportFormat}`)
                }}
              >
                <Download size={13} style={{ marginRight: 5, verticalAlign: -2 }} />
                Dışa Aktar
              </button>
            </>
          )}
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>İş Emri</th>
                <th>Araç</th>
                <th>Periyot</th>
                <th>Hedef KM</th>
                <th>Durum</th>
                <th>Tahmini Bakım Aralığı</th>
                <th>Tamamlanma Tarihi</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {list === null && <SkeletonTableRows columns={8} />}
              {list !== null && filtered.map((m) => {
                const window = estimateWindow(m)
                return (
                <tr key={m.id} className="clickable-row" onClick={() => setSelected(m.id)}>
                  <td style={{ fontWeight: 600 }}>#{m.id}</td>
                  <td><span className="vehicle-code">{m.vehicle.code}</span></td>
                  <td>{m.plan.name}</td>
                  <td>{fmtKm(m.target_km)} km</td>
                  <td><Badge tone={m.status}>{STATUS_TR[m.status]}</Badge></td>
                  <td>
                    {window ? (
                      <Badge tone={window.overdue ? 'danger' : window.earliestReached ? 'in_progress' : 'neutral'}>
                        {window.overdue
                          ? `Süresi geçti · ${fmtDate(window.latest)}`
                          : window.earliestReached
                            ? `En geç ${fmtDate(window.latest)}`
                            : `${fmtDate(window.earliest)} – ${fmtDate(window.latest)} arası`}
                      </Badge>
                    ) : '—'}
                  </td>
                  <td>{fmt(m.completed_at)}</td>
                  <td>
                    <button className="small primary" onClick={(e) => { e.stopPropagation(); setSelected(m.id) }}>
                      Aç <ChevronRight size={12} style={{ verticalAlign: -1, marginLeft: 2 }} />
                    </button>
                  </td>
                </tr>
                )
              })}
            </tbody>
          </table>
          {list !== null && filtered.length === 0 && (
            <EmptyState
              icon={<Wrench size={22} />}
              title="Kayıt bulunamadı"
              description="Seçtiğiniz filtrelere uyan bir bakım iş emri yok."
            />
          )}
        </div>
      </div>
    </>
  )
}
