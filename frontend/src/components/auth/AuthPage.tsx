import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../../auth/AuthContext'
import { ApiError, apiFetch } from '../../lib/api'
import './auth.css'
import navbarLogo from '../../assets/navbar-logo.png'

type View = 'login' | 'register' | 'forgot'

interface FormState {
  name: string
  email: string
  password: string
  rememberMe: boolean
  acceptTerms: boolean
}

const INITIAL_FORM: FormState = {
  name: '',
  email: '',
  password: '',
  rememberMe: true,
  acceptTerms: false,
}

function GoogleIcon() {
  return (
    <svg className="auth-google-icon" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
      <path
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
        fill="#4285F4"
      />
      <path
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
        fill="#34A853"
      />
      <path
        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.06H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.94l2.85-2.22.81-.63z"
        fill="#FBBC05"
      />
      <path
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.06l3.66 2.84c.87-2.6 3.3-4.52 6.16-4.52z"
        fill="#EA4335"
      />
    </svg>
  )
}

export default function AuthPage() {
  const [searchParams] = useSearchParams()
  const { login, register, status: authStatus } = useAuth()
  const navigate = useNavigate()
  const modeParam = searchParams.get('mode')

  const [view, setView] = useState<View>(() =>
    modeParam === 'register' ? 'register' : 'login',
  )
  const [form, setForm] = useState<FormState>(INITIAL_FORM)
  const [showPassword, setShowPassword] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [serverError, setServerError] = useState<string | null>(null)
  const [forgotSent, setForgotSent] = useState(false)
  const [googleNotice, setGoogleNotice] = useState(false)

  const formRef = useRef<HTMLFormElement>(null)

  useEffect(() => {
    if (authStatus === 'authenticated') {
      navigate('/dashboard', { replace: true })
    }
  }, [authStatus, navigate])

  const setField = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }))
    setServerError(null)
    setGoogleNotice(false)
  }

  const switchView = (next: View) => {
    setView(next)
    setServerError(null)
    setForgotSent(false)
    setGoogleNotice(false)
    setShowPassword(false)
    formRef.current?.querySelectorAll('.auth-input.is-invalid').forEach((el) => {
      el.classList.remove('is-invalid')
    })
  }

  const isRegister = view === 'register'

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setServerError(null)
    setForgotSent(false)

    if (view === 'forgot') {
      if (!form.email) return
      setSubmitting(true)
      try {
        await apiFetch<{ detail: string }>('/api/v1/auth/forgot-password', {
          method: 'POST',
          body: JSON.stringify({ email: form.email }),
        })
        setForgotSent(true)
      } catch (error) {
        setServerError(error instanceof ApiError ? error.detail : 'Error de conexión')
      } finally {
        setSubmitting(false)
      }
      return
    }

    if (isRegister && (!form.name || !form.acceptTerms)) return

    setSubmitting(true)
    try {
      if (isRegister) {
        await register(form.name.trim(), form.email.trim(), form.password)
      } else {
        await login(form.email.trim(), form.password, form.rememberMe)
      }
      navigate('/dashboard', { replace: true })
    } catch (error) {
      setServerError(error instanceof ApiError ? error.detail : 'Error de conexión')
    } finally {
      setSubmitting(false)
    }
  }

  const submitLabel = isRegister ? 'Crear cuenta corporativa' : 'Entrar a la plataforma'

  return (
    <div className="auth-page">
      <div className="auth-wrap">
        <div className="auth-shell">
          <div className="auth-card">
            <div>
              <div className="auth-brand">
                <img
                  src={navbarLogo}
                  alt="NexusManager Logo"
                  className="auth-brand-logo"
                />
              </div>

              <h1 className="auth-title">
                Acceso a Nexus<span className="auth-title-coral">Manager</span>
              </h1>
              <p className="auth-subtitle">
                Gestiona tus redes y genera contenido audiovisual desde tu panel
                de control
              </p>

              {view !== 'forgot' && (
                <div className="auth-tabs" role="tablist">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={view === 'login'}
                    className={`auth-tab${view === 'login' ? ' is-active' : ''}`}
                    onClick={() => switchView('login')}
                  >
                    Iniciar sesión
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={view === 'register'}
                    className={`auth-tab${view === 'register' ? ' is-active' : ''}`}
                    onClick={() => switchView('register')}
                  >
                    Crear cuenta
                  </button>
                </div>
              )}

              {view !== 'forgot' && (
                <button
                  type="button"
                  className="auth-google-btn"
                  onClick={() => setGoogleNotice(true)}
                >
                  <GoogleIcon />
                  <span>Continuar con Google Workspace</span>
                </button>
              )}

              {googleNotice && (
                <div className="auth-banner-success" role="status">
                  <span className="material-symbols-outlined auth-banner-icon" aria-hidden="true">
                    info
                  </span>
                  El acceso con Google Workspace estará disponible próximamente.
                </div>
              )}

              {view !== 'forgot' && (
                <div className="auth-divider">
                  <div className="auth-divider-line" />
                  <span className="auth-divider-label">
                    o con tu correo corporativo
                  </span>
                </div>
              )}

              <form className="auth-form" onSubmit={handleSubmit} ref={formRef} noValidate>
                {view === 'forgot' && (
                  <button
                    type="button"
                    className="auth-back-link"
                    onClick={() => switchView('login')}
                  >
                    <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: 16 }}>
                      arrow_back
                    </span>
                    Volver a iniciar sesión
                  </button>
                )}

                {view === 'forgot' && (
                  <div className="auth-field">
                    <label className="auth-label" htmlFor="input-forgot-email">
                      Correo electrónico profesional
                    </label>
                    <input
                      id="input-forgot-email"
                      className="auth-input"
                      type="email"
                      autoComplete="email"
                      placeholder="nombre@tuempresa.com"
                      value={form.email}
                      onChange={(e) => setField('email', e.target.value)}
                      required
                    />
                  </div>
                )}

                {forgotSent && (
                  <div className="auth-banner-success" role="status">
                    <span className="material-symbols-outlined auth-banner-icon" aria-hidden="true">
                      mail
                    </span>
                    Si el correo existe, recibirás un enlace para restablecer tu
                    contraseña.
                  </div>
                )}

                {isRegister && (
                  <div className="auth-field">
                    <label className="auth-label" htmlFor="input-name">
                      Nombre y apellidos
                    </label>
                    <input
                      id="input-name"
                      className="auth-input"
                      type="text"
                      autoComplete="name"
                      placeholder="p. ej. Roberto Silva"
                      value={form.name}
                      onChange={(e) => setField('name', e.target.value)}
                      minLength={2}
                      required={isRegister}
                    />
                  </div>
                )}

                {view !== 'forgot' && (
                  <div className="auth-field">
                    <label className="auth-label" htmlFor="input-email">
                      Correo electrónico profesional
                    </label>
                    <input
                      id="input-email"
                      className="auth-input"
                      type="email"
                      autoComplete="email"
                      placeholder="nombre@tuempresa.com"
                      value={form.email}
                      onChange={(e) => setField('email', e.target.value)}
                      required
                    />
                  </div>
                )}

                {view !== 'forgot' && (
                  <div className="auth-field">
                    <div className="auth-label-row">
                      <label className="auth-label" htmlFor="input-password">
                        Contraseña
                      </label>
                      {!isRegister && (
                        <button
                          type="button"
                          className="auth-forgot-link"
                          onClick={() => switchView('forgot')}
                        >
                          ¿Olvidaste tu contraseña?
                        </button>
                      )}
                    </div>
                    <div className="auth-password-wrap">
                      <input
                        id="input-password"
                        className="auth-input auth-input-password"
                        type={showPassword ? 'text' : 'password'}
                        autoComplete={isRegister ? 'new-password' : 'current-password'}
                        placeholder="Mínimo 8 caracteres"
                        value={form.password}
                        onChange={(e) => setField('password', e.target.value)}
                        minLength={isRegister ? 8 : undefined}
                        required
                      />
                      <button
                        type="button"
                        className="auth-password-toggle"
                        aria-label={showPassword ? 'Ocultar contraseña' : 'Mostrar contraseña'}
                        onClick={() => setShowPassword((prev) => !prev)}
                      >
                        <span className="material-symbols-outlined" style={{ fontSize: 18 }}>
                          {showPassword ? 'visibility_off' : 'visibility'}
                        </span>
                      </button>
                    </div>
                  </div>
                )}

                {view !== 'forgot' && (
                  <div className="auth-session-row">
                    <label className="auth-checkbox-label">
                      <input
                        className="auth-checkbox"
                        type="checkbox"
                        checked={isRegister ? form.acceptTerms : form.rememberMe}
                        onChange={(e) =>
                          isRegister
                            ? setField('acceptTerms', e.target.checked)
                            : setField('rememberMe', e.target.checked)
                        }
                      />
                      <span>
                        {isRegister
                          ? 'Acepto los términos de servicio y la política de privacidad'
                          : 'Mantener la sesión iniciada en este equipo'}
                      </span>
                    </label>
                  </div>
                )}

                {serverError && (
                  <div className="auth-banner-error" role="alert">
                    <span className="material-symbols-outlined auth-banner-icon" aria-hidden="true">
                      error
                    </span>
                    {serverError}
                  </div>
                )}

                <button
                  type="submit"
                  className="auth-submit"
                  disabled={submitting || forgotSent}
                >
                  {submitting
                    ? 'Un momento…'
                    : view === 'forgot'
                      ? 'Enviar enlace de recuperación'
                      : submitLabel}
                </button>
              </form>
            </div>

            <div className="auth-trust">
              <span className="material-symbols-outlined auth-trust-icon" aria-hidden="true">
                verified_user
              </span>
              <span>Cifrado de grado bancario TLS 1.3 · Compatible con SSO empresarial</span>
            </div>
          </div>

          <aside className="auth-aside">
            <div className="auth-aside-brand">
              <img src={navbarLogo} alt="NexusManager" className="auth-aside-logo" />
              <span className="auth-aside-wordmark">
                Nexus<span className="brand-accent">Manager</span>
              </span>
            </div>

            <h2 className="auth-aside-title headline-md">
              Un centro de mando para tu operación social
            </h2>
            <p className="auth-aside-sub body-md">
              Publicación multicanal, campañas de pago y video automático en una
              sola consola métrica.
            </p>

            <ul className="auth-aside-list">
              <li>
                <span className="material-symbols-outlined" aria-hidden="true">hub</span>
                Alcance unificado en tiempo real
              </li>
              <li>
                <span className="material-symbols-outlined" aria-hidden="true">auto_videocam</span>
                Flujos de video IA renderizando por ti
              </li>
              <li>
                <span className="material-symbols-outlined" aria-hidden="true">payments</span>
                Atribución de ROAS por canal
              </li>
            </ul>

            <div className="auth-aside-stats">
              <div>
                <span className="tnum">482.9k</span>
                <small>Alcance unificado</small>
              </div>
              <div>
                <span className="tnum">+34 h</span>
                <small>Producción ahorrada</small>
              </div>
              <div>
                <span className="tnum">4.2x</span>
                <small>ROAS promedio</small>
              </div>
            </div>

            <div className="auth-aside-foot label-sm">
              Cifrado TLS 1.3 · Compatible con SSO empresarial
            </div>
          </aside>
        </div>
      </div>
    </div>
  )
}