import { useState } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from './useAuth'
import { resolveApiBaseUrl } from '../services/api'

function destination(location) {
  const value = new URLSearchParams(location.search).get('redirect') || '/dashboard'
  return value.startsWith('/') && !value.startsWith('//') && !value.includes('\\') ? value : '/dashboard'
}

export function ProtectedRoute({ children }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return <div className="mx-auto mt-24 max-w-md text-center text-cyan-200">Restoring secure session…</div>
  if (!user) return <Navigate to={`/login?redirect=${encodeURIComponent(location.pathname + location.search)}`} replace />
  return children
}

function AuthPage({ mode }) {
  const isRegister = mode === 'register'
  const { user, login, register } = useAuth()
  const location = useLocation()
  const navigate = useNavigate()
  const [form, setForm] = useState({ name: '', email: '', password: '', confirm: '' })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  if (user) return <Navigate to={destination(location)} replace />

  const update = (event) => setForm((value) => ({ ...value, [event.target.name]: event.target.value }))
  const submit = async (event) => {
    event.preventDefault()
    setError('')
    if (isRegister && !form.name.trim()) return setError('Name is required.')
    if (!/^\S+@\S+\.\S+$/.test(form.email.trim())) return setError('Enter a valid email address.')
    if (form.password.length < 8) return setError('Password must be at least 8 characters.')
    if (isRegister && form.password !== form.confirm) return setError('Passwords do not match.')
    if (isRegister && !(/[a-z]/.test(form.password) && /[A-Z]/.test(form.password) && /\d/.test(form.password))) {
      return setError('Use at least 8 characters with uppercase, lowercase, and a number.')
    }
    setBusy(true)
    try {
      if (isRegister) await register({ name: form.name, email: form.email, password: form.password })
      else await login({ email: form.email, password: form.password })
      navigate(destination(location), { replace: true })
    } catch (requestError) {
      const status = requestError?.response?.status
      const detail = requestError?.response?.data?.detail
      const apiMessage = Array.isArray(detail)
        ? detail.map((item) => item.msg).filter(Boolean).join(' ')
        : detail
      if (!requestError?.response) {
        const reason = requestError?.code === 'ECONNABORTED' ? 'The request timed out.' : 'The backend could not be reached.'
        setError(`${reason} Check that CITADEL is running at ${resolveApiBaseUrl()}.`)
      } else {
        setError(status === 409
          ? 'An account with this email already exists.'
          : status === 401
            ? 'Invalid email or password.'
            : status === 503
              ? 'Authentication is not configured on the server.'
              : apiMessage || `The server returned HTTP ${status}.`)
      }
    } finally {
      setBusy(false)
    }
  }

  const inputClass = 'mt-2 w-full rounded-2xl border border-white/10 bg-black/25 px-4 py-3 text-sm text-white outline-none transition placeholder:text-slate-500 focus:border-[#00E5FF]/60 focus:ring-2 focus:ring-[#00E5FF]/15'
  return (
    <main className="mx-auto flex w-full max-w-lg flex-1 items-center justify-center py-10">
      <section className="glass-card neon-panel w-full rounded-[32px] p-6 sm:p-9">
        <div className="mb-8 text-center">
          <p className="terminal-text text-xs uppercase tracking-[0.35em] text-[#00E5FF]">Secure access gateway</p>
          <h2 className="mt-3 text-3xl font-semibold text-white">{isRegister ? 'Create your account' : 'Welcome back'}</h2>
          <p className="mt-2 text-sm text-slate-400">{isRegister ? 'Join the CITADEL intelligence workspace.' : 'Sign in to continue to CITADEL.'}</p>
        </div>
        <form onSubmit={submit} className="space-y-5" noValidate>
          {isRegister && <label className="block text-sm text-slate-300">Name<input autoComplete="name" required name="name" value={form.name} onChange={update} className={inputClass} /></label>}
          <label className="block text-sm text-slate-300">Email<input autoComplete="email" type="email" required name="email" value={form.email} onChange={update} className={inputClass} /></label>
          <label className="block text-sm text-slate-300">Password
            <span className="relative block"><input autoComplete={isRegister ? 'new-password' : 'current-password'} type={showPassword ? 'text' : 'password'} required minLength={8} name="password" value={form.password} onChange={update} className={`${inputClass} pr-20`} />
              <button type="button" onClick={() => setShowPassword((value) => !value)} className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-cyan-200">{showPassword ? 'Hide' : 'Show'}</button>
            </span>
          </label>
          {isRegister && <label className="block text-sm text-slate-300">Confirm password<input autoComplete="new-password" type={showPassword ? 'text' : 'password'} required name="confirm" value={form.confirm} onChange={update} className={inputClass} /></label>}
          {error && <p role="alert" className="rounded-xl border border-red-400/30 bg-red-400/10 px-4 py-3 text-sm text-red-200">{error}</p>}
          <button disabled={busy} className="w-full rounded-2xl border border-[#00E5FF]/30 bg-gradient-to-r from-cyan-500/20 to-emerald-400/15 px-5 py-3 font-semibold text-white shadow-[0_0_25px_rgba(0,229,255,0.1)] transition hover:border-[#00E5FF]/60 disabled:cursor-wait disabled:opacity-60">
            {busy ? 'Please wait…' : isRegister ? 'Create account' : 'Login'}
          </button>
        </form>
        <p className="mt-6 text-center text-sm text-slate-400">{isRegister ? 'Already have an account?' : "Don't have an account?"}{' '}
          <Link className="text-cyan-200 hover:text-white" to={isRegister ? '/login' : `/register${location.search}`}>{isRegister ? 'Login' : 'Register'}</Link>
        </p>
      </section>
    </main>
  )
}

export function LoginPage() { return <AuthPage mode="login" /> }
export function RegisterPage() { return <AuthPage mode="register" /> }
