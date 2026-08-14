import { useEffect } from 'react'

// Sayfa içi "detay" görünümlerini (ör. Bakımlar listesinde bir iş emrine,
// Araçlar listesinde bir araca, Parça Bilgi Bankası'nda bir maddeye
// tıklayınca açılan detay) tarayıcı geçmişine bağlamak için kullanılır.
//
// SORUN: Bu sayfalardaki "detay" durumu (ör. MaintenancesPage'teki
// `selected`) yalnızca React state'iydi, tarayıcı geçmişiyle hiç
// ilişkilendirilmiyordu. Kullanıcı listeden bir satıra tıklayıp detaya
// girdiğinde geçmişe yeni bir girdi eklenmiyordu; tarayıcının geri tuşuna
// basınca da bu yüzden bazen detaydan çıkılamıyor ya da beklenmedik şekilde
// (bir adım atlanarak) başka bir sayfaya gidiliyordu.
//
// ÇÖZÜM: Bir detay AÇILIRKEN (satıra tıklanınca vb.) `pushDetail(page, id)`
// çağrılıp geçmişe `{ page, detail: id }` girdisi eklenir. Detaydan ÇIKARKEN
// (sayfa içindeki "Listeye Dön" düğmesi DAHİL) state doğrudan sıfırlanmaz,
// bunun yerine `window.history.back()` çağrılır — böylece hem sayfa içi geri
// düğmesi hem tarayıcının kendi geri tuşu AYNI popstate olayını tetikler ve
// `useDetailPopState` bu olayı dinleyip state'i günceller. İkisi arasında
// senkron kaybolmaz, geri tuşu her zaman bir önceki adıma düzgün döner.
//
// `page` App.tsx'teki üst düzey sayfa anahtarıyla (ör. 'maintenances') AYNI
// olmalı: App.tsx'teki genel popstate dinleyicisi zaten her girdinin
// `page` alanına bakıp üst düzey sayfayı ayarlıyor — burada AYNI `page`
// değeri korunduğu için (girdi zaten o sayfadayken eklendiği için) üst
// düzey sayfa değişmeden kalır, yalnızca bu hook'un kendi dinleyicisi
// devreye girip sayfa içindeki detay durumunu günceller.

/** Geçmişe yeni bir "detay açık" girdisi ekler. */
export function pushDetail(page: string, detail: number | string) {
  window.history.pushState({ page, detail }, '', '')
}

/** Geri/ileri tuşuna basılınca (ya da `window.history.back()` çağrılınca)
 * bu sayfaya ait detay durumunu günceller. `page` uyuşmuyorsa (kullanıcı
 * başka bir üst sayfaya gitmiş/gidiyor demektir) dokunmaz — o durumu zaten
 * App.tsx'teki genel dinleyici hallediyor. */
export function useDetailPopState<T extends number | string>(
  page: string,
  // Not: React'ın useState setter'ı (ör. `setSelected`) doğrudan verilirse
  // referansı zaten sabit kalır — her render'da yeniden abone olmaz.
  onPop: (detail: T | null) => void,
) {
  useEffect(() => {
    function onPopState() {
      const state = window.history.state as { page?: string; detail?: T } | null
      if (state?.page !== page) return
      onPop(state.detail ?? null)
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [page, onPop])
}
