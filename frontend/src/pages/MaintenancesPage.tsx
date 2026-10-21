import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, apiDownload, apiUpload, apiUploadWithFields } from '../api'
import { useLiveEvent } from '../liveEvents'
import { showToast } from '../components/toast'
import { rawTargetLabel, mergedPeriodLabel, topPeriodLabel } from '../format'
import { pushDetail, useDetailPopState } from '../useDetailHistory'
import Tram3D from '../components/Tram3D'
import MaintenanceCalendar from '../components/MaintenanceCalendar'
import type { Item, Maintenance, MaintenanceNote, Model3DWithStatus, NodeAttachment, Plan, Shift, ShiftModeConfig, UpcomingItem, User, Vehicle } from '../types'
import { STATUS_TR } from '../types'
import {
  PageHeader,
  Badge,
  Avatar,
  EmptyState,
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
  Camera,
  LinkIcon,
  StickyNote,
  UserPlus,
  UserMinus,
  Send,
  AlertTriangle,
  Users,
  ShieldAlert,
  Trash2,
  Plus,
  Search,
  X,
  CheckCircle2,
  RotateCcw,
  RefreshCw,
  PlayCircle,
  Pencil,
  Check,
  CalendarClock,
  XCircle,
  BookOpen,
} from 'lucide-react'

// Paylaşılan tablet üzerinde vardiya seçimi, aynı tarayıcıda sayfa
// yenilense/gezinilse de hatırlansın diye localStorage'da tutulur.
const ACTIVE_SHIFT_STORAGE_KEY = 'ulasim_bakim_active_shift_id'

// Usta bakım takviminde geriye dönük kaç aylık TAMAMLANMIŞ bakımın
// yükleneceği. Takvim ay ay gezilebildiği için bir sınır gerekiyor; daha
// eskisi için Bakım Geçmişi sayfası kullanılır.
const CALENDAR_HISTORY_MONTHS = 12

// ---------------------------------------------------------- Madde ekleri (Faz 10)
function AttachmentChip({ att }: { att: NodeAttachment }) {
  const href = att.kind === 'link' || (att.kind === 'video' && !att.filename)
    ? att.url
    : `/uploads/attachments/${att.filename}`
  const icon =
    att.kind === 'image' ? <ImageIcon size={13} /> :
    att.kind === 'pdf' ? <FileText size={13} /> :
    att.kind === 'video' ? <PlayCircle size={13} /> :
    att.kind === 'link' ? <LinkIcon size={13} /> : <Download size={13} />
  const text = att.label || att.original_name || att.url || 'Ek'
  return (
    <a className="attach-chip" href={href} target="_blank" rel="noreferrer" title={text}>
      {icon}
      <span>{text}</span>
    </a>
  )
}

function ItemExtras({
  item,
  canEdit,
  onDeletePhoto,
}: {
  item: Item
  // Faz 21 — bakım sırasında eklenen görsellerin silinebilmesi için
  // (yalnızca işaretleme yetkisi olanlara gösterilir). Şablon ağacı
  // editörü gibi canEdit geçmeyen çağıranlarda opsiyoneldir.
  canEdit?: boolean
  onDeletePhoto?: (attachmentId: number) => void
}) {
  const hasNote = !!item.note
  const images = item.attachments.filter((a) => a.kind === 'image')
  const others = item.attachments.filter((a) => a.kind !== 'image')
  const execPhotos = item.exec_attachments ?? []
  if (!hasNote && item.attachments.length === 0 && execPhotos.length === 0) return null
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
      {execPhotos.length > 0 && (
        <div className="exec-attach-thumbs">
          {execPhotos.map((a) => (
            <div key={a.id} className="exec-attach-thumb-wrap">
              <a
                href={`/uploads/attachments/${a.filename}`}
                target="_blank"
                rel="noreferrer"
                title={`${a.original_name}${a.uploaded_by ? ' — ' + a.uploaded_by.full_name : ''}`}
              >
                <img src={`/uploads/attachments/${a.filename}`} alt={a.original_name} />
              </a>
              {canEdit && onDeletePhoto && (
                <button
                  type="button"
                  className="exec-attach-delete"
                  title="Görseli sil"
                  onClick={() => onDeletePhoto(a.id)}
                >
                  <X size={11} />
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const fmt = (s: string | null) => (s ? new Date(s).toLocaleString('tr-TR') : '—')
const fmtKm = (n: number) => n.toLocaleString('tr-TR')

async function askCompletedKm(
  maintenanceId: number,
  isKmPlan: boolean,
  existingKm?: number | null,
): Promise<boolean> {
  if (!isKmPlan) return true
  const value = window.prompt(
    'Bakımın yapıldığı kilometreyi girin (isteğe bağlı). Boş bırakırsanız kayıtlı araç kilometresi kullanılır.',
    existingKm?.toString() ?? '',
  )
  if (value === null) return false
  if (!value.trim()) return true

  const km = Number(value)
  if (!Number.isSafeInteger(km) || km <= 0) {
    throw new Error('Geçerli bir tam kilometre giriniz')
  }
  await api(`/maintenances/${maintenanceId}/completed-km`, {
    method: 'PATCH',
    body: { completed_km: km },
  })
  return true
}

// Süre bazlı (aylık/yıllık) iş emirlerinde gerçek hedef bir km değil takvim
// tarihidir (target_date) — backend km alanını bu durumda gizler (null).
// Tablo/başlık gösterimlerinde ikisini de doğru biçimde tek bir metne çevirir.
// Kullanıcı isteği: hedef km HER ZAMAN tam km olarak yazılsın (Sirio kısa
// M/Y kodu KULLANILMASIN — ör. "1.500.000 km", asla "150M").
const hedefLabel = (m: Maintenance) => rawTargetLabel(m.target_km, m.target_date)

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

const upcomingRemainingLabel = (item: UpcomingItem) =>
  item.remaining_km < 0
    ? `${fmtKm(Math.abs(item.remaining_km))} km geçti`
    : `${fmtKm(item.remaining_km)} km kaldı`

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
  onAddPhoto,
  onDeletePhoto,
  onOpenKnowledge,
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
  // Faz 21 — bakım sırasında tablet kamerasıyla/galeriden görsel ekleme.
  onAddPhoto: (itemId: number, file: File) => void
  onDeletePhoto: (attachmentId: number) => void
  // Faz 27 — "Bilgi" düğmesi: maddeyi Bakım Bilgi Bankası'nda açar (video /
  // talimat / açıklama). Tikleme akışına DOKUNMAZ, yanına eklenir.
  onOpenKnowledge?: (nodeId: number) => void
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
  // Faz 21 — iki ayrı gizli dosya girişi: biri (capture'sız) galeriden
  // seçim için, diğeri (capture="environment") doğrudan kamerayı açmak
  // için. Tek bir girişte capture verilince Android'de galeri seçeneği
  // tamamen kayboluyordu (yalnızca kamera açılıyordu) — kullanıcı isteği
  // üzerine ikisi de ayrı ayrı sunuluyor.
  const cameraInputRef = useRef<HTMLInputElement>(null)
  const galleryInputRef = useRef<HTMLInputElement>(null)
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
        {/* Maddede birden fazla kişi çalıştıysa hepsi gösterilir; eski
            kayıtlarda workers boş olduğu için completed_by'a düşülür. */}
        {item.workers && item.workers.length > 0 ? (
          <span className="who" title={`${item.workers.map((w) => w.full_name).join(', ')} · ${fmt(item.completed_at)}`}>
            {item.workers.slice(0, 3).map((w) => (
              <Avatar key={w.id} name={w.full_name} size={20} />
            ))}
            {item.workers.length === 1
              ? item.workers[0].full_name
              : `${item.workers[0].full_name} +${item.workers.length - 1}`}
            {' · '}{fmt(item.completed_at)}
          </span>
        ) : item.completed_by ? (
          <span className="who">
            <Avatar name={item.completed_by.full_name} size={20} />
            {item.completed_by.full_name} · {fmt(item.completed_at)}
          </span>
        ) : null}
        {isLeaf && item.is_completed && !item.completed_by && (
          <span className="who muted" title="Bu madde zorunlu değil işaretlendiği için bakım açılırken otomatik tiklendi">
            Otomatik (zorunlu değil)
          </span>
        )}
        {isLeaf && onOpenKnowledge && item.source_node_id != null && (
          <button
            className="small ghost"
            title="Bu maddenin bilgi kartını aç — eğitim videosu, talimat ve açıklama"
            onClick={() => onOpenKnowledge(item.source_node_id!)}
          >
            <BookOpen size={12} style={{ marginRight: 4, verticalAlign: -2 }} />
            Bilgi
          </button>
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
        {isLeaf && canEdit && (
          <>
            <button
              className="small ghost"
              title="Kamerayla fotoğraf çek"
              onClick={() => cameraInputRef.current?.click()}
            >
              <Camera size={12} style={{ marginRight: 4, verticalAlign: -2 }} />
              Çekerek Yükle
            </button>
            <button
              className="small ghost"
              title="Galeriden mevcut bir görsel seç"
              onClick={() => galleryInputRef.current?.click()}
            >
              <ImageIcon size={12} style={{ marginRight: 4, verticalAlign: -2 }} />
              Görsel Ekle
            </button>
            <input
              ref={cameraInputRef}
              type="file"
              accept="image/*"
              capture="environment"
              style={{ display: 'none' }}
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) onAddPhoto(item.id, f)
                e.target.value = ''
              }}
            />
            <input
              ref={galleryInputRef}
              type="file"
              accept="image/*"
              style={{ display: 'none' }}
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) onAddPhoto(item.id, f)
                e.target.value = ''
              }}
            />
          </>
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
      <ItemExtras item={item} canEdit={canEdit} onDeletePhoto={onDeletePhoto} />
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
                  onAddPhoto={onAddPhoto}
                  onDeletePhoto={onDeletePhoto}
                  onOpenKnowledge={onOpenKnowledge}
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
  shiftMembersVersion,
  shifts,
  activeWorkerIds,
  onOpenKnowledge,
}: {
  id: number
  user: User
  perms: Record<string, boolean>
  onBack: () => void
  onDeleted: () => void
  // Vardiyalı çalışma kapalıyken not ekleme seçici göstermeden doğrudan
  // oturum sahibine (user) atfedilir. Açıkken seçici yalnızca
  // activeShiftId'deki kişileri listeler. Madde tamamlama artık vardiya
  // modundan bağımsız her zaman doğrudan oturum sahibine atfedilir (bkz.
  // requestComplete) — kullanıcı isteği: "vardiya atandıktan sonra tek tek
  // tiklerken isim seçme olmasın, direkt giriş yapmış kullanıcı gözüksün."
  shiftModeEnabled: boolean
  activeShiftId: number | null
  // Vardiya üyeliği değişince (Bakımlar sayfasındaki vardiya barından üye
  // ekle/çıkar) isim seçici listesini hemen tazelemek için sayaç.
  shiftMembersVersion: number
  // "Vardiya" ataması (m.assigned_shift) için seçenek listesi — "Atanan"
  // (kişi) dropdown'undan bağımsız, ayrı bir satırda gösterilir.
  shifts: Shift[]
  // Kullanıcı isteği: "vardiya sorumlusu her tikte isim seçmek yerine
  // vardiyasındaki kendini ve diğer kişileri yukarıda vardiya kısmında
  // görüyor ya oradan bir isme tıklasın o an tıkladığı isim için tikliyor
  // olsun." Üstteki vardiya barında (bkz. shiftBar) tıklanarak seçilmiş
  // kişi(ler) — yalnızca vardiya sorumlusu/yönetici bunu değiştirebilir;
  // sıradan üyeler için her zaman boş kalır ve tikler kendi adlarına gider.
  activeWorkerIds: number[]
  // Faz 27 — madde satırındaki "Bilgi" düğmesi (Bakım Bilgi Bankası).
  onOpenKnowledge?: (nodeId: number) => void
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
  const [shiftAssignBusy, setShiftAssignBusy] = useState(false)
  // Bakım yapılırken girilen güncel km (yalnızca km bazlı planlarda) — bkz.
  // saveCompletedKm.
  const [completedKmInput, setCompletedKmInput] = useState('')
  const [completedKmBusy, setCompletedKmBusy] = useState(false)
  // Vardiyalı çalışma AÇIKKEN "kim tikledi / kim not ekledi" seçicilerinde
  // yalnızca o an seçili vardiyadaki kişiler listelenir (assignableUsers'tan
  // ayrı tutulur — "Atanan" (iş emri sorumlusu) dropdown'u vardiyadan
  // bağımsız olarak her zaman herkesi gösterir).
  const [pickerUsers, setPickerUsers] = useState<User[]>([])
  const [noteText, setNoteText] = useState('')
  const [noteBusy, setNoteBusy] = useState(false)
  // Usta/mühendisin, bakım sırasında tikledikleri bir alt madde için
  // eklediği/düzenlediği not (madde şablonundaki admin notundan bağımsız).
  const [itemNoteId, setItemNoteId] = useState<number | null>(null)
  const [itemNoteText, setItemNoteText] = useState('')
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

  // Kullanıcı isteği: "başkasının vardiyasına atanan bakım, başka vardiyadaki
  // kişi tarafından açılıp da yanlışlıkla tiklenmesin." İş emri bir vardiyaya
  // atanmışsa ve kullanıcı o vardiyada değilse madde işaretleme kapatılır
  // (bilgi amaçlı görüntüleme serbest). Yönetici/mühendis/amir muaftır —
  // hatalı tikleri düzeltmek zorunda kalabilirler. Backend de aynı kuralı
  // ayrıca uygular (bkz. POST /api/items/{id}/complete), yani arayüz
  // atlatılsa bile kayıt oluşmaz.
  const shiftLocked =
    shiftModeEnabled &&
    !isStaff &&
    m?.assigned_shift != null &&
    user.shift_id !== m.assigned_shift.id

  const load = useCallback(() => {
    api<Maintenance>(`/maintenances/${id}`).then(setM).catch((e) => setError(e.message))
    api<Model3DWithStatus[]>(`/maintenances/${id}/model3d`).then(setModels3d).catch(() => setModels3d([]))
  }, [id])

  useEffect(load, [load])
  useLiveEvent(['maintenance', 'vehicle', 'plan', 'tree_node', 'attachment'], load)

  useEffect(() => {
    const interval = setInterval(load, 30000)
    return () => clearInterval(interval)
  }, [load])

  useEffect(() => {
    // Teknisyenlerin de kendi aralarındaki isim listesini görebilmesi
    // gerekir ("Atanan" dropdown'u için — vardiyadan bağımsız, herkes).
    api<User[]>('/users/assignable').then(setAssignableUsers).catch(() => setAssignableUsers([]))
  }, [])

  useEffect(() => {
    // Vardiyalı çalışma kapalıyken bu listeye gerek yok (seçici zaten
    // gösterilmiyor). Açıkken, seçili vardiya değiştikçe yeniden çekilir.
    // Kullanıcı isteği: "bakım içerisinde sadece aynı vardiya içindeki
    // kişiler gözüksün." Bu yüzden liste, ekranda seçili vardiyadan DEĞİL,
    // İŞ EMRİNİN ATANDIĞI vardiyadan gelir; iş emri bir vardiyaya atanmamışsa
    // ekrandaki seçili vardiyaya düşülür.
    const sourceShiftId = m?.assigned_shift?.id ?? activeShiftId
    if (!shiftModeEnabled || sourceShiftId == null) {
      setPickerUsers([])
      return
    }
    api<User[]>(`/users/assignable?shift_id=${sourceShiftId}`)
      .then(setPickerUsers)
      .catch(() => setPickerUsers([]))
  }, [shiftModeEnabled, activeShiftId, shiftMembersVersion, m?.assigned_shift?.id])

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

  // "Atanan" (kişi) alanından bağımsız — iş emrini bir vardiyaya atar.
  async function assignShift(shiftId: number | null) {
    setError('')
    setShiftAssignBusy(true)
    try {
      await api(`/maintenances/${id}/assign-shift`, { method: 'PATCH', body: { assigned_shift_id: shiftId } })
      load()
      showToast('✓ Vardiya ataması güncellendi')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setShiftAssignBusy(false)
    }
  }

  // Kullanıcı isteği: "bakım yapılırken bakım yapan usta veya kişi
  // tarafından o anki güncel km girme olsun." Girilen km, hedef km'ye göre
  // sapma yüzdesini (backend'de hesaplanır) belirler ve gerekirse aracın
  // takip edilen km'sini de ileri taşır.
  async function saveCompletedKm() {
    const km = Number(completedKmInput)
    if (!completedKmInput || Number.isNaN(km) || km <= 0) {
      setError('Geçerli bir km giriniz')
      return
    }
    setError('')
    setCompletedKmBusy(true)
    try {
      await api(`/maintenances/${id}/completed-km`, { method: 'PATCH', body: { completed_km: km } })
      setCompletedKmInput('')
      load()
      showToast('✓ Tamamlanan km kaydedildi')
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setCompletedKmBusy(false)
    }
  }

  // Kullanıcı isteği: artık tiklerken açılan bir "kimler yaptı?" penceresi
  // YOK. Sıradan bir vardiya üyesi zaten başkasını seçemediği için hiç
  // sorulmaz — tik doğrudan kendi adına kaydedilir. Vardiya sorumlusu (ve
  // yönetici/mühendis/amir) ise üstteki vardiya barından (bkz. shiftBar)
  // önceden TIKLAYARAK seçtiği kişi(ler) adına tikler; bu seçim başka bir
  // isme tıklanana kadar sonraki tüm tiklemelerde aynen kullanılır — her
  // tikte yeniden isim sormaz. Kimse seçilmemişse (varsayılan) yine kendi
  // adına kaydedilir.
  const pickerShift =
    shifts.find((s) => s.id === (m?.assigned_shift?.id ?? activeShiftId)) ?? null
  const canPickOthers = isStaff || pickerShift?.supervisor?.id === user.id
  const selectableWorkers = canPickOthers
    ? pickerUsers
    : pickerUsers.filter((u) => u.id === user.id)

  function requestComplete(itemId: number) {
    setError('')
    if (!canPickOthers) {
      void submitComplete(itemId, [])
      return
    }
    // Üstteki vardiya barında seçili kişilerden yalnızca bu madde için
    // geçerli olanlar (aynı vardiyada olanlar) kullanılır.
    const ids = activeWorkerIds.filter((wid) => selectableWorkers.some((u) => u.id === wid))
    void submitComplete(itemId, ids)
  }

  async function submitComplete(itemId: number, workerIds: number[]) {
    try {
      const item = findItemById(m?.items ?? [], itemId)
      const [done, total] = countLeaves(m?.items ?? [])
      const completesMaintenance = item?.children.length === 0 && done + 1 >= total
      if (
        completesMaintenance &&
        m &&
        !(await askCompletedKm(m.id, m.plan.plan_type === 'km', m.completed_km))
      ) return
      await api(`/items/${itemId}/complete`, {
        method: 'POST',
        body: workerIds.length ? { worker_ids: workerIds } : {},
      })
      load()
      showToast('✓ Kaydedildi')
    } catch (e) {
      setError((e as Error).message)
    }
  }

  // Bir alt maddeye bakım sırasında not eklemek/düzenlemek için diyaloğu
  // açar; var olan notu (varsa) önceden doldurur.
  //
  // Kullanıcı isteği: "kim eklediyse o notu onun adı gözüksün" — eskiden
  // vardiyalı çalışmada notu kimin yazdığı bir listeden seçiliyordu ve yanlış
  // isim seçilebiliyordu. Artık seçim yok: not her zaman OTURUMU AÇAN kişiye
  // yazılır (author_id gönderilmez, backend oturum sahibini kullanır).
  function openItemNote(itemId: number) {
    setError('')
    const existing = m ? findItemById(m.items ?? [], itemId) : null
    setItemNoteText(existing?.exec_note ?? '')
    setItemNoteHadExisting(!!existing?.exec_note)
    setItemNoteId(itemId)
  }

  async function saveItemNote() {
    if (itemNoteId === null) return
    setItemNoteBusy(true)
    setError('')
    try {
      await api(`/items/${itemNoteId}/note`, {
        method: 'PATCH',
        body: { text: itemNoteText.trim() },
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

  // Faz 21 — usta/mühendis/amir bir maddeyi tikleyip incelerken tablet
  // kamerasıyla (veya galeriden) fotoğraf ekleyebilir. Not/tamamlama
  // işaretlerinden farklı olarak burada kasıtlı olarak vardiya bazlı "kim
  // ekledi" seçici AÇILMAZ — fotoğraf her zaman oturum sahibine atfedilir;
  // paylaşılan tablette bu basitlik yeterli (dosyanın kendisi zaten kanıt).
  async function uploadItemPhoto(itemId: number, file: File) {
    setError('')
    try {
      await apiUploadWithFields(`/items/${itemId}/exec-attachments`, file, {})
      load()
      showToast('✓ Görsel eklendi')
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function deleteItemPhoto(attId: number) {
    setError('')
    try {
      await api(`/item-exec-attachments/${attId}`, { method: 'DELETE' })
      load()
      showToast('✓ Görsel silindi')
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function removeItemNote() {
    if (itemNoteId === null) return
    setItemNoteBusy(true)
    setError('')
    try {
      await api(`/items/${itemNoteId}/note`, {
        method: 'PATCH',
        body: { text: '' },
      })
      setItemNoteId(null)
      load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setItemNoteBusy(false)
    }
  }

  // Aynı gerekçe (bkz. openItemNote): iş emri notu da her zaman oturumu açan
  // kişiye yazılır, isim listeden seçilmez — yanlış isim kaydedilemesin.
  // ------------------------------------------- nota fotoğraf ekleme (kuyruk)
  // Fotoğraflar notun KENDİSİNE bağlandığı için önce not oluşturulmalı, sonra
  // yüklenmeli. Bu yüzden kullanıcının seçtiği/sürüklediği dosyalar burada
  // bir kuyrukta bekletilir; "Ekle" denince önce not açılır, dönen not
  // id'siyle fotoğraflar sırayla yüklenir.
  const [notePhotos, setNotePhotos] = useState<File[]>([])
  const [noteDragOver, setNoteDragOver] = useState(false)
  const notePhotoRef = useRef<HTMLInputElement>(null)

  /** Sürüklenen/seçilen dosyalardan yalnızca resimleri kuyruğa alır. */
  function queueNotePhotos(files: FileList | null) {
    if (!files) return
    const images = Array.from(files).filter((f) => f.type.startsWith('image/'))
    if (images.length === 0) {
      setError('Yalnızca fotoğraf eklenebilir')
      return
    }
    setError('')
    setNotePhotos((prev) => [...prev, ...images])
  }

  function removeQueuedPhoto(index: number) {
    setNotePhotos((prev) => prev.filter((_, i) => i !== index))
  }

  // Aynı gerekçe (bkz. openItemNote): iş emri notu da her zaman oturumu açan
  // kişiye yazılır, isim listeden seçilmez — yanlış isim kaydedilemesin.
  async function addNote() {
    // Metin ya da en az bir fotoğraf gerekli — ikisi de yoksa boş not açılmaz.
    if (!noteText.trim() && notePhotos.length === 0) return
    setNoteBusy(true)
    setError('')
    try {
      const note = await api<MaintenanceNote>(`/maintenances/${id}/notes`, {
        method: 'POST',
        body: { text: noteText.trim() },
      })
      // Fotoğraflar sırayla yüklenir; biri hata verirse kullanıcı görsün diye
      // tamamı beklenir (not zaten oluşmuş olur, tekrar denemek gerekmez).
      for (const photo of notePhotos) {
        await apiUpload(`/notes/${note.id}/photos`, photo)
      }
      setNoteText('')
      setNotePhotos([])
      load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setNoteBusy(false)
      if (notePhotoRef.current) notePhotoRef.current.value = ''
    }
  }

  async function deleteNotePhoto(photoId: number) {
    if (!confirm('Bu fotoğraf silinsin mi?')) return
    setError('')
    try {
      await api(`/note-photos/${photoId}`, { method: 'DELETE' })
      load()
    } catch (e) {
      setError((e as Error).message)
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
      if (!(await askCompletedKm(m.id, m.plan.plan_type === 'km', m.completed_km))) return
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

  async function resyncTiers() {
    if (!m) return
    setError('')
    setStatusActionBusy(true)
    try {
      const result = await api<{
        added_items: number
        merged_periods: number[]
        diagnostics?: {
          name: string
          interval_km: number
          vehicle_model: string
          is_active: boolean
          is_catalog_only: boolean
          divides_target: boolean
          excluded_reason: string | null
        }[]
      }>(`/maintenances/${m.id}/resync-tiers`, { method: 'POST' })
      load()
      if (result.added_items > 0) {
        showToast(`✓ Eksik periyot eklendi (${result.added_items} yeni madde)`, 'info')
      } else {
        showToast('Eksik periyot bulunamadı, checklist zaten güncel', 'info')
        // Teşhis: bu araç modeliyle eşleşen HER km planının neden dahil
        // olup olmadığını göster — "3M gelmiyor" gibi durumlarda hangi
        // planın (pasif/katalog-amaçlı/hedefe bölünmüyor) dışlandığını
        // tek bakışta görmek için (bkz. services._diagnose_missing_tiers).
        const diag = result.diagnostics ?? []
        if (diag.length) {
          const lines = diag.map((d) => {
            const model = d.vehicle_model ? `[${d.vehicle_model}]` : '[Genel]'
            const status = d.excluded_reason ? `DIŞLANDI — ${d.excluded_reason}` : 'zaten dahil'
            return `${d.name} ${model} — ${d.interval_km.toLocaleString('tr-TR')} km — ${status}`
          })
          alert(`Bu araç modeliyle eşleşen km planları:\n\n${lines.join('\n')}`)
        }
      }
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
            {m.vehicle.code} — {mergedPeriodLabel(m.covered_tiers, m.plan.plan_type, m.vehicle.model, m.plan.name)}
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
            {!!perms.create_maintenance && m.status !== 'completed' && m.plan.plan_type === 'km' && (
              <button
                className="small"
                title="Bu iş emrinin hedef km'sinin kapsadığı ama (sonradan eklenen bir plan yüzünden) checklist'te eksik kalmış periyotların maddelerini ekler. Tamamlanmış maddelere dokunmaz."
                disabled={statusActionBusy}
                onClick={resyncTiers}
              >
                <RefreshCw size={13} style={{ marginRight: 5, verticalAlign: -2 }} />
                Periyotları Yeniden Hizala
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
            {!!perms.export_excel && (
              <button
                className="small"
                title="Kim hangi maddeyi ne zaman tamamladı, varsa hangi notu ekledi — Excel olarak indir"
                onClick={() => apiDownload(`/maintenances/${m.id}/export-items?format=xlsx`, `is_emri_${m.id}_maddeler.xlsx`)}
              >
                <Download size={13} style={{ marginRight: 5, verticalAlign: -2 }} />
                Excel'e Aktar
              </button>
            )}
          </div>
        </div>
        <div className="muted" style={{ margin: '10px 0' }}>
          İş Emri #{m.id} · {hedefLabel(m)}'de açıldı · Oluşturma: {fmt(m.created_at)} ·
          Başlangıç: {fmt(m.started_at)} · Bitiş: {fmt(m.completed_at)} · Açılış KM: {fmtKm(m.km_at_open)} ·{' '}
          <span title={m.vehicle.last_km_update_at ? `Son güncelleme: ${fmt(m.vehicle.last_km_update_at)}` : undefined}>
            Aracın Anlık KM'si: <strong>{fmtKm(m.vehicle.current_km)}</strong>
          </span>
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
        <div className="row" style={{ marginBottom: 10, gap: 8 }}>
          <span className="muted" style={{ fontSize: 13 }}>Vardiya:</span>
          {isStaff ? (
            <select
              value={m.assigned_shift?.id ?? ''}
              disabled={shiftAssignBusy}
              onChange={(e) => assignShift(e.target.value ? Number(e.target.value) : null)}
              style={{ fontSize: 12.5, padding: '5px 8px' }}
            >
              <option value="">Atanmamış</option>
              {shifts.map((s) => (
                <option key={s.id} value={s.id}>{s.name}</option>
              ))}
            </select>
          ) : (
            <span className="muted" style={{ fontSize: 13 }}>{m.assigned_shift?.name || 'Atanmamış'}</span>
          )}
        </div>
        {m.plan.plan_type === 'km' && (
          <div className="row" style={{ marginBottom: 10, gap: 8, flexWrap: 'wrap' }}>
            <span className="muted" style={{ fontSize: 13 }}>Tamamlanan KM:</span>
            {m.completed_km != null ? (
              <span style={{ fontWeight: 600, fontSize: 13 }}>{fmtKm(m.completed_km)}</span>
            ) : (
              <span className="muted" style={{ fontSize: 13 }}>Girilmedi</span>
            )}
            {m.km_deviation_pct != null && (
              <Badge tone={m.km_deviation_pct >= -5 && m.km_deviation_pct <= 10 ? 'completed' : 'danger'}>
                Sapma {m.km_deviation_pct > 0 ? '+' : ''}{m.km_deviation_pct}%
              </Badge>
            )}
            {(isStaff || (isTechnician && (!m.assigned_to || m.assigned_to.id === user.id))) && (
              <>
                <input
                  type="number"
                  min={1}
                  placeholder="Güncel km"
                  value={completedKmInput}
                  onChange={(e) => setCompletedKmInput(e.target.value)}
                  disabled={completedKmBusy}
                  style={{ fontSize: 12.5, padding: '5px 8px', width: 120 }}
                />
                <button className="small" disabled={completedKmBusy} onClick={saveCompletedKm}>
                  Kaydet
                </button>
              </>
            )}
          </div>
        )}
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
        {shiftLocked && (
          <div
            className="row"
            style={{
              gap: 8, alignItems: 'center', margin: '0 0 10px', padding: '9px 12px',
              borderRadius: 8, background: 'rgba(180, 83, 9, 0.10)',
              border: '1px solid rgba(180, 83, 9, 0.25)', fontSize: 12.5,
            }}
          >
            <ShieldAlert size={15} />
            <span>
              Bu iş emri <strong>{m?.assigned_shift?.name}</strong> vardiyasına atanmış. Kendi
              vardiyanız olmadığı için maddeleri işaretleyemezsiniz — yalnızca
              görüntüleyebilirsiniz.
            </span>
          </div>
        )}
        <ul className="tree">
          {displayedItems.map((i) => (
            <TreeItem
              key={i.id}
              item={i}
              canEdit={!!perms.edit_maintenance && !shiftLocked}
              canUnlock={!!perms.approve_maintenance}
              onComplete={requestComplete}
              onUnlock={unlock}
              onEditNote={openItemNote}
              onAddPhoto={uploadItemPhoto}
              onDeletePhoto={deleteItemPhoto}
              onOpenKnowledge={onOpenKnowledge}
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
                  {n.text && <div style={{ marginBottom: 4 }}>{n.text}</div>}
                  {(n.photos ?? []).length > 0 && (
                    <div className="note-photo-grid">
                      {(n.photos ?? []).map((ph) => (
                        <div key={ph.id} className="note-photo">
                          <a href={`/uploads/notes/${ph.filename}`} target="_blank" rel="noreferrer">
                            <img src={`/uploads/notes/${ph.filename}`} alt={ph.original_name} />
                          </a>
                          <button
                            className="note-photo-del"
                            title="Fotoğrafı sil"
                            onClick={() => deleteNotePhoto(ph.id)}
                          >
                            <Trash2 size={12} />
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="muted" style={{ fontSize: 11.5, display: 'flex', alignItems: 'center', gap: 5 }}>
                    <Avatar name={n.author?.full_name ?? '?'} size={16} />
                    {n.author?.full_name ?? 'Bilinmiyor'} · {fmt(n.created_at)}
                  </div>
                </li>
              ))}
            </ul>
          )}
          {/* Sürükle-bırak alanı: dosya buraya bırakılabilir, tıklanınca da
              kamera/galeri açılır. Tablette "çek ve bırak" akışı için. */}
          <div
            className={`note-drop${noteDragOver ? ' over' : ''}`}
            onDragOver={(e) => { e.preventDefault(); setNoteDragOver(true) }}
            onDragLeave={() => setNoteDragOver(false)}
            onDrop={(e) => {
              e.preventDefault()
              setNoteDragOver(false)
              queueNotePhotos(e.dataTransfer.files)
            }}
            onClick={() => notePhotoRef.current?.click()}
          >
            <Camera size={15} />
            <span>
              Fotoğrafı buraya sürükleyin ya da <strong>tıklayıp çekin</strong>
            </span>
            <input
              ref={notePhotoRef}
              type="file"
              accept="image/*"
              capture="environment"
              multiple
              style={{ display: 'none' }}
              onChange={(e) => queueNotePhotos(e.target.files)}
            />
          </div>
          {notePhotos.length > 0 && (
            <div className="note-photo-grid" style={{ marginTop: 10 }}>
              {notePhotos.map((f, i) => (
                <div key={`${f.name}-${i}`} className="note-photo">
                  <img src={URL.createObjectURL(f)} alt={f.name} />
                  <button
                    className="note-photo-del"
                    title="Kuyruktan çıkar"
                    onClick={() => removeQueuedPhoto(i)}
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              ))}
            </div>
          )}
          <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'flex-start', marginTop: 10 }}>
            <textarea
              value={noteText}
              onChange={(e) => setNoteText(e.target.value)}
              placeholder="Örn: Sağ ön fren balatası aşınmış, ayrıca kontrol edilmeli…"
              rows={2}
              style={{ flex: '1 1 260px', minWidth: 220, fontSize: 13, padding: '8px 10px' }}
            />
            <button
              className="primary"
              disabled={noteBusy || (!noteText.trim() && notePhotos.length === 0)}
              onClick={addNote}
            >
              <Send size={13} style={{ marginRight: 6, verticalAlign: -2 }} />
              {noteBusy
                ? 'Ekleniyor…'
                : notePhotos.length > 0
                  ? `Ekle (${notePhotos.length} foto)`
                  : 'Ekle'}
            </button>
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
            <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>
              Not, giriş yapmış kişi adına kaydedilir: <strong>{user.full_name}</strong>
            </div>
            <div className="row" style={{ marginTop: 14, gap: 8, justifyContent: 'flex-end' }}>
              <button className="ghost" disabled={itemNoteBusy} onClick={() => setItemNoteId(null)}>
                Vazgeç
              </button>
              {itemNoteHadExisting && itemNoteText === '' && (
                <button
                  className="danger"
                  disabled={itemNoteBusy}
                  onClick={removeItemNote}
                >
                  Notu Kaldır
                </button>
              )}
              <button
                className="primary"
                disabled={itemNoteBusy || !itemNoteText.trim()}
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
  onOpenVehicle,
  initialStatus,
  initialPlanName,
  onOpenKnowledge,
}: {
  user: User
  perms: Record<string, boolean>
  initialSelectedId?: number | null
  // Bakım Akışı satırına tıklayınca aracın kartını (Araçlar sayfası profili) açar.
  onOpenVehicle?: (vehicleId: number) => void
  // Dashboard'daki "Planlanan Bakım"/"Devam Eden" kartları ve periyot
  // grafiğinin Bekliyor/Devam Ediyor barlarından gelinirse Bakım Akışı'nı
  // "Başlatılanlar" sekmesinde (bu ikisi de gerçekten açılmış iş emirleri —
  // bkz. flowTabItems.started) ve verilen periyot adına göre önceden filtreler.
  initialStatus?: string
  initialPlanName?: string
  // Bir bakım maddesinin yanındaki "Bilgi" düğmesinden Bakım Bilgi
  // Bankası'nda o maddeyi açar (Faz 27).
  onOpenKnowledge?: (nodeId: number) => void
}) {
  const [selected, setSelected] = useState<number | null>(initialSelectedId ?? null)
  // Bir iş emri detayına girildiğinde tarayıcı geçmişine bir adım eklenir ki
  // geri tuşuna basınca (ya da detay içindeki "Bakım Listesi" düğmesine
  // basınca — o da history.back() çağırır, bkz. aşağıda) düzgünce listeye
  // dönülsün (bkz. useDetailHistory.ts).
  useDetailPopState<number>('maintenances', setSelected)
  function openDetail(id: number) {
    pushDetail('maintenances', id)
    setSelected(id)
  }
  const [upcoming, setUpcoming] = useState<UpcomingItem[]>([])
  const [upcomingBusy, setUpcomingBusy] = useState<number | null>(null)
  const [upcomingError, setUpcomingError] = useState('')
  // "İleri tarihe zamanla" inline tarih seçici — hangi satırın açık olduğunu
  // (busyKey ile aynı anahtar) ve o an seçili tarihi tutar.
  const [scheduleOpenKey, setScheduleOpenKey] = useState<number | null>(null)
  const [scheduleDate, setScheduleDate] = useState('')

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

  // Kullanıcı isteği: "vardiya sorumlusu her tikte isim seçmek yerine
  // vardiyasındaki kendini ve diğer kişileri yukarıda vardiya kısmında
  // görüyor ya oradan bir isme tıklasın o an tıkladığı isim için tikliyor
  // olsun. Başka ismi seçene kadar tekrar isim sormasın. Birden fazla kişi
  // seçebilme de olsun." — aşağıdaki vardiya barında (shiftBar) isme
  // tıklandıkça bu listeye eklenip çıkarılır; MaintenanceDetail'e prop
  // olarak geçilir ve her tiklemede kullanılır. Sıradan bir vardiya üyesi
  // (sorumlu/yönetici değilse) isim tıklama arayüzünü hiç görmez — o zaman
  // bu liste her zaman boş kalır ve tikler doğrudan kendi adına gider.
  const [activeWorkerIds, setActiveWorkerIds] = useState<number[]>([])
  function toggleActiveWorker(userId: number) {
    setActiveWorkerIds((prev) =>
      prev.includes(userId) ? prev.filter((x) => x !== userId) : [...prev, userId],
    )
  }

  // Kullanıcı isteği: vardiya isimleri değiştirilebilsin, yeni vardiya
  // açılıp kapatılabilsin (bkz. POST/PATCH/DELETE /shifts — manage_users
  // yetkisi gerektirir, üye atamasıyla aynı yetki).
  const [editingShiftId, setEditingShiftId] = useState<number | null>(null)
  const [shiftNameDraft, setShiftNameDraft] = useState('')
  const [addShiftOpen, setAddShiftOpen] = useState(false)
  const [newShiftName, setNewShiftName] = useState('')
  const [shiftCrudBusy, setShiftCrudBusy] = useState(false)
  const [shiftCrudError, setShiftCrudError] = useState('')

  // Vardiya üyelerini (kimin hangi vardiyada olduğunu) buradan yönetebilmek
  // için tüm atanabilir kullanıcıların listesi — "Kullanıcılar" sayfasına
  // gitmeye gerek kalmadan, seçili vardiyaya doğrudan buradan üye
  // eklenip/çıkarılabilir.
  const [allUsers, setAllUsers] = useState<User[]>([])
  const [memberBusy, setMemberBusy] = useState(false)
  const [supervisorBusy, setSupervisorBusy] = useState(false)
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
    // Vardiya değişince önceki "şu an kimin adına tikliyorum" seçimi
    // anlamsız kalır.
    setActiveWorkerIds([])
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

  const activeShift = shifts.find((s) => s.id === activeShiftId) ?? null
  const activeShiftName = activeShift?.name ?? null

  // Vardiya sorumlusunu değiştirir (yalnızca mühendis/amir/yönetici görür).
  // null gönderilirse sorumluluk kaldırılır ve o vardiyada kimse başkası
  // adına tikleyemez.
  async function setSupervisor(userId: number | null) {
    if (activeShiftId == null) return
    setSupervisorBusy(true)
    setShiftCrudError('')
    try {
      const updated = await api<Shift>(`/shifts/${activeShiftId}`, {
        method: 'PATCH',
        body: { supervisor_id: userId },
      })
      setShifts((prev) => prev.map((s) => (s.id === updated.id ? updated : s)))
      showToast('✓ Vardiya sorumlusu güncellendi')
    } catch (e) {
      setShiftCrudError((e as Error).message)
    } finally {
      setSupervisorBusy(false)
    }
  }

  function startEditShift(s: Shift) {
    setEditingShiftId(s.id)
    setShiftNameDraft(s.name)
    setShiftCrudError('')
  }

  async function saveShiftName(id: number) {
    const name = shiftNameDraft.trim()
    if (!name) return
    setShiftCrudBusy(true)
    setShiftCrudError('')
    try {
      const updated = await api<Shift>(`/shifts/${id}`, { method: 'PATCH', body: { name } })
      setShifts((prev) => prev.map((s) => (s.id === id ? updated : s)))
      setEditingShiftId(null)
    } catch (e) {
      setShiftCrudError((e as Error).message)
    } finally {
      setShiftCrudBusy(false)
    }
  }

  async function addShift() {
    const name = newShiftName.trim()
    if (!name) return
    setShiftCrudBusy(true)
    setShiftCrudError('')
    try {
      const created = await api<Shift>('/shifts', { method: 'POST', body: { name } })
      setShifts((prev) => [...prev, created])
      setNewShiftName('')
      setAddShiftOpen(false)
    } catch (e) {
      setShiftCrudError((e as Error).message)
    } finally {
      setShiftCrudBusy(false)
    }
  }

  async function removeShift(s: Shift) {
    if (shifts.length <= 1) return
    if (!confirm(`"${s.name}" vardiyasını silmek istiyor musunuz? Bu vardiyaya atanmış kullanıcıların vardiya ataması kaldırılır (kendileri silinmez).`)) return
    setShiftCrudBusy(true)
    setShiftCrudError('')
    try {
      await api(`/shifts/${s.id}`, { method: 'DELETE' })
      setShifts((prev) => prev.filter((x) => x.id !== s.id))
      if (activeShiftId === s.id) {
        setActiveShiftId(null)
        localStorage.removeItem(ACTIVE_SHIFT_STORAGE_KEY)
      }
      loadAllUsers()
    } catch (e) {
      setShiftCrudError((e as Error).message)
    } finally {
      setShiftCrudBusy(false)
    }
  }

  // Vardiya sorumlusu (ve yönetici/mühendis/amir), aşağıdaki üye rozetlerine
  // TIKLAYARAK "şu an kimin adına tikliyorum" seçimini yapar — sıradan bir
  // üye bu tıklama arayüzünü hiç görmez, her zaman kendi adına tikler.
  const canPickOthersInBar = isStaff || activeShift?.supervisor?.id === user.id

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
            {shifts.map((s) =>
              editingShiftId === s.id ? (
                <span key={s.id} className="row" style={{ gap: 4, alignItems: 'center' }}>
                  <input
                    autoFocus
                    value={shiftNameDraft}
                    onChange={(e) => setShiftNameDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') saveShiftName(s.id)
                      if (e.key === 'Escape') setEditingShiftId(null)
                    }}
                    style={{ fontSize: 12.5, padding: '5px 8px', width: 120 }}
                  />
                  <button className="small primary" disabled={shiftCrudBusy || !shiftNameDraft.trim()} onClick={() => saveShiftName(s.id)} title="Kaydet">
                    <Check size={13} />
                  </button>
                  <button className="small ghost" disabled={shiftCrudBusy} onClick={() => setEditingShiftId(null)} title="Vazgeç">
                    <X size={13} />
                  </button>
                </span>
              ) : (
                <span key={s.id} className="row" style={{ gap: 2, alignItems: 'center' }}>
                  <button
                    className={`small ${activeShiftId === s.id ? 'primary' : ''}`}
                    onClick={() => selectShift(s.id)}
                  >
                    {s.name}
                  </button>
                  {isStaff && (
                    <>
                      <button className="ghost" disabled={shiftCrudBusy} title="Vardiya adını değiştir" onClick={() => startEditShift(s)} style={{ padding: 4, minWidth: 0 }}>
                        <Pencil size={12} />
                      </button>
                      {shifts.length > 1 && (
                        <button className="ghost" disabled={shiftCrudBusy} title="Vardiyayı sil" onClick={() => removeShift(s)} style={{ padding: 4, minWidth: 0 }}>
                          <Trash2 size={12} />
                        </button>
                      )}
                    </>
                  )}
                </span>
              ),
            )}
            {isStaff && (
              addShiftOpen ? (
                <span className="row" style={{ gap: 4, alignItems: 'center' }}>
                  <input
                    autoFocus
                    value={newShiftName}
                    onChange={(e) => setNewShiftName(e.target.value)}
                    placeholder="Vardiya adı"
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') addShift()
                      if (e.key === 'Escape') { setAddShiftOpen(false); setNewShiftName('') }
                    }}
                    style={{ fontSize: 12.5, padding: '5px 8px', width: 120 }}
                  />
                  <button className="small primary" disabled={shiftCrudBusy || !newShiftName.trim()} onClick={addShift} title="Ekle">
                    <Check size={13} />
                  </button>
                  <button className="small ghost" disabled={shiftCrudBusy} onClick={() => { setAddShiftOpen(false); setNewShiftName('') }} title="Vazgeç">
                    <X size={13} />
                  </button>
                </span>
              ) : (
                <button className="small ghost" onClick={() => setAddShiftOpen(true)}>
                  <Plus size={12} style={{ marginRight: 4, verticalAlign: -2 }} />
                  Vardiya Ekle
                </button>
              )
            )}
          </>
        )}
      </div>
      {shiftCrudError && <div className="error" style={{ marginTop: 8 }}>{shiftCrudError}</div>}
      <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
        {shiftMode?.enabled
          ? (canPickOthersInBar
              ? 'Sıradan vardiya üyeleri madde tikleyince otomatik kendi adlarına kaydedilir. Siz vardiya sorumlusu/yönetici olduğunuz için aşağıdaki isimlerden birine (veya birkaçına) tıklayarak o kişi(ler) adına tiklemeye geçebilirsiniz — başka bir isme tıklayana kadar seçim aynı kalır.'
              : 'Madde tiklediğinizde otomatik olarak kendi adınıza kaydedilir — isim seçme penceresi çıkmaz.')
          : 'Kapalıyken bir madde tikleyen/not ekleyen kişi otomatik olarak o an giriş yapmış hesap olur — isim seçme penceresi çıkmaz.'}
      </div>
      {shiftMode?.enabled && activeShiftId != null && (
        <div style={{ marginTop: 12, paddingTop: 12, borderTop: '1px solid var(--border, rgba(0,0,0,0.08))' }}>
          <div className="muted" style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 8 }}>
            {activeShiftName} Üyeleri:
          </div>
          <div className="row" style={{ gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
            {allUsers.filter((u) => u.shift_id === activeShiftId).map((u) => {
              const isActiveWorker = canPickOthersInBar && activeWorkerIds.includes(u.id)
              return (
                <span
                  key={u.id}
                  className="badge"
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12.5, padding: '4px 8px',
                    ...(isActiveWorker
                      ? { background: 'rgba(37, 99, 235, 0.15)', border: '1px solid rgba(37, 99, 235, 0.5)' }
                      : {}),
                  }}
                >
                  {canPickOthersInBar ? (
                    <button
                      className="ghost"
                      title={
                        isActiveWorker
                          ? 'Bu kişi adına tiklemeyi bırak'
                          : 'Bundan sonraki tikleri bu kişi adına yap (başka bir isme tıklayana kadar)'
                      }
                      onClick={() => toggleActiveWorker(u.id)}
                      style={{
                        display: 'inline-flex', alignItems: 'center', gap: 5, padding: 0, minWidth: 0,
                        border: 'none', background: 'transparent', font: 'inherit', color: 'inherit', cursor: 'pointer',
                      }}
                    >
                      {isActiveWorker && <CheckCircle2 size={13} />}
                      <Avatar name={u.full_name} size={16} />
                      {u.full_name}
                    </button>
                  ) : (
                    <>
                      <Avatar name={u.full_name} size={16} />
                      {u.full_name}
                    </>
                  )}
                  {activeShift?.supervisor?.id === u.id && (
                    <span
                      title="Vardiya sorumlusu — yalnızca bu kişi başkaları adına madde işaretleyebilir"
                      style={{ display: 'inline-flex', alignItems: 'center', gap: 3, fontWeight: 600 }}
                    >
                      <ShieldAlert size={12} /> Sorumlu
                    </span>
                  )}
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
              )
            })}
            {allUsers.filter((u) => u.shift_id === activeShiftId).length === 0 && (
              <span className="muted" style={{ fontSize: 12.5 }}>Bu vardiyaya henüz kimse atanmamış.</span>
            )}
          </div>
          {canPickOthersInBar && (
            <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
              {activeWorkerIds.length > 0
                ? `Şu an ${allUsers
                    .filter((u) => activeWorkerIds.includes(u.id))
                    .map((u) => u.full_name)
                    .join(', ')} adına tikliyorsunuz.`
                : 'Kimse seçilmedi — tikler kendi adınıza kaydedilir. Başkası adına tiklemek için yukarıdan bir isme tıklayın.'}
            </div>
          )}
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
          {/* Vardiya sorumlusu — mühendis/amir seçer. Yalnızca bu kişi bir
              maddeyi başkaları adına işaretleyebilir; vardiyanın diğer
              üyeleri sadece kendi adlarına tikleyebilir. */}
          {isStaff && (
            <div className="row" style={{ gap: 8, marginTop: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <span className="muted" style={{ fontSize: 12.5, fontWeight: 600 }}>
                Vardiya sorumlusu:
              </span>
              <select
                value={activeShift?.supervisor?.id ?? ''}
                disabled={supervisorBusy}
                onChange={(e) => setSupervisor(e.target.value ? Number(e.target.value) : null)}
                style={{ fontSize: 12.5, padding: '5px 8px' }}
              >
                <option value="">Seçilmedi</option>
                {allUsers.filter((u) => u.shift_id === activeShiftId).map((u) => (
                  <option key={u.id} value={u.id}>{u.full_name}</option>
                ))}
              </select>
              <span className="muted" style={{ fontSize: 12 }}>
                Yalnızca sorumlu, başkaları adına madde işaretleyebilir.
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  )

  // Faz 26 — Bakım başlatma onayı: otomatik açılan ama admin/mühendis henüz
  // "Başlat" demediği için Bakımlar listesinde/Takvim'de görünmeyen aday iş
  // emirleri. Yalnızca start_maintenance yetkisi olanlara (admin/mühendis/
  // amir) yüklenir — teknisyen bu uç noktaya zaten erişemez (403).
  // Faz 26 UI — iki tablo alt alta karışık durmasın diye tek seferde
  // yalnızca biri gösterilir: "Başlatılanlar" (varsayılan) ya da
  // "Bekleyenler" (başlatma yetkisi olanlara, büyük sekme düğmeleriyle seçilir).
  // Bakım Akışı üst seçimi: dört kategori büyük sekme düğmeleriyle seçilir;
  // aşağıda yalnızca seçilen kategorinin tablosu gösterilir.
  // Bakım Akışı iki sekme: "Başlatılanlar" (iş emri açılmış — Planlandı +
  // Bakım Gecikti) ve "Başlatılmayı Bekleyenler" (henüz iş emri yok —
  // Planlanmayı Bekleyen + Yaklaşıyor). Her satırın kendi durumu DURUM
  // sütununda gösterilir.
  const [flowTab, setFlowTab] = useState<'started' | 'pending'>('started')
  // Kullanıcı isteği (Faz 27): usta ekranında "Başlatılmayı Bekleyenler"
  // hiç görünmesin — usta yalnızca gerçekten başlatılmış işleri görür.
  // (Zaten teknisyen bu adayları açma yetkisine de sahip değil.)
  const isTechnician = user.role === 'technician'
  const showPendingTab = !isTechnician
  // Bakım Akışı filtreleri: periyot (plan adı) ve araç tipi (model) — seçenekler
  // aşağıdaki `upcoming` verisinden türetilir (ayrı bir /plans veya /vehicles
  // çağrısına gerek yok). Dashboard'dan bir periyotla gelinirse (initialPlanName)
  // aşağıdaki efektle önceden doldurulur.
  const [flowFilterPlan, setFlowFilterPlan] = useState(initialPlanName ?? '')
  const [flowFilterModel, setFlowFilterModel] = useState('')
  // "Seçilen vardiyaya iş emri atama": Bakım Akışı tablosunda hem vardiyaya
  // göre filtreleme (kullanıcı bir vardiyanın iş listesini görmek isteyebilir)
  // hem de her satırdan hızlıca vardiya atama/değiştirme yapılabilir.
  const [flowFilterShift, setFlowFilterShift] = useState<number | ''>('')
  const [flowShiftAssignBusy, setFlowShiftAssignBusy] = useState<number | null>(null)
  const flowCardRef = useRef<HTMLDivElement>(null)

  // Bir iş emri detayından listeye DÖNÜLDÜĞÜNDE (tarayıcının geri tuşu ya da
  // detay içindeki "Bakım Listesi" düğmesi — ikisi de aynı popstate'i
  // tetikler, bkz. useDetailHistory.ts) sayfanın en başına, Bakım Akışı
  // kartına döner.
  //
  // NEDEN: detaya girmeden önce listenin ortasında/aşağısındaysa, geri
  // dönüldüğünde tarayıcı o eski kaydırma konumunu koruyordu; tablette uzun
  // listede kullanıcı "neredeyim?" durumunda kalıyordu. Artık her dönüşte
  // listenin başından devam edilir.
  //
  // Yalnızca detay -> liste geçişinde çalışır: sayfa ilk açılışında ya da
  // liste içinde gezinirken kaydırmaya dokunmaz.
  const prevSelectedRef = useRef<number | null>(selected)
  useEffect(() => {
    const prev = prevSelectedRef.current
    prevSelectedRef.current = selected
    if (prev === null || selected !== null) return
    // Liste bu render'da yeni monte edildiği için ref bir sonraki karede
    // hazır olur.
    requestAnimationFrame(() => {
      flowCardRef.current?.scrollIntoView({ behavior: 'auto', block: 'start' })
      window.scrollTo({ top: 0, behavior: 'auto' })
    })
  }, [selected])

  const loadUpcoming = useCallback(() => {
    api<UpcomingItem[]>('/vehicles/alerts').then(setUpcoming).catch(() => setUpcoming([]))
  }, [])

  // Faz 27 — Usta bakım takvimi. Listeli görünümün ÜSTÜNDE, başlatılmış ve
  // kendisine atanan iş emirlerini aylık takvimde gösterir. Backend zaten
  // teknisyene yalnızca kendisine atanan + atanmamış AKTİF (is_activated)
  // iş emirlerini döndürür (bkz. _filtered_maintenance_query), bu yüzden
  // burada ek bir yetki filtresi gerekmez.
  //
  // Tamamlananlar da gösterilir (yeşil): yönetici bir bakımı "Tamamlandı"
  // yapınca takvimde GERÇEKTE tamamlandığı güne (completed_at) oturur, geçmiş
  // aylarda da görünür. Geçmişin tamamı çekilmesin diye tamamlananlar son
  // CALENDAR_HISTORY_MONTHS ayla sınırlanır — takvim bundan daha geriye
  // gidilirse zaten Bakım Geçmişi sayfası kullanılır.
  const [calMaintenances, setCalMaintenances] = useState<Maintenance[]>([])
  const loadCalendar = useCallback(() => {
    if (user.role !== 'technician') return
    const since = new Date()
    since.setMonth(since.getMonth() - CALENDAR_HISTORY_MONTHS)
    const from = `${since.getFullYear()}-${String(since.getMonth() + 1).padStart(2, '0')}-01`
    Promise.all([
      // Açık (tamamlanmamış) iş emirleri — planlanan günlerine düşer.
      api<Maintenance[]>('/maintenances').then((rows) => rows.filter((m) => m.status !== 'completed')),
      // Tamamlananlar — tamamlanma tarihine göre süzülür.
      api<Maintenance[]>(
        `/maintenances?status=completed&date_field=completed&date_from=${from}`,
      ).catch(() => [] as Maintenance[]),
    ])
      .then(([open, done]) => setCalMaintenances([...open, ...done]))
      .catch(() => setCalMaintenances([]))
  }, [user.role])

  // Kullanıcı isteği: Bakım Akışı'nda o an seçili filtreyle (periyot/araç
  // tipi/sekme) BİREBİR aynı sonucu Excel'e aktar — her durum kategorisi
  // (Bakım Gecikti/Planlandı/Yaklaşıyor/Planlanmayı Bekleyen) kendi
  // sayfasında ("sheet sheet ayır").
  const [flowExportError, setFlowExportError] = useState('')
  const [flowExportBusy, setFlowExportBusy] = useState(false)
  async function exportFlowExcel() {
    setFlowExportError('')
    setFlowExportBusy(true)
    try {
      const p = new URLSearchParams()
      if (flowFilterPlan) p.set('plan_name', flowFilterPlan)
      if (flowFilterModel) p.set('vehicle_model', flowFilterModel)
      if (flowFilterShift !== '') p.set('shift_id', String(flowFilterShift))
      p.set('started', flowTab === 'started' ? 'true' : 'false')
      await apiDownload(`/vehicles/alerts/export?${p.toString()}`, 'bakim_akisi.xlsx')
    } catch (e) {
      setFlowExportError((e as Error).message)
    } finally {
      setFlowExportBusy(false)
    }
  }

  // Dashboard'dan bir filtreyle (initialStatus/initialPlanName) gelindiyse
  // Bakım Akışı kartına kaydırır ve doğru sekmeyi seçer — "Planlanan
  // Bakım"/"Devam Eden" kartları ile periyot grafiğinin Bekliyor/Devam
  // Ediyor barları, ikisi de gerçekten açılmış (başlatılmış) iş emirlerini
  // saydığı için her ikisi de "Başlatılanlar" sekmesine düşer.
  useEffect(() => {
    if (!initialStatus && !initialPlanName) return
    if (initialStatus === 'pending' || initialStatus === 'in_progress') setFlowTab('started')
    flowCardRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (selected !== null) return
    loadUpcoming()
    loadCalendar()
  }, [loadUpcoming, loadCalendar, selected])

  // Bakımlar sayfası eskiden canlı olayları dinlemediği için başka bir
  // ekranda yapılan planlama/tamamlama/KM güncellemesi burada eski kalıyordu.
  useLiveEvent(['maintenance', 'vehicle', 'plan'], () => {
    if (selected === null) {
      loadUpcoming()
      loadCalendar()
    }
  })

  // Kullanıcı isteği: "Başlatılmayı Bekleyenler" tablosundaki "Planlamaya Al"
  // artık iki adımlı değil — TEK tıkla hem aday iş emrini açar hem de
  // doğrudan başlatır, ardından kullanıcıyı otomatik olarak "Başlatılanlar"
  // sekmesine geçirir ki sonucu hemen orada görsün.
  // scheduledDate verilirse (YYYY-MM-DD, bugünden ileri): iş emri HEMEN
  // başlamaz, yalnızca o tarihe zamanlanır ve "Başlatılmayı Bekleyenler"
  // sekmesinde "Zamanlandı: X" rozetiyle kalır — arka plan zamanlayıcısı o
  // tarih geldiğinde otomatik başlatır (bkz. services.schedule_maintenance_
  // start/run_scheduled_maintenance_starts). Verilmezse eskisi gibi TEK
  // tıkla hem aday iş emrini açar hem de anında başlatır.
  async function planUpcoming(item: UpcomingItem, scheduledDate?: string) {
    setUpcomingBusy(item.maintenance_id ?? -item.target_km)
    setUpcomingError('')
    try {
      const maintenanceId =
        item.maintenance_id ??
        (await api<Maintenance>('/maintenances/plan-upcoming', {
          method: 'POST', body: { vehicle_id: item.vehicle.id, target_km: item.target_km },
        })).id
      await api(`/maintenances/${maintenanceId}/start`, {
        method: 'POST',
        body: scheduledDate ? { scheduled_start_at: scheduledDate } : {},
      })
      loadUpcoming()
      if (scheduledDate) {
        setScheduleOpenKey(null)
        showToast(`✓ ${item.vehicle.code} bakımı ${new Date(scheduledDate).toLocaleDateString('tr-TR')} tarihine zamanlandı`, 'info')
      } else {
        setFlowTab('started')
        showToast(`✓ ${item.vehicle.code} bakımı başlatıldı`, 'info')
      }
    } catch (e) {
      setUpcomingError((e as Error).message)
    } finally {
      setUpcomingBusy(null)
    }
  }

  // Zaten zamanlanmış bir adayın zamanlamasını iptal eder — iş emri
  // "Başlatılmayı Bekleyenler"de zamansız aday olarak kalmaya devam eder.
  // maintenance_id bu satırda null olabileceğinden (bkz. UpcomingItem.
  // scheduled_start_at açıklaması) önce plan-upcoming'e idempotent şekilde
  // tekrar gidilip gerçek id bulunur.
  async function cancelSchedule(item: UpcomingItem) {
    setUpcomingBusy(item.maintenance_id ?? -item.target_km)
    setUpcomingError('')
    try {
      const maintenanceId =
        item.maintenance_id ??
        (await api<Maintenance>('/maintenances/plan-upcoming', {
          method: 'POST', body: { vehicle_id: item.vehicle.id, target_km: item.target_km },
        })).id
      await api(`/maintenances/${maintenanceId}/cancel-schedule`, { method: 'POST' })
      loadUpcoming()
      showToast('✓ Zamanlama iptal edildi')
    } catch (e) {
      setUpcomingError((e as Error).message)
    } finally {
      setUpcomingBusy(null)
    }
  }

  // Kullanıcı isteği: "seçilen vardiyaya iş emri atama olsun" — Bakım Akışı
  // tablosundaki bir satırdan, iş emrini bir vardiyaya atar/vardiyadan
  // çıkarır. "Atanan" (kişi, assignTo) alanından bağımsızdır.
  async function assignFlowShift(maintenanceId: number, shiftId: number | null) {
    setFlowShiftAssignBusy(maintenanceId)
    setUpcomingError('')
    try {
      await api(`/maintenances/${maintenanceId}/assign-shift`, {
        method: 'PATCH', body: { assigned_shift_id: shiftId },
      })
      loadUpcoming()
      showToast(shiftId ? '✓ İş emri vardiyaya atandı' : '✓ Vardiya ataması kaldırıldı')
    } catch (e) {
      setUpcomingError((e as Error).message)
    } finally {
      setFlowShiftAssignBusy(null)
    }
  }

  async function completeUpcoming(item: UpcomingItem) {
    if (!item.maintenance_id) return
    if (!confirm(`${item.vehicle.code} — ${item.plan_name} bakımını doğrudan TAMAMLANDI yapmak istiyor musunuz?`)) return
    setUpcomingBusy(item.maintenance_id)
    setUpcomingError('')
    try {
      if (!(await askCompletedKm(item.maintenance_id, item.target_km > 0))) return
      await api(`/maintenances/${item.maintenance_id}/force-complete`, { method: 'POST' })
      loadUpcoming()
      showToast('✓ İş emri tamamlandı yapıldı')
    } catch (e) {
      setUpcomingError((e as Error).message)
    } finally {
      setUpcomingBusy(null)
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
      loadUpcoming()
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
      loadUpcoming()
    } catch (e) {
      setDangerError((e as Error).message)
    } finally {
      setDangerBusy(false)
    }
  }

  // Bakım Akışı kategori grupları + otomatik kategori seçimi.
  // ÖNEMLİ: Bu hook'lar (useMemo/useEffect) aşağıdaki erken return'lerden
  // ÖNCE çağrılmalı. Aksi halde bir bakım detayı açıldığında (selected !== null)
  // hook sırası değişir ve React "rendered fewer hooks than expected" hatasıyla
  // sayfayı çökertir.
  // Sekme ayrımı DURUM etiketine (yaklasiyor/planlanacak/planlandi/gecikti)
  // göre DEĞİL, doğrudan gerçekten başlatılmış olup olmadığına (maintenance_id
  // dolu mu) göre yapılır — kullanıcı onayı: pencere dışında ama Başlat
  // denmiş bir iş emri "Yaklaşıyor" etiketiyle görünse bile yine de
  // Başlatılanlar'da kalmalı; DURUM sütunundaki metin bundan bağımsız,
  // yalnızca km/tarih yakınlığını gösterir (bkz. backend
  // services._classify_upcoming_status docstring'i).
  // Sekme içinde en acil olan üstte görünsün diye DURUM etiketine göre
  // sıralanır (gecikti en acil, sonra planlandı/yaklaşıyor/planlanacak).
  const FLOW_STATUS_PRIORITY: Record<string, number> = { gecikti: 0, planlandi: 1, yaklasiyor: 2, planlanacak: 3 }
  const flowSort = (a: UpcomingItem, b: UpcomingItem) =>
    (FLOW_STATUS_PRIORITY[a.status ?? ''] ?? 4) - (FLOW_STATUS_PRIORITY[b.status ?? ''] ?? 4)
  const flowStartedItems = useMemo(
    () => upcoming.filter((x) => x.maintenance_id != null).sort(flowSort),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [upcoming],
  )
  const flowPendingItems = useMemo(
    () => upcoming.filter((x) => x.maintenance_id == null).sort(flowSort),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [upcoming],
  )

  // Veri yenilendiğinde seçili sekme boşsa diğer (dolu) sekmeye otomatik geç.
  // Ustada "Bekleyenler" sekmesi hiç olmadığı için bu otomatik geçiş de
  // devre dışıdır (aksi halde görünmeyen bir sekme seçili kalırdı).
  useEffect(() => {
    if (!showPendingTab || upcoming.length === 0) return
    const startedCount = flowStartedItems.length
    const pendingCount = flowPendingItems.length
    if (flowTab === 'started' && startedCount === 0 && pendingCount > 0) setFlowTab('pending')
    else if (flowTab === 'pending' && pendingCount === 0 && startedCount > 0) setFlowTab('started')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [upcoming])

  if (selected !== null)
    return (
      <>
        {shiftBar}
        <MaintenanceDetail
          id={selected}
          user={user}
          perms={perms}
          onBack={() => window.history.back()}
          onDeleted={() => {
            window.history.back()
            loadUpcoming()
          }}
          shiftModeEnabled={!!shiftMode?.enabled}
          activeShiftId={activeShiftId}
          shiftMembersVersion={shiftMembersVersion}
          shifts={shifts}
          activeWorkerIds={activeWorkerIds}
          onOpenKnowledge={onOpenKnowledge}
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

  // Bakım Akışı kategorilerinin etiket / renk tonu / ikon eşlemeleri.
  const flowLabel = { yaklasiyor: 'Yaklaşıyor', planlanacak: 'Planlanmayı Bekleyen', planlandi: 'Planlandı', gecikti: 'Bakım Gecikti' } as const
  // Durum -> Badge tonu (DURUM sütunundaki renkli rozet için).
  const flowBadgeTone = { gecikti: 'danger', planlandi: 'in_progress', planlanacak: 'pending', yaklasiyor: 'neutral' } as const
  // İki sekmenin içerikleri artık DURUM etiketine göre değil, gerçekten
  // başlatılmış olup olmadığına göre ayrılır (bkz. flowStartedItems/
  // flowPendingItems yukarıda) — bir iş emri pencere dışında "Yaklaşıyor"
  // etiketiyle görünse bile başlatılmışsa Başlatılanlar'da kalır.
  const flowTabItems = {
    started: flowStartedItems,
    pending: flowPendingItems,
  }

  // Bakım Akışı filtre seçenekleri — mevcut `upcoming` verisinde GERÇEKTEN
  // bulunan periyot/araç tipi değerlerinden türetilir (boş/uygunsuz seçenek
  // sunulmaz).
  const flowPlanOptions = Array.from(new Set(upcoming.map((u) => u.plan_name))).sort((a, b) => a.localeCompare(b, 'tr'))
  const flowModelOptions = Array.from(new Set(upcoming.map((u) => u.vehicle.model).filter((m): m is string => !!m))).sort((a, b) => a.localeCompare(b, 'tr'))
  const visibleFlowItems = flowTabItems[flowTab].filter(
    (item) =>
      (!flowFilterPlan || item.plan_name === flowFilterPlan) &&
      (!flowFilterModel || item.vehicle.model === flowFilterModel) &&
      (flowFilterShift === '' || item.assigned_shift_id === flowFilterShift),
  )
  const flowHasFilter = !!flowFilterPlan || !!flowFilterModel || flowFilterShift !== ''
  const canAssignShift = user.role === 'admin' || user.role === 'engineer' || user.role === 'amir'

  return (
    <>
      <PageHeader
        title="Bakımlar"
        subtitle={
          isTechnician
            ? 'Takvimde size atanan ve başlatılan bakımları görün, detaylara geçin.'
            : 'Başlatılan ve başlatılmayı bekleyen bakımları izleyin, detaylara geçin.'
        }
        actions={
          !!perms.create_maintenance && (
            <button className="primary" onClick={openManualAdd}>
              <Plus size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
              Manuel Bakım Ekle
            </button>
          )
        }
      />
      {isTechnician && (
        <MaintenanceCalendar
          maintenances={calMaintenances}
          user={user}
          onOpenMaintenance={openDetail}
        />
      )}
      <div className="card maintenance-flow-card fade-in" ref={flowCardRef}>
        <div className="card-head">
          <div><div className="card-title">Bakım Akışı</div><div className="card-subtitle">Araçlar ekranındaki uyarılar burada da planlanabilir ve yönetilebilir.</div></div>
          <div className="row" style={{ gap: 8 }}>
            {!!perms.export_excel && (
              <button className="small ghost" disabled={flowExportBusy} onClick={exportFlowExcel} title="Seçili filtreye göre Excel'e aktar (durum kategorisine göre sayfa sayfa)">
                <Download size={13} style={{ marginRight: 5, verticalAlign: -2 }} />
                {flowExportBusy ? 'İndiriliyor…' : "Excel'e Aktar"}
              </button>
            )}
            <button className="small ghost" onClick={loadUpcoming}><RefreshCw size={13} style={{ marginRight: 5, verticalAlign: -2 }} /> Yenile</button>
          </div>
        </div>
        {flowExportError && <div className="error">{flowExportError}</div>}
        {upcomingError && <div className="error">{upcomingError}</div>}
        <div className="row maintenance-view-tabs maintenance-flow-tabs" style={{ gap: 10, marginBottom: 14, flexWrap: 'wrap' }}>
          {(showPendingTab
            ? ([['started', 'Başlatılanlar', Wrench], ['pending', 'Başlatılmayı Bekleyenler', ListFilter]] as const)
            : ([['started', 'Başlatılanlar', Wrench]] as const)
          ).map(([key, label, Icon]) => {
            const active = flowTab === key
            const count = flowTabItems[key].length
            return (
              <button
                key={key}
                className={active ? 'primary' : ''}
                style={{ padding: '11px 20px', fontSize: 14, fontWeight: 700 }}
                onClick={() => setFlowTab(key)}
              >
                <Icon size={16} style={{ marginRight: 8, verticalAlign: -3 }} />
                {label}
                <span
                  style={{
                    marginLeft: 8,
                    background: active ? 'rgba(255,255,255,0.28)' : 'var(--amber-light)',
                    color: active ? '#fff' : 'var(--amber)',
                    borderRadius: 999, padding: '1px 9px', fontSize: 12.5, fontWeight: 700,
                  }}
                >
                  {count}
                </span>
              </button>
            )
          })}
        </div>
        {upcoming.length > 0 && (
          <div className="table-toolbar" style={{ flexWrap: 'wrap', marginBottom: 12 }}>
            <ListFilter size={14} color="var(--text-tertiary)" />
            <select value={flowFilterPlan} onChange={(e) => setFlowFilterPlan(e.target.value)}>
              <option value="">Tüm Periyotlar</option>
              {flowPlanOptions.map((name) => (
                <option key={name} value={name}>{name}</option>
              ))}
            </select>
            <select value={flowFilterModel} onChange={(e) => setFlowFilterModel(e.target.value)}>
              <option value="">Tüm Araç Tipleri</option>
              {flowModelOptions.map((model) => (
                <option key={model} value={model}>{model}</option>
              ))}
            </select>
            {shifts.length > 0 && (
              <select
                value={flowFilterShift}
                onChange={(e) => setFlowFilterShift(e.target.value ? Number(e.target.value) : '')}
              >
                <option value="">Tüm Vardiyalar</option>
                {shifts.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
            )}
            {flowHasFilter && (
              <button className="small ghost" onClick={() => { setFlowFilterPlan(''); setFlowFilterModel(''); setFlowFilterShift('') }}>
                Filtreleri Temizle
              </button>
            )}
          </div>
        )}
        {upcoming.length === 0
          ? <div className="muted" style={{ fontSize: 13 }}>Şu an bakım takip penceresinde bir araç yok.</div>
          : visibleFlowItems.length === 0
            ? (
              <div className="muted" style={{ fontSize: 13 }}>
                {flowHasFilter
                  ? 'Seçili filtrelere uyan bir kayıt yok.'
                  : flowTab === 'started' ? 'Açık iş emri olan (başlatılan) bakım yok.' : 'Başlatılmayı bekleyen bakım yok.'}
              </div>
            )
            : <div className="maintenance-flow-groups">
                <section className="maintenance-flow-group">
                  <div className="table-scroll"><table><thead><tr><th>Araç</th><th>Periyot</th><th>Hedef KM</th><th>Durum</th><th>Kalan Durum</th><th>İş Emri</th><th>Vardiya</th><th>Planlanan Başlangıç</th><th>İşlem</th></tr></thead><tbody>{visibleFlowItems.map((item) => {
                    const busyKey = item.maintenance_id ?? -item.target_km
                    const st = item.status ?? undefined
                    const durLabel = st ? flowLabel[st] : '—'
                    const durTone = st ? flowBadgeTone[st] : 'neutral'
                    const rowClickable = !!item.maintenance_id || !!onOpenVehicle
                    const rowTitle = item.maintenance_id
                      ? `İş emri #${item.maintenance_id} — bakım detayını aç`
                      : onOpenVehicle ? `${item.vehicle.code} araç kartını aç` : undefined
                    return <tr key={`${item.vehicle.id}-${item.target_km}`} className={rowClickable ? 'clickable-row' : ''} onClick={() => { if (item.maintenance_id) openDetail(item.maintenance_id); else onOpenVehicle?.(item.vehicle.id) }} title={rowTitle}><td><span className="vehicle-code">{item.vehicle.code}</span></td><td>{topPeriodLabel(item.covered_tiers, 'km', item.vehicle.model, item.plan_name)}</td><td>{rawTargetLabel(item.target_km, item.target_date)}</td><td><Badge tone={durTone}>{durLabel}</Badge></td><td>{upcomingRemainingLabel(item)}</td><td>{item.maintenance_id ? <button className="small ghost" onClick={(e) => { e.stopPropagation(); openDetail(item.maintenance_id!) }}>#{item.maintenance_id} Aç</button> : <span className="muted">Henüz yok</span>}</td><td onClick={(e) => e.stopPropagation()}>
                      {!item.maintenance_id ? (
                        <span className="muted">—</span>
                      ) : canAssignShift ? (
                        <select
                          value={item.assigned_shift_id ?? ''}
                          disabled={flowShiftAssignBusy === item.maintenance_id}
                          onChange={(e) => assignFlowShift(item.maintenance_id!, e.target.value ? Number(e.target.value) : null)}
                          style={{ fontSize: 12.5, padding: '4px 6px' }}
                        >
                          <option value="">Atanmamış</option>
                          {shifts.map((s) => (
                            <option key={s.id} value={s.id}>{s.name}</option>
                          ))}
                        </select>
                      ) : (
                        <span className="muted">{item.assigned_shift_name || 'Atanmamış'}</span>
                      )}
                    </td><td>
                      {item.scheduled_start_at ? (
                        <Badge tone="in_progress">
                          <CalendarClock size={11} style={{ marginRight: 4, verticalAlign: -2 }} />
                          {new Date(item.scheduled_start_at).toLocaleDateString('tr-TR')}
                        </Badge>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td><td onClick={(e) => e.stopPropagation()}>
                      {!item.maintenance_id && (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
                          {item.scheduled_start_at ? (
                            // Tarih artık ayrı "Planlanan Başlangıç" sütununda gösteriliyor —
                            // burada yalnızca iptal işlemi kalır.
                            <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                              <button className="small ghost" disabled={upcomingBusy === busyKey} onClick={() => cancelSchedule(item)}>
                                <XCircle size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                                İptal Et
                              </button>
                            </div>
                          ) : null}
                          {!!perms.start_maintenance && (
                            <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                              <button className="small primary" disabled={upcomingBusy === busyKey} onClick={() => planUpcoming(item)}>
                                <PlayCircle size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                                {upcomingBusy === busyKey ? 'Başlatılıyor…' : 'Başlat'}
                              </button>
                              <button
                                className="small ghost"
                                title="İleri bir tarihe zamanla"
                                disabled={upcomingBusy === busyKey}
                                onClick={() => {
                                  if (scheduleOpenKey === busyKey) { setScheduleOpenKey(null); return }
                                  setScheduleOpenKey(busyKey)
                                  setScheduleDate('')
                                }}
                              >
                                <CalendarClock size={12} style={{ marginRight: 5, verticalAlign: -2 }} />
                                {item.scheduled_start_at ? 'Tarihi Değiştir' : 'İleri Tarihe Al'}
                              </button>
                            </div>
                          )}
                          {scheduleOpenKey === busyKey && (
                            <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                              <input
                                type="date"
                                value={scheduleDate}
                                min={new Date(Date.now() + 86400000).toISOString().slice(0, 10)}
                                onChange={(e) => setScheduleDate(e.target.value)}
                                style={{ fontSize: 12.5, padding: '4px 6px' }}
                              />
                              <button
                                className="small"
                                disabled={!scheduleDate || upcomingBusy === busyKey}
                                onClick={() => planUpcoming(item, scheduleDate)}
                              >
                                Zamanla
                              </button>
                              <button className="small ghost" onClick={() => setScheduleOpenKey(null)}>Vazgeç</button>
                            </div>
                          )}
                        </div>
                      )}
                      {user.role === 'admin' && !!item.maintenance_id && <button className="small" disabled={upcomingBusy === busyKey} onClick={() => completeUpcoming(item)}>Tamamlandı Yap</button>}
                    </td></tr>
                  })}</tbody></table></div>
                </section>
              </div>}
      </div>

      {shiftBar}

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
