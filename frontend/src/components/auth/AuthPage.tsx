import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useAuth } from '../../auth/AuthContext'
import { ApiError, apiFetch } from '../../lib/api'
import { useResendCooldown } from '../../lib/useResendCooldown'
import OtpInput from './OtpInput'
import './auth.css'
import navbarLogo from '../../assets/navbar-logo.png'

type View = 'login' | 'register' | 'forgot' | 'reset'

const OTP_LENGTH = 6
const RESEND_COOLDOWN = 60
const ERROR_BANNER_ID = 'auth-error'

interface FormState {
  name: string
  email: string
  password: string
  confirmPassword: string
  rememberMe: boolean
  acceptTerms: boolean
}

const INITIAL_FORM: FormState = {
  name: '',
  email: '',
  password: '',
  confirmPassword: '',
  rememberMe: true,
  acceptTerms: false,
}

/* El enlace que llega por correo apunta a /auth?mode=reset&email=… */
function initialView(mode: string | null): View {
  if (mode === 'register') return 'register'
  if (mode === 'reset') return 'reset'
  if (mode === 'forgot') return 'forgot'
  return 'login'
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

  const [view, setView] = useState<View>(() => initialView(modeParam))
  const [form, setForm] = useState<FormState>(() => ({
    ...INITIAL_FORM,
    email: searchParams.get('email') ?? '',
  }))
  const [showPassword, setShowPassword] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [serverError, setServerError] = useState<string | null>(null)
  const [forgotSent, setForgotSent] = useState(false)
  const [googleNotice, setGoogleNotice] = useState(false)

  /* Vista de restablecimiento: código, confirmación y estado final */
  const [code, setCode] = useState('')
  const [codeInvalid, setCodeInvalid] = useState(false)
  const [resetDone, setResetDone] = useState(false)
  const [resendNotice, setResendNotice] = useState<string | null>(null)
  const [resending, setResending] = useState(false)
  const resend = useResendCooldown(RESEND_COOLDOWN)

  const formRef = useRef<HTMLFormElement>(null)
  const otpRef = useRef<{ focus: (index?: number) => void } | null>(null)


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
    setCode('')
    setCodeInvalid(false)
    setResetDone(false)
    setResendNotice(null)
    resend.reset()
    // El correo sobrevive al cambio de vista (el flujo forgot → reset depende
    // de ello); las contraseñas nunca: son datos sensibles que no deben quedar
    // en el DOM al salir de la vista que las capturó.
    setForm((prev) => ({ ...prev, password: '', confirmPassword: '' }))
    formRef.current?.querySelectorAll('.auth-input.is-invalid').forEach((el) => {
      el.classList.remove('is-invalid')
    })
  }

  const isRegister = view === 'register'
  const isIdentity = view === 'login' || view === 'register'
  const isReset = view === 'reset'
  /* Estados terminales: sustituyen el formulario por una única acción clara */
  const finished = (view === 'forgot' && forgotSent) || (isReset && resetDone)

  const handleResend = async () => {
    const email = form.email.trim()
    if (resending || resend.remaining > 0 || !email) return

    setResending(true)
    setResendNotice(null)
    try {
      // forgot-password es la única vía sin sesión para reemitir un código.
      await apiFetch<{ detail: string }>('/api/v1/auth/forgot-password', {
        method: 'POST',
        body: JSON.stringify({ email }),
      })
      setResendNotice(
        'Si el correo está registrado, recibirás un código nuevo en unos segundos.',
      )
      setCode('')
      resend.start()
      otpRef.current?.focus(0)
    } catch (error) {
      setResendNotice(
        error instanceof ApiError ? error.detail : 'Error de conexión',
      )
    } finally {
      setResending(false)
    }
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setServerError(null)
    setForgotSent(false)
    setResendNotice(null)

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

    if (isReset) {
      const email = form.email.trim()
      if (!email) {
        setServerError('Introduce el correo donde recibiste el código.')
        return
      }
      if (code.length !== OTP_LENGTH) {
        setServerError('Introduce el código de 6 dígitos que enviamos por correo.')
        setCodeInvalid(true)
        otpRef.current?.focus(0)
        return
      }
      if (form.password.length < 8) {
        setServerError('La nueva contraseña debe tener al menos 8 caracteres.')
        return
      }
      if (form.confirmPassword && form.confirmPassword !== form.password) {
        setServerError('Las contraseñas no coinciden.')
        return
      }

      setSubmitting(true)
      try {
        await apiFetch<{ detail: string }>('/api/v1/auth/reset-password', {
          method: 'POST',
          body: JSON.stringify({
            email,
            code,
            new_password: form.password,
          }),
        })
        setResetDone(true)
        setCode('')
        setCodeInvalid(false)
        setForm((prev) => ({ ...prev, password: '', confirmPassword: '' }))
      } catch (error) {
        setServerError(error instanceof ApiError ? error.detail : 'Error de conexión')
        if (error instanceof ApiError && error.status === 400) {
          // El código caducó o no existe: el correo se conserva intacto para
          // reintentar y la atención vuelve a las casillas.
          setCode('')
          setCodeInvalid(true)
          otpRef.current?.focus(0)
        }
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

  const submitLabel = isRegister
    ? 'Crear cuenta corporativa'
    : view === 'forgot'
      ? 'Enviar código de recuperación'
      : isReset
        ? 'Restablecer contraseña'
        : 'Entrar a la plataforma'


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
                {isReset
                  ? 'Establece una nueva contraseña con el código de 6 dígitos que enviamos a tu correo'
                  : view === 'forgot'
                    ? 'Solicita un código de 6 dígitos para restablecer el acceso a tu cuenta'
                    : 'Gestiona tus redes y genera contenido audiovisual desde tu panel de control'}
              </p>

              {isIdentity && (
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

              {isIdentity && (
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

              {isIdentity && (
                <div className="auth-divider">
                  <div className="auth-divider-line" />
                  <span className="auth-divider-label">
                    o con tu correo corporativo
                  </span>
                </div>
              )}

              <form className="auth-form" onSubmit={handleSubmit} ref={formRef} noValidate>
                {/* Olvidé contraseña: el backend siempre responde 202 para no
                    revelar qué correos existen, así que la pantalla confirma el
                    envío sin afirmar que la cuenta exista. */}
                {view === 'forgot' && forgotSent && (
                  <div className="auth-state">
                    <div className="auth-banner-success" role="status">
                      <span className="material-symbols-outlined auth-banner-icon" aria-hidden="true">
                        mark_email_read
                      </span>
                      <span>
                        Si el correo{' '}
                        <span className="auth-state-mail">{form.email.trim()}</span>{' '}
                        está registrado, recibirás un código de 6 dígitos en unos
                        segundos. Revisa también la carpeta de spam.
                      </span>
                    </div>
                    <p className="body-sm auth-note">
                      Por seguridad no confirmamos si la cuenta está dada de
                      alta: si no reconoces este correo, ignora este mensaje.
                    </p>
                    <button
                      type="button"
                      className="auth-submit"
                      onClick={() => switchView('reset')}
                    >
                      Ya lo tengo, escribir el código
                    </button>
                    <div className="auth-state-links">
                      <button
                        type="button"
                        className="auth-back-link"
                        onClick={() => {
                          setForgotSent(false)
                          setServerError(null)
                        }}
                      >
                        Usar otro correo
                      </button>
                      <button
                        type="button"
                        className="auth-back-link"
                        onClick={() => switchView('login')}
                      >
                        Volver a iniciar sesión
                      </button>
                    </div>
                  </div>
                )}

                {/* Restablecimiento completado: no se abre sesión aquí, el
                    usuario vuelve al acceso con su nueva contraseña. */}
                {isReset && resetDone && (
                  <div className="auth-state">
                    <div className="auth-banner-success" role="status">
                      <span className="material-symbols-outlined auth-banner-icon" aria-hidden="true">
                        verified
                      </span>
                      Tu contraseña se ha restablecido. Ya puedes entrar con la
                      nueva contraseña.
                    </div>
                    <p className="body-sm auth-note">
                      Por seguridad el restablecimiento no inicia sesión
                      automáticamente.
                    </p>
                    <button
                      type="button"
                      className="auth-submit"
                      onClick={() => switchView('login')}
                    >
                      Ir a iniciar sesión
                    </button>
                  </div>
                )}

                {!finished && (
                  <>
                  {view !== 'login' && (
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

                  {isIdentity && (
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

                  {isIdentity && (
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

                  {/* ---------------- Restablecimiento ---------------- */}
                  {isReset && (
                    <>
                      <div className="auth-field">
                        <label className="auth-label" htmlFor="input-reset-email">
                          Correo electrónico profesional
                        </label>
                        <input
                          id="input-reset-email"
                          className="auth-input"
                          type="email"
                          autoComplete="email"
                          placeholder="nombre@tuempresa.com"
                          value={form.email}
                          onChange={(e) => setField('email', e.target.value)}
                          required
                        />
                      </div>

                      <div className="auth-field">
                        <label className="auth-label" htmlFor="input-reset-code-1">
                          Código de 6 dígitos
                        </label>
                        <OtpInput
                          id="input-reset-code"
                          ref={otpRef}
                          label="Código de 6 dígitos"
                          value={code}
                          onChange={(value) => {
                            setCode(value)
                            setCodeInvalid(false)
                            setServerError(null)
                          }}
                          errorId={codeInvalid && serverError ? ERROR_BANNER_ID : undefined}
                          invalid={codeInvalid}
                          autoFocus
                        />
                        <div className="auth-otp-row">
                          <span className="body-sm auth-hint">
                            También puedes pegar el código completo.
                          </span>
                          <button
                            type="button"
                            className="auth-forgot-link"
                            onClick={() => void handleResend()}
                            disabled={resending || resend.remaining > 0 || !form.email.trim()}
                          >
                            {resending
                              ? 'Enviando…'
                              : resend.remaining > 0
                                ? `Reenviar código en ${resend.remaining}s`
                                : 'Reenviar código'}
                          </button>
                        </div>
                      </div>

                      <div className="auth-field">
                        <label className="auth-label" htmlFor="input-reset-password">
                          Nueva contraseña
                        </label>
                        <div className="auth-password-wrap">
                          <input
                            id="input-reset-password"
                            className="auth-input auth-input-password"
                            type={showPassword ? 'text' : 'password'}
                            autoComplete="new-password"
                            placeholder="Mínimo 8 caracteres"
                            value={form.password}
                            onChange={(e) => setField('password', e.target.value)}
                            minLength={8}
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

                      <div className="auth-field">
                        <label className="auth-label" htmlFor="input-reset-confirm">
                          Repetir contraseña
                        </label>
                        <input
                          id="input-reset-confirm"
                          className="auth-input"
                          type={showPassword ? 'text' : 'password'}
                          autoComplete="new-password"
                          placeholder="Vuelve a escribirla"
                          value={form.confirmPassword}
                          onChange={(e) => setField('confirmPassword', e.target.value)}
                        />
                        <span className="body-sm auth-hint">
                          Opcional, pero evita errores de tecleo en el cambio.
                        </span>
                      </div>
                  </>
                )}

                {isIdentity && (
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
                  <div className="auth-banner-error" id={ERROR_BANNER_ID} role="alert">
                    <span className="material-symbols-outlined auth-banner-icon" aria-hidden="true">
                      error
                    </span>
                    {serverError}
                  </div>
                )}

                {isReset && resendNotice && (
                  <div className="auth-banner-success" role="status">
                    <span className="material-symbols-outlined auth-banner-icon" aria-hidden="true">
                      forward_to_inbox
                    </span>
                    {resendNotice}
                  </div>
                )}

                <button
                  type="submit"
                  className="auth-submit"
                  disabled={submitting}
                >
                  {submitting
                    ? 'Un momento…'
                    : view === 'forgot'
                      ? 'Enviar código de recuperación'
                      : submitLabel}
                </button>
                  </>
                )}
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