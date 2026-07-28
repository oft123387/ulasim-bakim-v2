let token: string | null = sessionStorage.getItem('token')
let refreshToken: string | null = sessionStorage.getItem('refreshToken')

export function setToken(t: string | null) {
  token = t
  if (t) sessionStorage.setItem('token', t)
  else sessionStorage.removeItem('token')
}

/** Canlı güncelleme (SSE) bağlantısı token'ı URL sorgu parametresi olarak
 * taşımak zorunda olduğu için (EventSource özel başlık gönderemez) — bkz.
 * liveEvents.ts. */
export function getToken(): string | null {
  return token
}

export function setRefreshToken(t: string | null) {
  refreshToken = t
  if (t) sessionStorage.setItem('refreshToken', t)
  else sessionStorage.removeItem('refreshToken')
}

/** Erişim jetonu süresi dolmadan önce sessizce yeniler (Faz 23 — JWT +
 * Refresh Token). App.tsx bunu periyodik bir zamanlayıcıyla çağırır; burada
 * başarısız olursa (yenileme jetonu da geçersizse) sessizce false döner —
 * mevcut sayfa akışını bozmaz, kullanıcı bir sonraki normal istekte zaten
 * 401 alıp yeniden giriş ekranına yönlendirilir. */
export async function refreshAccessToken(): Promise<boolean> {
  if (!refreshToken) return false
  try {
    const res = await fetch('/api/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })
    if (!res.ok) {
      setToken(null)
      setRefreshToken(null)
      return false
    }
    const data = await res.json()
    setToken(data.access_token)
    setRefreshToken(data.refresh_token)
    return true
  } catch {
    return false
  }
}

/** Çıkış yaparken sunucudaki yenileme jetonunu iptal eder (o cihazdaki
 * jeton daha sonra bir şekilde ele geçse bile artık kullanılamaz). */
export async function revokeRefreshToken(): Promise<void> {
  if (!refreshToken) return
  try {
    await fetch('/api/auth/logout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })
  } catch {
    // Çıkış zaten yerel olarak gerçekleşecek; sunucu isteği başarısız olsa
    // da (ör. ağ koptu) kullanıcıyı bekletmeye gerek yok.
  }
}

async function readError(res: Response): Promise<string> {
  try {
    const data = await res.json()
    return data.detail ?? 'İşlem başarısız'
  } catch {
    return 'İşlem başarısız'
  }
}

export async function api<T = unknown>(
  path: string,
  options: { method?: string; body?: unknown; form?: Record<string, string> } = {},
): Promise<T> {
  const headers: Record<string, string> = {}
  let body: BodyInit | undefined
  if (options.form) {
    body = new URLSearchParams(options.form)
  } else if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(options.body)
  }
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(`/api${path}`, { method: options.method ?? 'GET', headers, body })
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

/** Dosya yükleme (multipart/form-data). */
export async function apiUpload<T = unknown>(path: string, file: File): Promise<T> {
  const form = new FormData()
  form.append('file', file)
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(`/api${path}`, { method: 'POST', headers, body: form })
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

/** Dosya + ek metin alanları yükleme (multipart/form-data). */
export async function apiUploadWithFields<T = unknown>(
  path: string,
  file: File,
  fields: Record<string, string | number>,
): Promise<T> {
  const form = new FormData()
  for (const [k, v] of Object.entries(fields)) form.append(k, String(v))
  form.append('file', file)
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(`/api${path}`, { method: 'POST', headers, body: form })
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

/** Dosya indirme: yetkilendirme başlığı gerektiği için <a href> yerine fetch+blob kullanır. */
export async function apiDownload(path: string, filename: string): Promise<void> {
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  const res = await fetch(`/api${path}`, { headers })
  if (!res.ok) throw new Error(await readError(res))
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}
