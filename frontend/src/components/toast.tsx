import { useEffect, useState } from 'react'
import { CheckCircle2, XCircle, Info, X } from 'lucide-react'

/* =========================================================================
   Global toast/snackbar bildirimi.
   Amaç: kullanıcı bir işlemi (madde tamamlama, not kaydetme, KM güncelleme
   vb.) yaptığında sayfa değişmeden, formu terk etmeden "✓ Kaydedildi" gibi
   anında bir onay görsün — sessizce başarılı olan bir API çağrısı, kaydın
   gerçekten işlendiğinden emin olamadığı için kullanıcıyı tekrar tekrar
   kontrol etmeye ("gerçekten kaydoldu mu?") zorlar.

   Basit bir pub/sub ile çalışır: showToast(...) herhangi bir yerden
   çağrılabilir (modül state'i, React context'e gerek yok); <ToastHost/>
   App.tsx'te bir kez render edilip aboneliği üstlenir.
   ========================================================================= */

export type ToastTone = 'success' | 'error' | 'info'

export interface ToastMessage {
  id: number
  text: string
  tone: ToastTone
}

let nextId = 1
let listeners: ((t: ToastMessage) => void)[] = []

export function showToast(text: string, tone: ToastTone = 'success') {
  const msg: ToastMessage = { id: nextId++, text, tone }
  listeners.forEach((l) => l(msg))
}

const TOAST_ICON: Record<ToastTone, React.ReactNode> = {
  success: <CheckCircle2 size={17} />,
  error: <XCircle size={17} />,
  info: <Info size={17} />,
}

const DEFAULT_DURATION_MS = 3200

export function ToastHost() {
  const [items, setItems] = useState<ToastMessage[]>([])

  useEffect(() => {
    function onMsg(msg: ToastMessage) {
      setItems((prev) => [...prev, msg])
      setTimeout(() => {
        setItems((prev) => prev.filter((i) => i.id !== msg.id))
      }, DEFAULT_DURATION_MS)
    }
    listeners.push(onMsg)
    return () => {
      listeners = listeners.filter((l) => l !== onMsg)
    }
  }, [])

  function dismiss(id: number) {
    setItems((prev) => prev.filter((i) => i.id !== id))
  }

  if (items.length === 0) return null

  return (
    <div className="toast-host" role="status" aria-live="polite">
      {items.map((t) => (
        <div key={t.id} className={`toast toast-${t.tone}`}>
          <span className="toast-icon">{TOAST_ICON[t.tone]}</span>
          <span className="toast-text">{t.text}</span>
          <button className="toast-close" onClick={() => dismiss(t.id)} title="Kapat">
            <X size={13} />
          </button>
        </div>
      ))}
    </div>
  )
}
