import { useState } from 'react'
import { api } from '../api'
import type { User } from '../types'
import { ROLE_TR } from '../types'
import { PageHeader, Avatar, Badge } from '../components/ui'
import { Moon, Sun, KeyRound, UserCircle2, Mail, CheckCircle2, AlertTriangle } from 'lucide-react'

interface EmailActionResult {
  message: string
  email_sent?: boolean | null
}

function EmailCard({ user, onUserUpdate }: { user: User; onUserUpdate: (u: User) => void }) {
  // Adım: 'idle' (mevcut durumu göster) -> 'code' (kod gönderildi, giriş bekleniyor)
  const [email, setEmail] = useState(user.email || '')
  const [step, setStep] = useState<'idle' | 'code'>('idle')
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [busy, setBusy] = useState(false)

  async function sendCode(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setInfo('')
    setBusy(true)
    try {
      const r = await api<EmailActionResult>('/auth/email/send-code', {
        method: 'POST',
        body: { email: email.trim() },
      })
      setInfo(r.message)
      setStep('code')
      // Adres hemen (doğrulanmamış olarak) kaydedildiği için üst bileşene
      // haber verelim ki "Doğrulanmadı" rozeti anında görünsün.
      onUserUpdate({ ...user, email: email.trim(), email_verified: false })
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function verify(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      const updated = await api<User>('/auth/email/verify', {
        method: 'POST',
        body: { code: code.trim() },
      })
      onUserUpdate(updated)
      setInfo('E-posta doğrulandı.')
      setStep('idle')
      setCode('')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function resend() {
    setError('')
    setBusy(true)
    try {
      const r = await api<EmailActionResult>('/auth/email/send-code', {
        method: 'POST',
        body: { email: (user.email || email).trim() },
      })
      setInfo(r.message)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card slide-up" style={{ maxWidth: 460 }}>
      <div className="card-head">
        <div className="card-title">
          <Mail size={16} style={{ verticalAlign: -3, marginRight: 6 }} />
          Gmail / E-posta
        </div>
      </div>
      <div className="row" style={{ gap: 8, marginBottom: 14, flexWrap: 'wrap' }}>
        {user.email ? (
          <>
            <span style={{ fontWeight: 600, fontSize: 13.5 }}>{user.email}</span>
            {user.email_verified ? (
              <Badge tone="completed">
                <CheckCircle2 size={12} style={{ marginRight: 4, verticalAlign: -2 }} />
                Doğrulandı
              </Badge>
            ) : (
              <Badge tone="pending">
                <AlertTriangle size={12} style={{ marginRight: 4, verticalAlign: -2 }} />
                Doğrulanmadı
              </Badge>
            )}
          </>
        ) : (
          <span className="muted" style={{ fontSize: 13 }}>Henüz bir e-posta eklenmedi.</span>
        )}
      </div>

      {step === 'idle' && (
        <form onSubmit={sendCode} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <input
            type="email"
            placeholder="ornek@gmail.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          {error && <div className="error">{error}</div>}
          {info && <div className="muted" style={{ fontSize: 12.5 }}>{info}</div>}
          <button className="primary" disabled={busy} style={{ alignSelf: 'flex-start' }}>
            {user.email ? 'E-postayı Güncelle ve Kod Gönder' : 'Ekle ve Kod Gönder'}
          </button>
          {user.email && !user.email_verified && (
            <button type="button" disabled={busy} onClick={() => setStep('code')} style={{ alignSelf: 'flex-start' }}>
              Zaten bir kodum var, girmek istiyorum
            </button>
          )}
        </form>
      )}

      {step === 'code' && (
        <form onSubmit={verify} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>
            {(user.email || email)} adresine gönderilen 6 haneli kodu girin.
          </p>
          <input
            placeholder="Doğrulama kodu"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            autoFocus
            required
          />
          {error && <div className="error">{error}</div>}
          <div className="row" style={{ gap: 8 }}>
            <button className="primary" disabled={busy}>Doğrula</button>
            <button type="button" disabled={busy} onClick={resend}>Kodu Tekrar Gönder</button>
            <button type="button" disabled={busy} onClick={() => setStep('idle')}>Vazgeç</button>
          </div>
        </form>
      )}
    </div>
  )
}

export default function ProfilePage({
  user,
  theme,
  onThemeChange,
  onUserUpdate,
}: {
  user: User
  theme: 'light' | 'dark'
  onThemeChange: (t: 'light' | 'dark') => void
  onUserUpdate: (u: User) => void
}) {
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [busy, setBusy] = useState(false)

  async function changePassword(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setSuccess('')
    if (newPassword !== confirmPassword) {
      setError('Yeni şifreler eşleşmiyor')
      return
    }
    setBusy(true)
    try {
      await api('/auth/change-password', {
        method: 'POST',
        body: { current_password: currentPassword, new_password: newPassword },
      })
      setSuccess('Şifreniz güncellendi.')
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <PageHeader title="Profil" />

      <div className="grid grid-2" style={{ alignItems: 'start' }}>
        <div className="card slide-up">
          <div className="card-head">
            <div className="card-title">
              <UserCircle2 size={16} style={{ verticalAlign: -3, marginRight: 6 }} />
              Kişisel Bilgiler
            </div>
          </div>
          <div className="row" style={{ gap: 14, marginBottom: 6 }}>
            <Avatar name={user.full_name} size={52} />
            <div>
              <div style={{ fontWeight: 700, fontSize: 16 }}>{user.full_name}</div>
              <div className="muted" style={{ fontSize: 13 }}>@{user.username}</div>
            </div>
          </div>
          <div className="row" style={{ marginTop: 14, gap: 8, flexWrap: 'wrap' }}>
            <Badge tone="neutral">{ROLE_TR[user.role]}</Badge>
            <Badge tone={user.is_active ? 'completed' : 'neutral'}>
              {user.is_active ? 'Hesap Aktif' : 'Hesap Pasif'}
            </Badge>
          </div>
        </div>

        <div className="card slide-up">
          <div className="card-head">
            <div className="card-title">
              {theme === 'dark' ? <Moon size={16} style={{ verticalAlign: -3, marginRight: 6 }} /> : <Sun size={16} style={{ verticalAlign: -3, marginRight: 6 }} />}
              Görünüm
            </div>
          </div>
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <span className="muted" style={{ fontSize: 13.5 }}>
              Arayüz temasını açık veya koyu olarak ayarlayın.
            </span>
            <div className="tabs">
              <button className={theme === 'light' ? 'active' : ''} onClick={() => onThemeChange('light')}>
                <Sun size={13} style={{ verticalAlign: -2, marginRight: 5 }} /> Açık
              </button>
              <button className={theme === 'dark' ? 'active' : ''} onClick={() => onThemeChange('dark')}>
                <Moon size={13} style={{ verticalAlign: -2, marginRight: 5 }} /> Koyu
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="card slide-up" style={{ maxWidth: 460 }}>
        <div className="card-head">
          <div className="card-title">
            <KeyRound size={16} style={{ verticalAlign: -3, marginRight: 6 }} />
            Şifre Değiştir
          </div>
        </div>
        <form onSubmit={changePassword} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <input
            type="password"
            placeholder="Mevcut şifre"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            required
          />
          <input
            type="password"
            placeholder="Yeni şifre (en az 8 karakter, harf ve rakam)"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            minLength={8}
            required
          />
          <input
            type="password"
            placeholder="Yeni şifre (tekrar)"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            minLength={8}
            required
          />
          {error && <div className="error">{error}</div>}
          {success && <div className="muted" style={{ color: 'var(--green)', fontWeight: 600 }}>{success}</div>}
          <button className="primary" disabled={busy} style={{ alignSelf: 'flex-start' }}>
            Şifreyi Güncelle
          </button>
        </form>
      </div>

      <EmailCard user={user} onUserUpdate={onUserUpdate} />
    </>
  )
}
