// Canlı Güncelleme (SSE) — backend'deki /api/events/stream'e tek bir
// EventSource ile bağlanır, gelen olayları (bkz. app/events.py) tüm
// abonelere (sayfalara) dağıtır. Her sayfa kendi ilgilendiği entity_type'ları
// dinler (bkz. useLiveEvent hook'u aşağıda) ve olay gelince kendi mevcut
// `load()`/yenileme fonksiyonunu çağırır — polling (setInterval) yedek
// olarak KALDIRILMAZ, SSE yalnızca "daha hızlı" yenilemeyi sağlar; bağlantı
// koparsa sayfalar zaten periyodik olarak kendiliğinden tazelenir.
import { useEffect } from 'react'
import { getToken } from './api'

type Listener = (entityType: string, action: string) => void

let es: EventSource | null = null
const listeners = new Set<Listener>()

export function startLiveEvents() {
  const token = getToken()
  if (!token) return
  // Zaten açık bir bağlantı varsa (ör. token yenilendi) önce kapat, taze
  // token'la yeniden aç — 2 saatlik erişim jetonu süresi dolunca eski
  // bağlantı kendiliğinden yeniden bağlanamaz (bkz. restartLiveEvents).
  es?.close()
  es = new EventSource(`/api/events/stream?token=${encodeURIComponent(token)}`)
  es.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data) as { entity_type?: string; action?: string }
      if (!data.entity_type) return
      listeners.forEach((l) => l(data.entity_type!, data.action ?? ''))
    } catch {
      // ping/yorum satırları burada işlenmez (onmessage yalnızca `data:` ile
      // başlayan satırları tetikler), ama yine de bozuk bir JSON'ı sessizce yut.
    }
  }
  // EventSource, sunucu bağlantıyı kapatırsa tarayıcı tarafından otomatik
  // olarak yeniden denenir (yerleşik davranış) — burada ekstra bir şey
  // yapmaya gerek yok, sadece hatayı sessizce yutuyoruz.
  es.onerror = () => {}
}

export function stopLiveEvents() {
  es?.close()
  es = null
}

/** Token yenilendiğinde (App.tsx'teki periyodik refreshAccessToken sonrası)
 * çağrılır — aksi halde 2 saatlik erişim jetonu süresi dolunca bağlantı
 * sessizce ölür ve bir daha kurulamaz. */
export function restartLiveEvents() {
  startLiveEvents()
}

/** Bir sayfanın belirli entity_type'lardaki canlı olaylara abone olmasını
 * sağlayan hook. `onEvent` yalnızca `entityTypes` listesindeki bir tür
 * geldiğinde çağrılır — sayfa kendi `load()` fonksiyonunu tekrar çalıştırır. */
export function useLiveEvent(entityTypes: string[], onEvent: () => void) {
  useEffect(() => {
    const unsubscribe = subscribeLive((entityType) => {
      if (entityTypes.includes(entityType)) onEvent()
    })
    return unsubscribe
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entityTypes.join(','), onEvent])
}

function subscribeLive(listener: Listener): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
