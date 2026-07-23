import { useState } from 'react'
import { api, setRefreshToken, setToken } from '../api'
import type { User } from '../types'
import { Box, LogIn, Mail } from 'lucide-react'

interface RegisterResult {
  message: string
  email_sent?: boolean | null
}

function RegisterForm({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState('')
  const [fullName, setFullName] = useState('')
  const [password, setPassword] = useState('')
  const [password2, setPassword2] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [busy, setBusy] = useState(false)
  // Kayıt formu -> kod doğrulama adımı -> bitti. E-posta zorunludur; kayıt
  // sırasında her zaman bir doğrulama kodu gönderilir. Kullanıcı isterse bu
  // adımı atlayıp e-postasını daha sonra profilinden de doğrulayabilir.
  const [step, setStep] = useState<'form' | 'verify' | 'done'>('form')
  const [code, setCode] = useState('')
  const [resendMsg, setResendMsg] = useState('')

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    if (password !== password2) {
      setError('Şifreler eşleşmiyor')
      return
    }
    setBusy(true)
    try {
      const r = await api<RegisterResult>('/auth/register', {
        method: 'POST',
        body: { username, full_name: fullName, password, email: email.trim() },
      })
      setSuccess(r.message)
      setStep('verify')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function verifyCode(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      const r = await api<RegisterResult>('/auth/register/verify-email', {
        method: 'POST',
        body: { username, code: code.trim() },
      })
      setSuccess(r.message)
      setStep('done')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function resendCode() {
    setError('')
    setResendMsg('')
    try {
      const r = await api<RegisterResult>('/auth/register/resend-code', {
        method: 'POST',
        body: { username },
      })
      setResendMsg(r.message)
    } catch (err) {
      setError((err as Error).message)
    }
  }

  if (step === 'done') {
    return (
      <div>
        <p style={{ marginBottom: 16 }}>{success}</p>
        <button className="primary" onClick={onDone}>
          Giriş Ekranına Dön
        </button>
      </div>
    )
  }

  if (step === 'verify') {
    return (
      <form onSubmit={verifyCode}>
        <p className="muted" style={{ marginBottom: 12, fontSize: 13 }}>
          <Mail size={13} style={{ verticalAlign: -2, marginRight: 5 }} />
          {email} adresine gönderilen 6 haneli kodu girin.
        </p>
        <input
          placeholder="Doğrulama kodu"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          autoFocus
          required
        />
        {error && <div className="error">{error}</div>}
        {resendMsg && <div className="muted" style={{ fontSize: 12.5 }}>{resendMsg}</div>}
        <button className="primary" disabled={busy}>
          Kodu Doğrula
        </button>
        <button type="button" onClick={resendCode}>
          Kodu Tekrar Gönder
        </button>
        <button type="button" onClick={onDone}>
          Sonra Doğrularım — Giriş Ekranına Dön
        </button>
      </form>
    )
  }

  return (
    <form onSubmit={submit}>
      <input placeholder="Kullanıcı adı" value={username} onChange={(e) => setUsername(e.target.value)} required autoFocus />
      <input placeholder="Ad Soyad" value={fullName} onChange={(e) => setFullName(e.target.value)} required />
      <input
        type="email"
        placeholder="Gmail / e-posta"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        required
      />
      <input
        type="password"
        placeholder="Şifre (en az 8 karakter, harf ve rakam)"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        minLength={8}
        required
      />
      <input
        type="password"
        placeholder="Şifre (tekrar)"
        value={password2}
        onChange={(e) => setPassword2(e.target.value)}
        required
      />
      {error && <div className="error">{error}</div>}
      <button className="primary" disabled={busy}>
        Kayıt Ol
      </button>
      <button type="button" onClick={onDone}>
        Vazgeç
      </button>
    </form>
  )
}

function ForgotPasswordForm({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState('')
  const [step, setStep] = useState<'request' | 'reset'>('request')
  const [code, setCode] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newPassword2, setNewPassword2] = useState('')
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [success, setSuccess] = useState('')
  const [busy, setBusy] = useState(false)

  async function requestCode(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      const r = await api<{ message: string }>('/auth/forgot-password', {
        method: 'POST',
        body: { username },
      })
      setInfo(r.message)
      setStep('reset')
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  async function resetPassword(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    if (newPassword !== newPassword2) {
      setError('Şifreler eşleşmiyor')
      return
    }
    setBusy(true)
    try {
      const r = await api<{ message: string }>('/auth/reset-password', {
        method: 'POST',
        body: { username, code: code.trim(), new_password: newPassword },
      })
      setSuccess(r.message)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (success) {
    return (
      <div>
        <p style={{ marginBottom: 16 }}>{success}</p>
        <button className="primary" onClick={onDone}>
          Giriş Ekranına Dön
        </button>
      </div>
    )
  }

  if (step === 'reset') {
    return (
      <form onSubmit={resetPassword}>
        <p className="muted" style={{ marginBottom: 12, fontSize: 13 }}>{info}</p>
        <input
          placeholder="E-postanıza gelen kod"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          autoFocus
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
          value={newPassword2}
          onChange={(e) => setNewPassword2(e.target.value)}
          minLength={8}
          required
        />
        {error && <div className="error">{error}</div>}
        <button className="primary" disabled={busy}>
          Şifreyi Sıfırla
        </button>
        <button type="button" onClick={onDone}>
          Vazgeç
        </button>
      </form>
    )
  }

  return (
    <form onSubmit={requestCode}>
      <p className="muted" style={{ marginBottom: 12, fontSize: 13 }}>
        Kullanıcı adınızı girin; hesabınıza kayıtlı doğrulanmış bir e-posta varsa sıfırlama kodu
        gönderilir.
      </p>
      <input
        placeholder="Kullanıcı adı"
        value={username}
        onChange={(e) => setUsername(e.target.value)}
        autoFocus
        required
      />
      {error && <div className="error">{error}</div>}
      <button className="primary" disabled={busy}>
        <Mail size={13} style={{ marginRight: 6, verticalAlign: -2 }} />
        Kod Gönder
      </button>
      <button type="button" onClick={onDone}>
        Vazgeç
      </button>
    </form>
  )
}

export default function Login({ onLogin }: { onLogin: (u: User) => void }) {
  const [mode, setMode] = useState<'login' | 'register' | 'forgot'>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      const t = await api<{ access_token: string; refresh_token: string }>('/auth/login', {
        method: 'POST',
        form: { username, password },
      })
      setToken(t.access_token)
      setRefreshToken(t.refresh_token)
      onLogin(await api<User>('/auth/me'))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const titles: Record<typeof mode, string> = {
    login: 'Devam etmek için giriş yapın',
    register: 'Yeni hesap oluşturun',
    forgot: 'Şifrenizi e-posta ile sıfırlayın',
  }

  return (
    <div className="login-wrap">
      <div className="login-box slide-up">
        <div className="login-logo">
          <Box size={20} />
        </div>
        <h1>Ulaşım Bakım Yönetim Sistemi</h1>
        <div className="sub">{titles[mode]}</div>
        {mode === 'login' && (
          <>
            <form onSubmit={submit}>
              <input
                placeholder="Kullanıcı adı"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoFocus
              />
              <input
                type="password"
                placeholder="Şifre"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              {error && <div className="error">{error}</div>}
              <button className="primary" disabled={busy || !username || !password}>
                <LogIn size={14} style={{ marginRight: 7, verticalAlign: -2 }} />
                Giriş Yap
              </button>
            </form>
            <div className="muted" style={{ marginTop: 14 }}>
              <a href="#" onClick={(e) => { e.preventDefault(); setMode('forgot') }}>
                Şifremi unuttum
              </a>
            </div>
            <div className="muted" style={{ marginTop: 6 }}>
              Hesabın yok mu?{' '}
              <a href="#" onClick={(e) => { e.preventDefault(); setMode('register') }}>
                Kayıt ol
              </a>
            </div>
          </>
        )}
        {mode === 'register' && <RegisterForm onDone={() => setMode('login')} />}
        {mode === 'forgot' && <ForgotPasswordForm onDone={() => setMode('login')} />}
      </div>
    </div>
  )
}
