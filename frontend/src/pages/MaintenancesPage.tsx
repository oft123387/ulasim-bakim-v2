import { useCallback, useEffect, useRef, useState } from 'react'
import { api, apiDownload } from '../api'
import { showToast } from '../components/toast'
import Tram3D from '../components/Tram3D'
import type { ExportFormat, Item, Maintenance, MaintenanceNote, Model3DWithStatus, NodeAttachment, Plan, Shift, ShiftModeConfig, User, Vehicle } from '../types'
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
  PlayCircle,
  ShieldAlert,
  Trash2,
  Plus,
  Search,
  X,
  CheckCircle2,
  RotateCcw,
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
// Tahmini Bakım Aralığı rozetinde km'nin yanında tahmini tarih de gösterilir
// (ör. "en geç 12.08.2026") — DAILY_KM varsayımıyla hesaplanan earliest/latest
// tarihlerin gün.ay.yıl biçiminde yazımı.
const fmtDate = (d: Date) => d.toLocaleDateString('tr-TR')

// Süre bazlı (aylık/yıllık) iş emirlerinde gerçek hedef bir km değil takvim
// tarihidir (target_date) — backend km alanını bu durumda gizler (null).
// Tablo/başlık gösterimlerinde ikisini de doğru biçimde tek bir metne çevirir.
const hedefLabel = (m: Maintenance) =>
  m.target_date ? new Date(m.target_date).toLocaleDateString('tr-TR') : `${fmtKm(m.target_km ?? 0)} km`

// Plan periyodunu türüne göre okunur biçimde göster (km bazlı ↔ süre bazlı) —
// seçim listelerinde kullanılır.
const planPeriodLabel = (p: Plan) => {
  if (p.plan_type === 'unknown') return 'Belirsiz (?)'
  if (p.plan_type === 'time') {
    const mo = p.interval_months ?? 0
    return mo % 12 === 0 ? `${mo / 12} yıl` : `${mo} ay`
  }
  return `${fmtKm(p.interval_km)} km`
}

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
): { earliest: Date; latest: Date; earliestReached: boolean; overdue: boolean; remainingKm: number } | null {
  if (m.status === 'completed') return null
  // Süre bazlı (aylık/yıllık) iş emirlerinde hedef bir km değil takvim
  // tarihidir (target_date) — km projeksiyonu burada anlamsız, bu yüzden bu
  // fonksiyon yalnızca km bazlı planlar için hesap yapar.
  if (m.plan.plan_type === 'time' || m.target_km == null) return null
  // Faz 26: plan yüzde (lower_pct/upper_pct) ya da sabit km (lower_km/
  // upper_km) bazlı tolerans kullanabiliyor — hangisi geçerliyse ona göre
  // hesapla (aksi halde km bazlı bir plan için burada hep varsayılan %50/%10
  // kullanılıp yanlış tarih üretilirdi).
  const lowerKm = m.plan.threshold_type === 'km'
    ? m.target_km - m.plan.lower_km
    : m.target_km - (m.plan.interval_km * m.plan.lower_pct) / 100
  const upperKm = m.plan.threshold_type === 'km'
    ? m.target_km + m.plan.upper_km
    : m.target_km + (m.plan.interval_km * m.plan.upper_pct) / 100
  const remainingLower = lowerKm - m.vehicle.current_km
  const remainingUpper = upperKm - m.vehicle.current_km
  const earliestDays = Math.max(0, Math.ceil(remainingLower / DAILY_KM))
  const latestDays = Math.max(0, Math.ceil(remainingUpper / DAILY_KM))
  const earliest = new Date()
  earliest.setDate(earliest.getDate() + earliestDays)
  const latest = new Date()
  latest.setDate(latest.getDate() + latestDays)
  return {
    earliest,
    latest,
    earliestReached: remainingLower <= 0,
    overdue: remainingUpper <= 0,
    // Sıralama ve gösterim için km bazlı kalan mesafe (negatifse süresi
    // geçmiş demektir) — hedef km'den aracın güncel km'si çıkarılır.
    remainingKm: m.target_km - m.vehicle.current_km,
  }
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
  searchMatchIds,
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
  // Bakım ağacı aramasıyla eşleşen madde id'leri — eşleşen satırlar sarı
  // vurguyla işaretlenir (arama kutusu boşken undefined/boş küme).
  searchMatchIds?: Set<number>
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
        className={`tree-row ${isLeaf ? 'leaf' : ''} ${item.is_completed ? 'done' : ''} ${isHighlighted ? 'flash' : ''} ${searchMatchIds?.has(item.id) ? 'search-match' : ''}`}
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
        {item.card_code && <span className="card-code-pill">{item.card_code}</span>}
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
        {(() => {
          // PDF eki varsa, not rozetiyle aynı mantıkta ama tıklanabilir küçük
          // bir rozet — maddenin hemen yanında, nerede olduğu belli olacak
          // şekilde gösterilir; tıklanınca PDF yeni sekmede açılır.
          const pdfAtt = item.attachments.find((a) => a.kind === 'pdf')
          if (!pdfAtt) return null
          return (
            <a
              className="pdf-pill"
              href={`/uploads/attachments/${pdfAtt.filename}`}
              target="_blank"
              rel="noreferrer"
              title={pdfAtt.label || pdfAtt.original_name || 'PDF ekini aç'}
            >
              <FileText size={11} /> PDF
            </a>
          )
        })()}
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
                  searchMatchIds={searchMatchIds}
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

interface SearchMatchEntry {
  id: number
  path: string
}

/** Ağacı gezip adı arama sorgusuyla eşleşen tüm alt maddeleri, "Notlu
 * Maddeler" panelindekiyle aynı yol (breadcrumb) formatında düz bir listeye
 * toplar — bakım ağacında hızlı arama (Ctrl+F) için kullanılır. */
function collectSearchMatches(items: Item[], query: string, trail: string[] = []): SearchMatchEntry[] {
  const q = query.trim().toLocaleLowerCase('tr-TR')
  if (!q) return []
  const out: SearchMatchEntry[] = []
  for (const it of items) {
    const isLeaf = it.children.length === 0
    if (isLeaf) {
      if (it.name.toLocaleLowerCase('tr-TR').includes(q)) {
        out.push({ id: it.id, path: [...trail, it.name].join(' > ') })
      }
    } else {
      out.push(...collectSearchMatches(it.children, query, [...trail, it.name]))
    }
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
  // Admin kısayolları: tek tek madde işaretlemeden iş emrini doğrudan
  // tamamlandı yapma / tamamlandı durumundan çıkarma.
  const [statusActionBusy, setStatusActionBusy] = useState(false)
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
  // Bakım ağacında hızlı arama (Ctrl+F) — girilen metne göre eşleşen
  // maddeleri bulur, "sonraki" ile aralarında gezinilebilir; jumpToItem ile
  // aynı vurgulama/otomatik açma mekanizmasını kullanır.
  const [treeSearch, setTreeSearch] = useState('')
  const [searchIdx, setSearchIdx] = useState(0)
  const treeSearchRef = useRef<HTMLInputElement>(null)

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
      showToast('✓ Atama güncellendi')
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
        showToast('✓ Kaydedildi')
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
      showToast('✓ Kaydedildi')
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
      showToast('✓ Not kaydedildi')
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
      showToast('✓ Kilit açıldı', 'info')
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

  async function forceComplete() {
    if (!m) return
    if (!confirm(`İş Emri #${m.id} (${m.vehicle.code} — ${m.plan.name}) tek tek madde işaretlenmeden doğrudan TAMAMLANDI yapılsın mı?`)) return
    setError('')
    setStatusActionBusy(true)
    try {
      await api(`/maintenances/${m.id}/force-complete`, { method: 'POST' })
      load()
      showToast('✓ İş emri tamamlandı yapıldı')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setStatusActionBusy(false)
    }
  }

  async function reopenMaintenance() {
    if (!m) return
    if (!confirm(`İş Emri #${m.id} (${m.vehicle.code} — ${m.plan.name}) "Tamamlandı" durumundan çıkarılsın mı? Madde işaretleri değişmez.`)) return
    setError('')
    setStatusActionBusy(true)
    try {
      await api(`/maintenances/${m.id}/reopen`, { method: 'POST' })
      load()
      showToast('✓ Tamamlandıdan çıkarıldı', 'info')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setStatusActionBusy(false)
    }
  }

  function jumpToItem(itemId: number) {
    const ancestors = m ? findAncestorIds(m.items ?? [], itemId) : null
    if (ancestors) setExpandIds((prev) => new Set([...prev, ...ancestors]))
    setHighlightId(itemId)
    setTimeout(() => setHighlightId(null), 2200)
  }

  // Ctrl+F (Cmd+F Mac'te) tarayıcının kendi "sayfada bul" kutusu yerine
  // buradaki bakım ağacı arama kutusuna odaklansın — ustanın en sık
  // ihtiyaç duyacağı kısayol.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
        e.preventDefault()
        treeSearchRef.current?.focus()
        treeSearchRef.current?.select()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

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
  const searchResults = collectSearchMatches(displayedItems, treeSearch)
  const searchMatchSet = treeSearch.trim() ? new Set(searchResults.map((r) => r.id)) : undefined

  function goToSearchMatch(idx: number) {
    if (searchResults.length === 0) return
    const wrapped = ((idx % searchResults.length) + searchResults.length) % searchResults.length
    setSearchIdx(wrapped)
    jumpToItem(searchResults[wrapped].id)
  }

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
          <div className="row" style={{ marginLeft: 'auto', gap: 8 }}>
            {user.role === 'admin' && m.status !== 'completed' && (
              <button
                className="small"
                title="Tek tek madde işaretlemeden iş emrini doğrudan tamamlandı yap"
                disabled={statusActionBusy}
                onClick={forceComplete}
              >
                <CheckCircle2 size={13} style={{ marginRight: 5, verticalAlign: -2 }} />
                Tamamlandı Yap
              </button>
            )}
            {user.role === 'admin' && m.status === 'completed' && (
              <button
                className="small"
                title="Bu iş emrini 'Tamamlandı' durumundan çıkar (madde işaretleri değişmez)"
                disabled={statusActionBusy}
                onClick={reopenMaintenance}
              >
                <RotateCcw size={13} style={{ marginRight: 5, verticalAlign: -2 }} />
                Tamamlandıdan Çıkar
              </button>
            )}
            {perms.delete_maintenance && m.status !== 'completed' && (
              <button
                className="small danger"
                disabled={deleting}
                onClick={remove}
              >
                İş Emrini Sil
              </button>
            )}
          </div>
        </div>
        <div className="muted" style={{ margin: '10px 0' }}>
          İş Emri #{m.id} · {hedefLabel(m)}'de açıldı · Oluşturma: {fmt(m.created_at)} ·
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
        <div className="row" style={{ gap: 10, flexWrap: 'wrap', alignItems: 'center', marginBottom: 14 }}>
          <div className="search-box" style={{ flex: 1, minWidth: 220 }}>
            <Search size={15} />
            <input
              ref={treeSearchRef}
              value={treeSearch}
              onChange={(e) => {
                const v = e.target.value
                setTreeSearch(v)
                setSearchIdx(0)
                if (v.trim()) {
                  const matches = collectSearchMatches(displayedItems, v)
                  if (matches.length > 0) jumpToItem(matches[0].id)
                }
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  goToSearchMatch(e.shiftKey ? searchIdx - 1 : searchIdx + 1)
                }
              }}
              placeholder="Bakım ağacında ara… (Ctrl+F)"
            />
            {treeSearch && (
              <button
                className="topbar-search-clear"
                title="Temizle"
                onClick={() => { setTreeSearch(''); setSearchIdx(0) }}
              >
                <X size={14} />
              </button>
            )}
          </div>
          {treeSearch.trim() && (
            <span className="muted" style={{ fontSize: 12.5, display: 'flex', alignItems: 'center', gap: 8 }}>
              {searchResults.length > 0 ? (
                <>
                  {(searchIdx % searchResults.length) + 1} / {searchResults.length} eşleşme
                  <button className="small ghost" onClick={() => goToSearchMatch(searchIdx - 1)}>‹ Önceki</button>
                  <button className="small ghost" onClick={() => goToSearchMatch(searchIdx + 1)}>Sonraki ›</button>
                </>
              ) : (
                <>Eşleşme bulunamadı</>
              )}
            </span>
          )}
        </div>
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
              searchMatchIds={searchMatchSet}
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

  const loadList = useCallback(() => {
    setList(null)
    setListError('')
    const q = status ? `?status=${status}` : ''
    api<Maintenance[]>(`/maintenances${q}`)
      .then(setList)
      .catch((e) => {
        setListError((e as Error).message)
        setList([])
      })
  }, [status])

  useEffect(() => {
    if (selected !== null) return
    loadList()
  }, [loadList, selected])

  // Faz 26 — Bakım başlatma onayı: otomatik açılan ama admin/mühendis henüz
  // "Başlat" demediği için Bakımlar listesinde/Takvim'de görünmeyen aday iş
  // emirleri. Yalnızca start_maintenance yetkisi olanlara (admin/mühendis/
  // amir) yüklenir — teknisyen bu uç noktaya zaten erişemez (403).
  // Faz 26 UI — iki tablo alt alta karışık durmasın diye tek seferde
  // yalnızca biri gösterilir: "Başlatılanlar" (varsayılan) ya da
  // "Bekleyenler" (başlatma yetkisi olanlara, büyük sekme düğmeleriyle seçilir).
  const [mainView, setMainView] = useState<'started' | 'pending'>('started')
  const [pendingStart, setPendingStart] = useState<Maintenance[] | null>(null)
  const [pendingStartBusy, setPendingStartBusy] = useState<number | null>(null)
  const [pendingStartError, setPendingStartError] = useState('')

  const loadPendingStart = useCallback(() => {
    if (!perms.start_maintenance) return
    api<Maintenance[]>('/maintenances/pending-start')
      .then(setPendingStart)
      .catch(() => setPendingStart([]))
  }, [perms.start_maintenance])

  useEffect(() => {
    if (selected !== null) return
    loadPendingStart()
  }, [loadPendingStart, selected])

  async function startPendingMaintenance(id: number) {
    setPendingStartBusy(id)
    setPendingStartError('')
    try {
      await api(`/maintenances/${id}/start`, { method: 'POST' })
      loadPendingStart()
      loadList()
    } catch (e) {
      setPendingStartError((e as Error).message)
    } finally {
      setPendingStartBusy(null)
    }
  }

  // Faz 28 — Manuel Bakım Ekle: otomatik tespiti beklemeden admin/mühendis
  // araç + periyot + hedef km seçerek elle bir iş emri açabilir (ör. otomatik
  // algılama kaçırılmışsa ya da plan dışı ek bir bakım gerekiyorsa). Bu iş
  // emri "Başlat" onayı beklemeden doğrudan aktif olarak açılır.
  const [manualOpen, setManualOpen] = useState(false)
  const [manualVehicles, setManualVehicles] = useState<Vehicle[]>([])
  const [manualPlans, setManualPlans] = useState<Plan[]>([])
  const [manualVehicleId, setManualVehicleId] = useState<number | ''>('')
  const [manualPlanId, setManualPlanId] = useState<number | ''>('')
  const [manualTargetKm, setManualTargetKm] = useState('')
  const [manualTargetDate, setManualTargetDate] = useState('')
  const [manualBusy, setManualBusy] = useState(false)
  const [manualError, setManualError] = useState('')

  useEffect(() => {
    if (!perms.create_maintenance) return
    api<Vehicle[]>('/vehicles').then(setManualVehicles).catch(() => setManualVehicles([]))
    api<Plan[]>('/plans').then(setManualPlans).catch(() => setManualPlans([]))
  }, [perms.create_maintenance])

  const manualVehicle = manualVehicles.find((v) => v.id === manualVehicleId) ?? null
  const manualPlan = manualPlans.find((p) => p.id === manualPlanId) ?? null
  // Araç ve periyot seçilince, aracın güncel km'sinden sonraki ilk periyot
  // katını öneri olarak doldurur — kullanıcı isterse elle değiştirebilir.
  const manualSuggestedKm =
    manualVehicle && manualPlan && manualPlan.interval_km > 0
      ? Math.ceil((manualVehicle.current_km + 1) / manualPlan.interval_km) * manualPlan.interval_km
      : null

  function openManualAdd() {
    setManualVehicleId('')
    setManualPlanId('')
    setManualTargetKm('')
    setManualTargetDate('')
    setManualError('')
    setManualOpen(true)
  }

  async function submitManualMaintenance() {
    const isTimePlan = manualPlan?.plan_type === 'time'
    if (manualVehicleId === '' || manualPlanId === '') return
    if (isTimePlan ? !manualTargetDate.trim() : !manualTargetKm.trim()) return
    setManualBusy(true)
    setManualError('')
    try {
      await api('/maintenances/manual', {
        method: 'POST',
        body: isTimePlan
          ? { vehicle_id: manualVehicleId, plan_id: manualPlanId, target_date: manualTargetDate }
          : { vehicle_id: manualVehicleId, plan_id: manualPlanId, target_km: Number(manualTargetKm) },
      })
      setManualOpen(false)
      setMainView('started')
      loadList()
    } catch (e) {
      setManualError((e as Error).message)
    } finally {
      setManualBusy(false)
    }
  }

  // Faz 27 — Tehlikeli Bölge: tamamlananlar dahil TÜM bakım iş emirlerini
  // kalıcı olarak siler. Yanlışlıkla tıklamayı önlemek için "SİL" yazarak
  // onaylama zorunludur (tek tıkla confirm() yeterli görülmedi — bu işlem
  // tüm araçların bakım geçmişini geri dönüşsüz şekilde siler).
  const [dangerOpen, setDangerOpen] = useState(false)
  const [dangerConfirmText, setDangerConfirmText] = useState('')
  const [dangerBusy, setDangerBusy] = useState(false)
  const [dangerError, setDangerError] = useState('')

  async function deleteAllMaintenances() {
    if (dangerConfirmText.trim().toUpperCase() !== 'SİL') return
    setDangerBusy(true)
    setDangerError('')
    try {
      await api('/maintenances/all', { method: 'DELETE' })
      setDangerOpen(false)
      setDangerConfirmText('')
      loadList()
      loadPendingStart()
    } catch (e) {
      setDangerError((e as Error).message)
    } finally {
      setDangerBusy(false)
    }
  }

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
        <PageHeader title="Bakımlar" />
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
  // Faz: km ve süre bazlı iş emirleri artık aynı araçta eş zamanlı açık
  // olabilir — bu yüzden dedup anahtarı sadece araç değil, (araç, plan
  // türü) olmalı; aksi halde bir km iş emri diğer türdeki iş emrini
  // gizleyebilir. target_km süre bazlı maddelerde null olabileceğinden
  // mutlak değer + null-güvenli karşılaştırma kullanılır.
  const maxOpenTargetByVehicle = new Map<string, number>()
  const dedupKey = (m: Maintenance) => `${m.vehicle.id}:${m.plan.plan_type}`
  for (const m of rawList) {
    if (m.status === 'completed') continue
    const key = dedupKey(m)
    const val = Math.abs(m.target_km ?? 0)
    const cur = maxOpenTargetByVehicle.get(key) ?? -Infinity
    if (val > cur) maxOpenTargetByVehicle.set(key, val)
  }
  const all = rawList.filter(
    (m) => m.status === 'completed' || Math.abs(m.target_km ?? 0) === maxOpenTargetByVehicle.get(dedupKey(m)),
  )

  const vehicleCodes = Array.from(new Set(all.map((m) => m.vehicle.code))).sort()
  const planNames = Array.from(new Set(all.map((m) => m.plan.name))).sort(
    (a, b) => (all.find((m) => m.plan.name === a)?.plan.interval_km ?? 0) -
      (all.find((m) => m.plan.name === b)?.plan.interval_km ?? 0),
  )

  // Kalan kilometreye göre sıralanır (tarihe göre değil): kalan km'si en az
  // (veya negatifse en çok süresi geçmiş) olan en üstte çıkar. Tamamlanmış
  // bakımların kalan km'si olmadığı için en sona (en son tamamlanan en
  // üstte olacak şekilde) eklenir.
  const filtered = all
    .filter(
      (m) =>
        (!vehicleFilter || m.vehicle.code === vehicleFilter) &&
        (!planFilter || m.plan.name === planFilter),
    )
    .sort((a, b) => {
      const wa = estimateWindow(a)
      const wb = estimateWindow(b)
      if (wa && wb) return wa.remainingKm - wb.remainingKm
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
        subtitle="Açık iş emirlerini filtreleyin, bekleyenleri başlatın ve detaylara geçin."
        actions={
          !!perms.create_maintenance && (
            <button className="primary" onClick={openManualAdd}>
              <Plus size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
              Manuel Bakım Ekle
            </button>
          )
        }
      />
      {shiftBar}

      {!!perms.start_maintenance && (
        <div className="row maintenance-view-tabs" style={{ gap: 10, marginBottom: 16 }}>
          <button
            className={mainView === 'started' ? 'primary' : ''}
            style={{ padding: '11px 22px', fontSize: 14.5, fontWeight: 700 }}
            onClick={() => setMainView('started')}
          >
            <Wrench size={16} style={{ marginRight: 8, verticalAlign: -3 }} />
            Başlatılanlar
          </button>
          <button
            className={mainView === 'pending' ? 'primary' : ''}
            style={{ padding: '11px 22px', fontSize: 14.5, fontWeight: 700 }}
            onClick={() => setMainView('pending')}
          >
            <PlayCircle size={16} style={{ marginRight: 8, verticalAlign: -3 }} />
            Bekleyenler
            {!!pendingStart && pendingStart.length > 0 && (
              <span
                style={{
                  marginLeft: 8, background: mainView === 'pending' ? 'rgba(255,255,255,0.28)' : 'var(--amber-light)',
                  color: mainView === 'pending' ? '#fff' : 'var(--amber)',
                  borderRadius: 999, padding: '1px 9px', fontSize: 12.5, fontWeight: 700,
                }}
              >
                {pendingStart.length}
              </span>
            )}
          </button>
        </div>
      )}

      {mainView === 'pending' && !!perms.start_maintenance && (
        <div className="card fade-in" style={{ marginBottom: 16 }}>
          <div className="card-head">
            <div>
              <div className="card-title">
                <PlayCircle size={15} style={{ marginRight: 6, verticalAlign: -3, color: 'var(--primary-600)' }} />
                Başlatılmayı Bekleyenler
              </div>
            </div>
            <Badge tone="pending">{pendingStart?.length ?? 0}</Badge>
          </div>
          {pendingStartError && <div className="error">{pendingStartError}</div>}
          {pendingStart && pendingStart.length === 0 && (
            <EmptyState
              icon={<PlayCircle size={22} />}
              title="Başlatılmayı bekleyen bakım yok"
              description="Otomatik açılan tüm adaylar zaten başlatılmış."
            />
          )}
          {pendingStart && pendingStart.length > 0 && (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Araç</th>
                    <th>Periyot</th>
                    <th>Hedef KM</th>
                    <th>Açılış KM</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {pendingStart.map((m) => (
                    <tr key={m.id}>
                      <td><span className="vehicle-code">{m.vehicle.code}</span> {m.vehicle.name}</td>
                      <td>{m.plan.name}</td>
                      <td>{hedefLabel(m)}</td>
                      <td>{fmtKm(m.km_at_open)} km</td>
                      <td>
                        <button
                          className="small primary"
                          disabled={pendingStartBusy === m.id}
                          onClick={() => startPendingMaintenance(m.id)}
                        >
                          <PlayCircle size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                          {pendingStartBusy === m.id ? 'Başlatılıyor…' : 'Başlat'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {mainView === 'started' && (
      <>
      {listError && <div className="error" style={{ marginBottom: 12 }}>{listError}</div>}

      <div className="table-card fade-in maintenance-list-card">
        <div className="table-toolbar maintenance-toolbar">
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
          <span className="maintenance-record-count">
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
                  <td>{hedefLabel(m)}</td>
                  <td><Badge tone={m.status}>{STATUS_TR[m.status]}</Badge></td>
                  <td>
                    {window ? (
                      <Badge tone={window.overdue ? 'danger' : window.earliestReached ? 'in_progress' : 'neutral'}>
                        {window.overdue
                          ? `Süresi geçti · ${fmtKm(Math.abs(window.remainingKm))} km geçildi (en geç ${fmtDate(window.latest)} tarihindeydi)`
                          : window.earliestReached
                            ? `En geç ${fmtDate(window.latest)} · ${fmtKm(window.remainingKm)} km kaldı`
                            : `Kalan ${fmtKm(window.remainingKm)} km · en geç ${fmtDate(window.latest)}`}
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
      )}

      {!!perms.delete_all_maintenance && (
        <div className="card fade-in" style={{ marginTop: 20, borderColor: 'var(--red)', background: 'var(--red-light)' }}>
          <div className="card-head">
            <div>
              <div className="card-title" style={{ color: 'var(--red)' }}>
                <ShieldAlert size={15} style={{ marginRight: 6, verticalAlign: -3 }} />
                Tehlikeli Bölge
              </div>
              <div className="card-subtitle">Tüm bakım iş emirleri kalıcı olarak silinir. Bu işlem geri alınamaz.</div>
            </div>
          </div>
          {dangerError && <div className="error">{dangerError}</div>}
          {!dangerOpen ? (
            <button className="small danger" onClick={() => setDangerOpen(true)}>
              <Trash2 size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
              Tüm Bakım İş Emirlerini Sil
            </button>
          ) : (
            <div className="row" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
              <span style={{ fontSize: 13 }}>
                Onaylamak için kutuya <strong>SİL</strong> yazın:
              </span>
              <input
                style={{ width: 100 }}
                value={dangerConfirmText}
                onChange={(e) => setDangerConfirmText(e.target.value)}
                placeholder="SİL"
                autoFocus
              />
              <button
                className="small danger"
                disabled={dangerBusy || dangerConfirmText.trim().toUpperCase() !== 'SİL'}
                onClick={deleteAllMaintenances}
              >
                {dangerBusy ? 'Siliniyor…' : 'Kalıcı Olarak Sil'}
              </button>
              <button
                className="small"
                type="button"
                onClick={() => { setDangerOpen(false); setDangerConfirmText(''); setDangerError('') }}
              >
                Vazgeç
              </button>
            </div>
          )}
        </div>
      )}

      {manualOpen && (
        <div
          style={{
            position: 'fixed', inset: 0, background: 'rgba(15, 23, 42, 0.45)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 200,
          }}
          onClick={() => !manualBusy && setManualOpen(false)}
        >
          <div
            className="card"
            style={{ width: 420, maxWidth: '90vw' }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ marginTop: 0, fontSize: 15, display: 'flex', alignItems: 'center', gap: 6 }}>
              <Plus size={15} /> Manuel Bakım Ekle
            </h3>
            <div className="muted" style={{ fontSize: 12.5, marginBottom: 14 }}>
              Otomatik tespiti beklemeden, seçtiğiniz araç ve periyot için hedef km'yi kendiniz
              belirleyerek doğrudan aktif bir iş emri açar.
            </div>
            {manualError && <div className="error" style={{ marginBottom: 10 }}>{manualError}</div>}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <label style={{ fontSize: 12.5 }}>
                <div className="muted" style={{ marginBottom: 4 }}>Araç</div>
                <select
                  style={{ width: '100%' }}
                  value={manualVehicleId}
                  onChange={(e) => {
                    setManualVehicleId(e.target.value ? Number(e.target.value) : '')
                    setManualTargetKm('')
                  }}
                >
                  <option value="">Araç seçin…</option>
                  {manualVehicles.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.code} — {v.name} ({fmtKm(v.current_km)} km)
                    </option>
                  ))}
                </select>
              </label>
              <label style={{ fontSize: 12.5 }}>
                <div className="muted" style={{ marginBottom: 4 }}>Periyot / Plan</div>
                <select
                  style={{ width: '100%' }}
                  value={manualPlanId}
                  onChange={(e) => {
                    const id = e.target.value ? Number(e.target.value) : ''
                    setManualPlanId(id)
                    setManualTargetKm('')
                    setManualTargetDate('')
                    const plan = manualPlans.find((p) => p.id === id)
                    if (manualVehicle && plan && plan.plan_type === 'km' && plan.interval_km > 0) {
                      setManualTargetKm(
                        String(Math.ceil((manualVehicle.current_km + 1) / plan.interval_km) * plan.interval_km),
                      )
                    }
                  }}
                >
                  <option value="">Periyot seçin…</option>
                  {manualPlans.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name} ({planPeriodLabel(p)}{p.vehicle_model ? ` · ${p.vehicle_model}` : ''})
                    </option>
                  ))}
                </select>
              </label>
              {manualPlan?.plan_type === 'time' ? (
                <label style={{ fontSize: 12.5 }}>
                  <div className="muted" style={{ marginBottom: 4 }}>Hedef Tarih</div>
                  <input
                    type="date"
                    style={{ width: '100%' }}
                    value={manualTargetDate}
                    onChange={(e) => setManualTargetDate(e.target.value)}
                  />
                </label>
              ) : (
                <label style={{ fontSize: 12.5 }}>
                  <div className="muted" style={{ marginBottom: 4 }}>
                    Hedef KM
                    {manualSuggestedKm !== null && (
                      <span style={{ marginLeft: 6 }}>
                        (önerilen: {fmtKm(manualSuggestedKm)} km)
                      </span>
                    )}
                  </div>
                  <input
                    type="number"
                    style={{ width: '100%' }}
                    value={manualTargetKm}
                    onChange={(e) => setManualTargetKm(e.target.value)}
                    placeholder="Örn. 125000"
                    min={1}
                  />
                </label>
              )}
            </div>
            <div className="row" style={{ marginTop: 16, gap: 8, justifyContent: 'flex-end' }}>
              <button className="ghost" disabled={manualBusy} onClick={() => setManualOpen(false)}>
                Vazgeç
              </button>
              <button
                className="primary"
                disabled={
                  manualBusy ||
                  manualVehicleId === '' ||
                  manualPlanId === '' ||
                  (manualPlan?.plan_type === 'time'
                    ? !manualTargetDate.trim()
                    : !manualTargetKm.trim() || Number(manualTargetKm) <= 0)
                }
                onClick={submitManualMaintenance}
              >
                {manualBusy ? 'Ekleniyor…' : 'İş Emrini Oluştur'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
