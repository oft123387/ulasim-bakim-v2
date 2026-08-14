import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, ArrowRight, X } from 'lucide-react'
import type { Page } from '../App'

/* =========================================================================
   Site içi eğitim turu (onboarding).
   Amaç: yeni bir kullanıcı ilk giriş yaptığında, önemli alanların üzerinde
   ("spotlight" ile karartılmış arka plan + o alanı çerçeveleyen vurgu)
   kısa açıklamalar gösterip "Devam" ile adım adım ilerletmek — sayfa
   sayfa dolaşarak. Bir kez tamamlanınca/kapatılınca localStorage'a
   (kullanıcı adına özel) yazılır ve bir daha otomatik açılmaz; istenirse
   üstteki "?" butonundan elle tekrar başlatılabilir.
   ========================================================================= */

export interface TourStep {
  page: Page
  selector: string
  title: string
  text: string
}

// Her adım hangi sayfada, hangi CSS seçicisinin üzerinde durulacağını
// tanımlar. Yalnızca o an DOM'da bulunan ve kullanıcının rolüne göre
// görünür olan sayfalar App.tsx'te filtrelenip buraya geçirilir.
export const ONBOARDING_STEPS: TourStep[] = [
  {
    page: 'dashboard',
    selector: '.dashboard-kpis',
    title: 'Genel Durum',
    text: 'Toplam araç, bekleyen/devam eden/tamamlanan bakım sayıları burada özetlenir. Kartlara tıklayarak doğrudan ilgili bakım listesine gidebilirsiniz.',
  },
  {
    page: 'dashboard',
    selector: '.dashboard-analysis',
    title: 'Analiz',
    text: 'Periyoda göre bakım dağılımı ve genel tamamlanma oranı burada. Grafikteki çubuklara tıklarsanız o duruma/periyoda filtrelenmiş bakım listesi açılır.',
  },
  {
    page: 'dashboard',
    selector: '.dashboard-activity',
    title: 'Aktivite',
    text: 'Zamanı gelen/yaklaşan bakımlar ve son tamamlanan işler burada listelenir — bir satıra tıklayarak doğrudan o bakımı açabilirsiniz.',
  },
  {
    page: 'dashboard',
    selector: '.sidebar nav',
    title: 'Menü',
    text: 'Sol menüden Bakımlar, Araçlar, Takvim, Parça Bilgi Bankası gibi tüm bölümlere ulaşabilirsiniz. Daraltmak için üstteki oka basabilirsiniz.',
  },
  {
    page: 'dashboard',
    selector: '.topbar-search',
    title: 'Global Arama',
    text: 'Araç, iş emri, parça, personel, PDF — her şeyi tek kutudan arayabilirsiniz. Kısayol: Ctrl+K.',
  },
  {
    page: 'dashboard',
    selector: '.topbar-bell',
    title: 'Bildirimler',
    text: 'Geciken bakımlar, yeni iş emirleri, tamamlanan bakımlar ve kilit açma gibi olaylar burada birikir.',
  },
  {
    page: 'maintenances',
    selector: '.page-header',
    title: 'Bakımlar',
    text: 'Tüm iş emirleri burada listelenir. Bir bakıma tıklayınca madde listesi açılır; checkbox\'ı işaretlediğinizde sayfa değişmeden anında kaydedilir. Ağaçta hızlı arama için Ctrl+F kullanabilirsiniz.',
  },
  {
    page: 'vehicles',
    selector: '.table-toolbar',
    title: 'Araçlar',
    text: 'Arama ve filtrelerin yanında ⭐ ile sık kullandığınız araçları favoriye ekleyebilir, "Son Açılanlar" listesinden tek tıkla geri dönebilirsiniz. Araç detayında QR kod ve yazdırma seçenekleri de var.',
  },
  {
    page: 'calendar',
    selector: '.page-header',
    title: 'Takvim',
    text: 'Bakımlar haftalık (varsayılan) veya aylık görünümde tarih eksenine yerleştirilir; her bakımın altında "en geç bu tarihte" tahmini de görünür.',
  },
  {
    page: 'parts',
    selector: '.page-header',
    title: 'Parça Bilgi Bankası',
    text: 'Parçaların teknik bilgisi, görselleri, dokümanları ve stok durumu burada tutulur.',
  },
  {
    page: 'suggestions',
    selector: '.page-header',
    title: 'İyileştirme Talepleri',
    text: 'Bakım ağacına eklenmesini istediğiniz bir maddeyi buradan talep edebilirsiniz — mühendis, amir veya admin doğrudan onaylar ya da reddeder. Talepler herkese açık şekilde görünür.',
  },
  {
    page: 'kpi',
    selector: '.page-header',
    title: 'KPI Raporu',
    text: 'Tarih aralığı seçip tamamlanma oranı/süresi, kritik-gecikmiş bakım sayıları ile araç/model ve personel performansını inceleyin; tek tıkla Excel\'e aktarın.',
  },
]

interface Rect {
  top: number
  left: number
  width: number
  height: number
}

// Uygulama genelinde <html> öğesine "zoom: 0.85" uygulanıyor (bkz.
// index.css) — daha fazla içeriği ekrana sığdırmak için. Sorun şu: bu
// zoom, getBoundingClientRect()'in döndürdüğü değerlere ZATEN uygulanmış
// durumda (tarayıcı ölçümü küçültülmüş/zoom'lu koordinatlarda verir), ama
// bu turun position:fixed öğeleri de AYNI zoom'lu <html> içinde
// render edildiği için, bu (zaten küçültülmüş) sayıları doğrudan
// top/left/width/height olarak kullanınca tarayıcı bunları BİR KEZ DAHA
// küçültüyor — vurgu kutusu hedeften daha küçük/kaymış görünmesinin asıl
// sebebi bu (tek seferlik yeniden ölçüm ya da her karede ölçüm bunu
// düzeltmiyordu, çünkü hata zamanla değil matematikle ilgiliydi). Çözüm:
// ölçülen değerleri zoom'a bölerek bu "iki kez küçültme"yi baştan iptal
// etmek — tarayıcı tekrar zoom uygulayınca sonuç gerçek/doğru boyuta döner.
function currentZoom(): number {
  const z = parseFloat(getComputedStyle(document.documentElement).zoom || '1')
  return Number.isFinite(z) && z > 0 ? z : 1
}

function measure(selector: string): Rect | null {
  const el = document.querySelector(selector) as HTMLElement | null
  if (!el) return null
  const r = el.getBoundingClientRect()
  const z = currentZoom()
  return { top: r.top / z, left: r.left / z, width: r.width / z, height: r.height / z }
}

export function OnboardingTour({
  steps,
  currentPage,
  onNavigate,
  onClose,
}: {
  steps: TourStep[]
  currentPage: Page
  onNavigate: (p: Page) => void
  onClose: () => void
}) {
  const [idx, setIdx] = useState(0)
  const [rect, setRect] = useState<Rect | null>(null)
  const attemptsRef = useRef(0)
  const step = steps[idx]

  function goNext() {
    if (idx >= steps.length - 1) {
      onClose()
      return
    }
    setIdx((i) => i + 1)
  }
  function goBack() {
    setIdx((i) => Math.max(0, i - 1))
  }

  // Adım değişince (veya sayfa değişince) hedef elemanı bul; sayfa farklıysa
  // önce oraya geçilir, DOM güncellenince bu efekt tekrar çalışır.
  useEffect(() => {
    attemptsRef.current = 0
    setRect(null)
    if (!step) return
    if (step.page !== currentPage) {
      onNavigate(step.page)
      return
    }
    let cancelled = false
    let timer: number | undefined
    function tryFind() {
      if (cancelled) return
      const r = measure(step.selector)
      if (r) {
        document.querySelector(step.selector)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
        timer = window.setTimeout(() => {
          if (!cancelled) setRect(measure(step.selector))
        }, 240)
        return
      }
      attemptsRef.current++
      // ~2 saniye denenir; hedef bulunamazsa (izin/veri nedeniyle bu adım
      // uygun değilse) sessizce sonraki adıma geçilir.
      if (attemptsRef.current > 40) {
        goNext()
        return
      }
      timer = window.setTimeout(tryFind, 50)
    }
    tryFind()
    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idx, currentPage])

  // Vurgu kutusunun hedeften "kayması" (yanlış konumda görünmesi) daha
  // önce yalnızca resize/scroll olaylarında yeniden ölçülmesinden
  // kaynaklanıyordu — ama gerçek düzen kayması genelde BAŞKA sebeplerden
  // olur: Inter yazı tipi geç yüklenip metin genişliği değişir, dashboard
  // kartları veri gelince boy değiştirir, kenar çubuğu daralma/genişleme
  // animasyonu sürerken ölçüm alınır vb. — bunların hiçbiri resize/scroll
  // tetiklemez. Bunun yerine adım aktif olduğu sürece her animasyon
  // karesinde yeniden ölçülüp yalnızca değer gerçekten değiştiyse state
  // güncellenir; böylece kutu her zaman hedefin GERÇEK/güncel konumunu
  // takip eder, sebebi ne olursa olsun.
  useEffect(() => {
    if (!step) return
    let raf: number
    let lastKey = ''
    function loop() {
      const r = measure(step.selector)
      const key = r ? `${r.top}|${r.left}|${r.width}|${r.height}` : ''
      if (key !== lastKey) {
        lastKey = key
        setRect(r)
      }
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [step])

  if (!step) return null

  const PAD = 6
  const hasRect = !!rect
  // window.innerWidth/innerHeight zoom'lu görünür alanı verir (tarayıcının
  // kendisinden gelir, ikinci kez küçültülmez); ama bu turun öğeleri AYNI
  // zoom'lu <html> içinde render edildiği için, measure()'daki aynı sebeple
  // bu ikisi de zoom'a bölünmeli — aksi halde ekran kenarına "sığdırma"
  // hesapları (tooltip/maske konumu) rect ile tutarsız kalır.
  const z = currentZoom()
  const viewportW = window.innerWidth / z
  const viewportH = window.innerHeight / z

  // Ham rect, sayfa kaydırıldığında (özellikle ekrandan uzun bölümlerde,
  // ör. Dashboard'daki aktivite kartları) görünür alanın tamamen dışına
  // taşabiliyordu — bu durumda vurgu kutusu ve tooltip ekran dışına
  // yerleşip "görünmüyor / tıklanamıyor" haline geliyordu. Bunu önlemek
  // için önce rect'i görünür pencereyle kesiştiriyoruz (viewport clamp);
  // kesişim çok küçük/negatifse (eleman neredeyse tamamen ekran dışında),
  // en azından ekranın görünür bir yerinde küçük bir işaretleyici kutu
  // gösteriyoruz — hiçbir zaman tamamen kaybolmuyor.
  const raw = rect ?? { top: 0, left: 0, width: 0, height: 0 }
  let r = raw
  if (hasRect) {
    const visTop = Math.min(Math.max(raw.top, 0), viewportH)
    const visBottom = Math.min(Math.max(raw.top + raw.height, 0), viewportH)
    const visLeft = Math.min(Math.max(raw.left, 0), viewportW)
    const visRight = Math.min(Math.max(raw.left + raw.width, 0), viewportW)
    let height = visBottom - visTop
    let top = visTop
    if (height < 24) {
      height = 24
      top = Math.min(Math.max(raw.top, 16), viewportH - 40)
    }
    let width = visRight - visLeft
    let left = visLeft
    if (width < 24) {
      width = Math.min(200, viewportW - 32)
      left = Math.min(Math.max(raw.left, 16), viewportW - width - 16)
    }
    r = { top, left, width, height }
  }

  const tooltipW = Math.min(340, viewportW - 32)
  const tooltipMaxH = 220
  const spaceBelow = viewportH - (r.top + r.height)
  const placeAbove = hasRect && spaceBelow < tooltipMaxH && r.top > tooltipMaxH
  let tooltipTop = hasRect
    ? placeAbove
      ? r.top - PAD - 10
      : r.top + r.height + PAD + 10
    : viewportH / 2 - 90
  // Tooltip'in kendisi de (yüksekliği ne olursa olsun) her zaman ekranın
  // görünür alanında kalsın diye son bir kez sıkıştırılır — yönüne göre
  // (yukarı açılıyorsa üstten, aşağı açılıyorsa alttan) pay bırakılır.
  tooltipTop = placeAbove
    ? Math.min(Math.max(tooltipTop, tooltipMaxH + 16), viewportH - 16)
    : Math.min(Math.max(tooltipTop, 16), Math.max(16, viewportH - tooltipMaxH - 16))
  const tooltipLeft = hasRect
    ? Math.min(Math.max(r.left, 16), viewportW - tooltipW - 16)
    : viewportW / 2 - tooltipW / 2

  return (
    <div className="tour-root">
      {hasRect ? (
        <>
          <div className="tour-mask" style={{ top: 0, left: 0, right: 0, height: Math.max(0, r.top - PAD) }} />
          <div className="tour-mask" style={{ top: r.top + r.height + PAD, left: 0, right: 0, bottom: 0 }} />
          <div
            className="tour-mask"
            style={{ top: r.top - PAD, left: 0, width: Math.max(0, r.left - PAD), height: r.height + PAD * 2 }}
          />
          <div
            className="tour-mask"
            style={{ top: r.top - PAD, left: r.left + r.width + PAD, right: 0, height: r.height + PAD * 2 }}
          />
          <div
            className="tour-highlight"
            style={{ top: r.top - PAD, left: r.left - PAD, width: r.width + PAD * 2, height: r.height + PAD * 2 }}
          />
        </>
      ) : (
        <div className="tour-mask" style={{ top: 0, left: 0, right: 0, bottom: 0 }} />
      )}
      <div
        className={`tour-tooltip ${placeAbove ? 'above' : ''}`}
        style={{ top: tooltipTop, left: tooltipLeft, width: tooltipW, transform: placeAbove ? 'translateY(-100%)' : undefined }}
      >
        <button className="tour-close" onClick={onClose} title="Turu kapat">
          <X size={14} />
        </button>
        <div className="tour-step-count">
          {idx + 1} / {steps.length}
        </div>
        <div className="tour-title">{step.title}</div>
        <div className="tour-text">{step.text}</div>
        <div className="tour-footer">
          <button className="ghost small" onClick={onClose}>Atla</button>
          <div className="row" style={{ gap: 6 }}>
            {idx > 0 && (
              <button className="small" onClick={goBack}>
                <ArrowLeft size={13} style={{ marginRight: 4, verticalAlign: -2 }} />
                Geri
              </button>
            )}
            <button className="primary small" onClick={goNext}>
              {idx === steps.length - 1 ? 'Bitir' : 'Devam'}
              {idx < steps.length - 1 && <ArrowRight size={13} style={{ marginLeft: 4, verticalAlign: -2 }} />}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
