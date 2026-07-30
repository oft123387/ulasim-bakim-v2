import { useMemo, useState, type ReactNode } from 'react'
import { PageHeader, SearchInput, EmptyState } from '../components/ui'
import {
  ChevronRight,
  LayoutDashboard,
  Wrench,
  CalendarDays,
  Truck,
  History as HistoryIcon,
  ClipboardList,
  Workflow,
  PackageSearch,
  Bell,
  ShieldCheck,
  Users as UsersIcon,
  ScrollText,
  Activity,
  Radio,
  HelpCircle,
} from 'lucide-react'

type Role = 'all' | 'staff' | 'admin'

interface HelpSection {
  id: string
  title: string
  icon: ReactNode
  role: Role
  body: ReactNode
}

const SECTIONS: HelpSection[] = [
  {
    id: 'dashboard',
    title: 'Dashboard',
    icon: <LayoutDashboard size={16} />,
    role: 'all',
    body: (
      <>
        <p>
          Genel durumu tek bakışta özetler: toplam araç, bekleyen/devam eden/tamamlanan bakım
          sayıları, zamanı gelen ve yaklaşan bakımlar, son tamamlananlar. Kartlara tıklayınca
          ilgili filtreyle Bakımlar sayfasına gidersiniz.
        </p>
        <p>
          "Analiz" bölümü (periyoda göre bakım grafiği + genel tamamlanma oranı) Yetki Yönetimi
          sayfasından rol bazında açılıp kapatılabilir — admin her zaman görür.
        </p>
      </>
    ),
  },
  {
    id: 'maintenances',
    title: 'Bakımlar',
    icon: <Wrench size={16} />,
    role: 'all',
    body: (
      <>
        <p>
          Açık iş emirlerinin checklist'ini gösterir. Yalnızca en alt seviye (yaprak) maddeler
          işaretlenebilir; bir madde tamamlanınca üst başlıkları otomatik tamamlanır, tüm ağaç
          bitince iş emri "Tamamlandı" olur.
        </p>
        <p>
          Bir madde yanlışlıkla işaretlenirse yetkili bir rol (onaylama izni olan) kilidi
          açabilir — madde ve altındaki her şey sıfırlanır. Otomatik açılan iş emirleri önce
          "Başlatılmayı Bekleyenler" listesinde aday olarak durur; yetkili biri "Başlat" demeden
          usta ekranında görünmez.
        </p>
      </>
    ),
  },
  {
    id: 'calendar',
    title: 'Takvim',
    icon: <CalendarDays size={16} />,
    role: 'all',
    body: <p>Yaklaşan bakımları tarih eksenine yerleştirerek gösterir — hangi hafta hangi araçların bakıma gireceğini planlamak için kullanılır.</p>,
  },
  {
    id: 'vehicles',
    title: 'Araçlar',
    icon: <Truck size={16} />,
    role: 'staff',
    body: (
      <p>
        Filodaki araçların kilometre/tarih bilgisini günceller, profil sayfasından geçmişini
        görürsünüz. Km güncellemesi otomatik bakım tespitini hemen tetikler — eşik aralığına
        giren bakımlar aday olarak açılır.
      </p>
    ),
  },
  {
    id: 'history',
    title: 'Bakım Geçmişi',
    icon: <HistoryIcon size={16} />,
    role: 'all',
    body: (
      <p>
        Geçmiş/tüm bakımları filtreleyip Excel, PDF, CSV veya Word olarak dışa aktarabilirsiniz.
        Excel'e aktarırken tek durum seçiliyse (ör. yalnızca "Tamamlandı") çıktı, periyoda göre
        (200.000 km, 500.000 km gibi) ayrı sayfalara bölünür.
      </p>
    ),
  },
  {
    id: 'plans',
    title: 'Bakım Planları',
    icon: <ClipboardList size={16} />,
    role: 'admin',
    body: (
      <p>
        Km veya süre (ay/yıl) bazlı periyotları ve açılma penceresini (yüzde ya da sabit km)
        tanımlar. Bir temel periyodun tam katı olan planlar (ör. 800.000 km = 4×200.000) burada
        görünmez, gizlidir çünkü temel periyot zaten o katta da tetiklenir.
      </p>
    ),
  },
  {
    id: 'tree',
    title: 'Bakım Ağacı',
    icon: <Workflow size={16} />,
    role: 'admin',
    body: (
      <>
        <p>
          Her planın checklist şablonunu (Bölge → Sistem → Ekipman → Kalem) düzenlersiniz. "Tümü"
          seçeneği, bir araç modelinin (veya "Genel (mevcut filo)" seçiliyken TÜM modellerin) tüm
          periyotlarını tek ağaçta, hangi periyottan geldiği etiketiyle birleştirip gösterir
          (salt okunur).
        </p>
        <p>
          <strong>Toplu Bakım Kataloğu İçe Aktar</strong>: üretici formatındaki Excel'i yükleyip
          birden fazla araç modelinin planlarını tek seferde oluşturur. "Önizle" ile önce hiçbir
          şey kaydetmeden sonucu görebilir, memnun kalırsanız "Onayla ve Yükle" ile gerçek
          yüklemeyi yaparsınız.
        </p>
        <p>
          Bir madde detayında "Kullanılan Parçalar" bölümünden, o madde tamamlanınca hangi
          parçadan (Parça Bilgi Bankası'ndan) kaç adet/miktar düşeceğini tanımlayabilirsiniz —
          miktar kesirli olabilir (ör. 1 kg'lık bir üründen 0.2 kg kullanımı).
        </p>
      </>
    ),
  },
  {
    id: 'parts',
    title: 'Parça Bilgi Bankası',
    icon: <PackageSearch size={16} />,
    role: 'all',
    body: (
      <>
        <p>
          Sık kullanılan parçaların görseli, dokümanı, kablo/devre şeması ve eğitim videosunu
          tutan bir referans kütüphanesi. Parçalar alt parçalara ayrılabilir (ör. "Motorlu Boji"
          içinde "Motor", "Fren Sistemi").
        </p>
        <p>
          <strong>Stok takibi</strong>: her parçaya stok adedi, minimum eşik ve birim (adet/kg/…)
          eklenebilir. Bir bakım maddesi tamamlanınca eşleştirilmiş parçalar otomatik düşer,
          kilit açılırsa geri eklenir; elle stok girişi/düşümü de yapılabilir.
        </p>
        <p>
          <strong>Tamir Malzemeleri</strong>: bir parçanın (ör. "Körük") tamirinde kullanılan
          malzemeleri (katalogdan seçilen, stok bağlantılı ya da serbest metin, bilgi amaçlı)
          listeleyip "Tamir Edildi" dediğinizde katalogdan seçilenlerin stoğu otomatik düşer.
        </p>
        <p>
          Stok eşiğin altına inince Bildirim Merkezi'nde "düşük stok" uyarısı çıkar.
        </p>
      </>
    ),
  },
  {
    id: 'notifications',
    title: 'Bildirim Merkezi (üstteki zil)',
    icon: <Bell size={16} />,
    role: 'all',
    body: (
      <p>
        Yeni iş emri, tamamlanan bakım, kilit açma, yeni kullanıcı, yeni öneri, geciken bakım ve
        düşük stok gibi olayları akış halinde gösterir. Bir olay olunca (bkz. Canlı Güncelleme)
        zil anında güncellenir, 60 saniyede bir de kendiliğinden tazelenir.
      </p>
    ),
  },
  {
    id: 'live',
    title: 'Canlı Güncelleme',
    icon: <Radio size={16} />,
    role: 'all',
    body: (
      <p>
        Dashboard ve Bildirim zili artık sayfayı yenilemenize gerek kalmadan
        anında güncellenir — biri bir bakımı tamamladığında, bir alarmı onayladığında ya da km
        güncellediğinde ekranınız kendiliğinden tazelenir. Bağlantı geçici olarak koparsa,
        mevcut periyodik yenileme (30-60 saniye) yedek olarak devreye girer.
      </p>
    ),
  },
  {
    id: 'permissions',
    title: 'Yetki Yönetimi',
    icon: <ShieldCheck size={16} />,
    role: 'admin',
    body: (
      <>
        <p>
          Mühendis, Amir ve Usta rollerinin neler yapabildiğini kategorilere ayrılmış kartlarda
          yönetirsiniz (Bakım İşlemleri, Veri ve Dışa Aktarma, Kullanıcı Yönetimi, Dashboard
          Görünürlüğü). Her satırın altında ne işe yaradığı yazar; değişiklikler anında kaydedilir.
        </p>
        <p>
          Bir rolde çok fazla ayar değiştirip kafanız karıştıysa "Sıfırla" ile o rolü fabrika
          varsayılanına döndürebilirsiniz. Admin her zaman tüm yetkilere sahiptir, buradan
          kısıtlanamaz.
        </p>
      </>
    ),
  },
  {
    id: 'users',
    title: 'Kullanıcılar',
    icon: <UsersIcon size={16} />,
    role: 'staff',
    body: (
      <p>
        Yeni kayıt olan kullanıcılar onay bekler durumda başlar; onaylamadan giriş yapamazlar.
        Buradan rol atayabilir, hesabı aktif/pasif yapabilirsiniz.
      </p>
    ),
  },
  {
    id: 'audit',
    title: 'İşlem Kayıtları',
    icon: <ScrollText size={16} />,
    role: 'admin',
    body: (
      <p>
        Sistemdeki hemen hemen her değişikliğin (kim, ne zaman, ne yaptı) değiştirilemez kaydı.
        Bir sorunu araştırırken "bunu kim değiştirdi" sorusunun cevabı burada.
      </p>
    ),
  },
  {
    id: 'system',
    title: 'Sistem Durumu',
    icon: <Activity size={16} />,
    role: 'admin',
    body: (
      <p>
        Veritabanı konumu/türü, çalışma süresi, araç/kullanıcı/plan sayaçları, yedekleme durumu
        gibi teknik bilgileri gösterir — özellikle "yaptığım değişiklik görünmüyor" gibi
        durumlarda (ör. yanlış veritabanına bakıyor olma ihtimali) ilk bakılacak yerdir.
      </p>
    ),
  },
]

const ROLE_LABEL: Record<Role, string> = { all: 'Herkes', staff: 'Yönetici roller', admin: 'Yalnızca Admin' }

function AccordionItem({ section, defaultOpen }: { section: HelpSection; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
      <button
        onClick={() => setOpen(!open)}
        style={{
          width: '100%', display: 'flex', alignItems: 'center', gap: 10, padding: '13px 16px',
          background: 'transparent', border: 'none', cursor: 'pointer', textAlign: 'left',
        }}
      >
        <ChevronRight size={15} style={{ transform: open ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s', flexShrink: 0 }} />
        {section.icon}
        <span style={{ fontWeight: 600, fontSize: 14 }}>{section.title}</span>
        <span
          className="muted"
          style={{ marginLeft: 'auto', fontSize: 11, whiteSpace: 'nowrap' }}
        >
          {ROLE_LABEL[section.role]}
        </span>
      </button>
      {open && (
        <div style={{ padding: '0 16px 16px 41px', fontSize: 13.5, lineHeight: 1.6, color: 'var(--text-secondary, var(--text))' }}>
          {section.body}
        </div>
      )}
    </div>
  )
}

export default function HelpPage({ isAdmin, isStaff }: { isAdmin: boolean; isStaff: boolean }) {
  const [search, setSearch] = useState('')

  const visible = useMemo(() => {
    const bySearch = SECTIONS.filter((s) => {
      if (s.role === 'admin' && !isAdmin) return false
      if (s.role === 'staff' && !isStaff) return false
      return true
    })
    const q = search.trim().toLowerCase()
    if (!q) return bySearch
    return bySearch.filter((s) => {
      const text = `${s.title} ${extractText(s.body)}`.toLowerCase()
      return text.includes(q)
    })
  }, [search, isAdmin, isStaff])

  return (
    <>
      <PageHeader
        title="Yardım"
        subtitle="Ulaşım-Bakım'daki her sayfanın ne işe yaradığını ve nasıl kullanılacağını burada bulabilirsiniz."
      />
      <SearchInput value={search} onChange={setSearch} placeholder="Yardım konusu ara… (ör. stok, önizleme, alarm)" style={{ marginBottom: 14 }} />
      {visible.length === 0 ? (
        <EmptyState icon={<HelpCircle size={28} />} title="Eşleşen konu bulunamadı" description="Farklı bir arama terimi deneyin." />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {visible.map((s, i) => (
            <AccordionItem key={s.id} section={s} defaultOpen={visible.length <= 3 && i === 0} />
          ))}
        </div>
      )}
    </>
  )
}

// Basit metin çıkarımı — ReactNode ağacındaki string parçalarını arama için
// düz metne çevirir (tam bir HTML->text dönüştürücü değildir, yalnızca bu
// sayfadaki sade <p>/<strong> yapıları için yeterlidir).
function extractText(node: ReactNode): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(extractText).join(' ')
  if (node && typeof node === 'object' && 'props' in node) {
    return extractText((node as { props: { children?: ReactNode } }).props.children)
  }
  return ''
}
