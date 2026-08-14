// Sirio filosu, km periyotlarını kısa "M"/"Y" koduyla takip eder (bkz.
// backend/app/services.py sirio_period_label — BİREBİR aynı birimler:
// M = 10.000 km, Y = 12 x 10.000 = 120.000 km). Bakım Planları sayfasında
// bir Sirio planının adı zaten bu kısa koddur (ör. "3M", "12Y"); ama bir
// bakımın kendi HEDEF km'si (ör. 1.520.000 — planın kaçıncı tekrarı olduğu)
// ayrı bir sayıdır ve aynı kurala göre kısa koda çevrilmelidir (ör. "152M").
// Bu yardımcı, o çeviriyi frontend'de (Dashboard/Bakım Geçmişi/Takvim/
// Bakımlar/Araçlar sayfalarındaki ortak "hedef" gösterimi için) tekrarlar.
const SIRIO_KM_UNIT = { Y: 120_000, M: 10_000 } as const

const TR_MAP: Record<string, string> = {
  ç: 'c', Ç: 'C', ğ: 'g', Ğ: 'G', ı: 'i', İ: 'I', ö: 'o', Ö: 'O', ş: 's', Ş: 'S', ü: 'u', Ü: 'U',
}
function normalizeTr(s: string): string {
  return s.replace(/[çÇğĞıİöÖşŞüÜ]/g, (c) => TR_MAP[c] ?? c)
}

export function isSirioModel(vehicleModel: string | null | undefined): boolean {
  if (!vehicleModel) return false
  return normalizeTr(vehicleModel).toLowerCase().includes('sirio')
}

/** Sirio modelinde ve km TAM olarak Y ya da M birimine bölünüyorsa kısa kodu
 * (ör. "12Y", "152M") döner; aksi halde (Sirio değilse ya da tam bölünmüyorsa)
 * null döner — çağıran taraf normal "X km" biçimine düşmeli. */
export function sirioKmCode(km: number, vehicleModel: string | null | undefined): string | null {
  if (!isSirioModel(vehicleModel) || km <= 0) return null
  if (km % SIRIO_KM_UNIT.Y === 0) return `${km / SIRIO_KM_UNIT.Y}Y`
  if (km % SIRIO_KM_UNIT.M === 0) return `${km / SIRIO_KM_UNIT.M}M`
  return null
}

const fmtKmTr = (n: number) => n.toLocaleString('tr-TR')

/** Bir bakımın "hedef" gösterimi: süre bazlıysa tarih, km bazlıysa Sirio
 * modelinde kısa kod (ör. "152M"), aksi halde "1.520.000 km". */
export function targetLabel(
  targetKm: number | null | undefined,
  targetDate: string | null | undefined,
  vehicleModel: string | null | undefined,
): string {
  if (targetDate) return new Date(targetDate).toLocaleDateString('tr-TR')
  const km = targetKm ?? 0
  return sirioKmCode(km, vehicleModel) ?? `${fmtKmTr(km)} km`
}

/** Bir bakımın hedefini HER ZAMAN düz (kısa koda çevirmeden) gösterir —
 * süre bazlıysa tarih, km bazlıysa "1.520.000 km". Kısa kod (ör. "152M")
 * ile bakımın periyot ADI (ör. plan.name = "1M") aynı satırda gösterilirse
 * karışabildiği için (152M hedef, 1M periyot — ikisi de "M" ile bitiyor),
 * periyot adının yanında hedefi net ayırt etmek için bu düz biçim kullanılır
 * (ör. Dashboard'daki Son Tamamlanan Bakımlar listesi). */
export function rawTargetLabel(
  targetKm: number | null | undefined,
  targetDate: string | null | undefined,
): string {
  if (targetDate) return new Date(targetDate).toLocaleDateString('tr-TR')
  return `${fmtKmTr(targetKm ?? 0)} km`
}

function monthsLabel(months: number): string {
  return months % 12 === 0 ? `${months / 12} yıl` : `${months} ay`
}

/** Kademeli/kümülatif (birden fazla periyodun tek iş emrinde birleştiği)
 * bir bakımın TÜM kapsadığı periyotları tek bir etikette gösterir (ör.
 * "1M + 3M", "25.000 km + 50.000 km", "1 ay + 3 ay") — yalnızca "yöneten"
 * (en büyük) planın adını (ör. sadece "3M") göstermek, bu bakımın aslında
 * daha küçük periyotları (1M gibi) da kapsadığını gizler ve kafa karıştırır.
 * coveredTiers boş/tek elemanlıysa (kademeli değil) sadece fallbackPlanName
 * olduğu gibi döner. */
export function mergedPeriodLabel(
  coveredTiers: number[] | null | undefined,
  planType: 'km' | 'time' | 'unknown' | undefined,
  vehicleModel: string | null | undefined,
  fallbackPlanName: string,
): string {
  const tiers = coveredTiers ?? []
  if (tiers.length <= 1) return fallbackPlanName
  const sorted = [...tiers].sort((a, b) => a - b)
  if (planType === 'time') return sorted.map(monthsLabel).join(' + ')
  return sorted.map((km) => sirioKmCode(km, vehicleModel) ?? `${fmtKmTr(km)} km`).join(' + ')
}

/** Kademeli bir bakımın kapsadığı periyotlardan yalnızca EN BÜYÜĞÜNÜ (yöneten
 * periyodu) gösterir — ör. "800.000 km" (altındaki 200.000 km vb. küçük
 * periyotları listede tekrar tekrar yazmaz). Bakımlar sayfasındaki Bakım
 * Akışı listesi için: küçük periyotlar zaten bakım detayına girilince
 * ("Bu bakım şu periyotları birleştirir" bölümünde) görülüyor, listede ayrıca
 * uzun "200.000 km + 800.000 km" etiketine gerek yok. Diğer ekranlarda
 * (bakım detay başlığı, takvim vb.) kafa karışıklığını önlemek için TÜM
 * periyotları gösteren mergedPeriodLabel kasıtlı olarak kullanılmaya devam
 * eder — bu yardımcı yalnızca liste görünümü içindir. */
export function topPeriodLabel(
  coveredTiers: number[] | null | undefined,
  planType: 'km' | 'time' | 'unknown' | undefined,
  vehicleModel: string | null | undefined,
  fallbackPlanName: string,
): string {
  const tiers = coveredTiers ?? []
  if (tiers.length === 0) return fallbackPlanName
  const top = Math.max(...tiers)
  if (planType === 'time') return monthsLabel(top)
  return sirioKmCode(top, vehicleModel) ?? `${fmtKmTr(top)} km`
}
