import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertTriangle, RefreshCw, RotateCcw } from 'lucide-react'

/* =========================================================================
   Genel amaçlı hata sınırı (Error Boundary).
   React'te bir bileşenin render sırasında beklenmeyen bir hata fırlatması
   (ör. API'den beklenmeyen bir null/undefined gelmesi) o ana kadar hiçbir
   şekilde yakalanmıyordu — sonuç, kullanıcının hiçbir açıklama görmediği
   boş/beyaz bir ekrandı. Bu bileşen App'i sararak böyle bir hatayı yakalar,
   dostane bir mesajla "Tekrar Dene" (yeniden render) veya "Sayfayı Yenile"
   (tam reload) seçeneği sunar. Hata detayını yalnızca konsola basar —
   kullanıcıya teknik hata metni göstermez (bkz. proje güvenlik önerileri:
   "kullanıcıya teknik hata detaylarının gösterilmemesi").
   ========================================================================= */

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false }

  static getDerivedStateFromError(): State {
    return { hasError: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Şimdilik yalnızca konsola — ileride burada backend'e ("client error
    // log") gönderen bir çağrı eklenebilir ki üretimdeki hatalar
    // kullanıcının şikayet etmesini beklemeden fark edilebilsin.
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  handleRetry = () => {
    this.setState({ hasError: false })
  }

  handleReload = () => {
    window.location.reload()
  }

  render() {
    if (!this.state.hasError) return this.props.children
    return (
      <div
        style={{
          // index.css'teki html{zoom} küçültmesini telafi eder — bkz.
          // --vh-fill tanımı (aksi halde bu tam ekran hata sayfası da
          // ekranın altında boş bir şerit bırakırdı).
          minHeight: 'var(--vh-fill, 100vh)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 24,
        }}
      >
        <div className="card" style={{ maxWidth: 420, textAlign: 'center', padding: 32 }}>
          <div
            style={{
              width: 52,
              height: 52,
              borderRadius: 14,
              background: 'var(--red-light)',
              color: 'var(--red)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              margin: '0 auto 16px',
            }}
          >
            <AlertTriangle size={26} />
          </div>
          <h2 style={{ margin: '0 0 8px', fontSize: 17 }}>Bir şeyler ters gitti</h2>
          <p className="muted" style={{ margin: '0 0 22px', fontSize: 13, lineHeight: 1.5 }}>
            Beklenmeyen bir hata oluştu. Verileriniz kaybolmadı — aşağıdaki seçeneklerden birini
            deneyebilirsiniz. Sorun devam ederse yöneticinizle iletişime geçin.
          </p>
          <div className="row" style={{ justifyContent: 'center', gap: 8 }}>
            <button onClick={this.handleRetry}>
              <RotateCcw size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
              Tekrar Dene
            </button>
            <button className="primary" onClick={this.handleReload}>
              <RefreshCw size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
              Sayfayı Yenile
            </button>
          </div>
        </div>
      </div>
    )
  }
}
