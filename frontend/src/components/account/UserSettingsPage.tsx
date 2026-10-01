import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../../auth/AuthContext'
import { ApiError, apiFetch, type AuthSession } from '../../lib/api'
import '../dashboard.css'
import './account.css'

interface SectionDef {
  id: string
  label: string
  icon: string
}

const SECTIONS: SectionDef[] = [
  { id: 'acct-profile', label: 'Perfil', icon: 'person' },
  { id: 'acct-security', label: 'Seguridad', icon: 'shield' },
  { id: 'acct-sessions', label: 'Sesiones', icon: 'devices' },
  { id: 'acct-preferences', label: 'Preferencias', icon: 'tune' },
  { id: 'acct-danger', label: 'Zona de peligro', icon: 'warning' },
  { id: 'acct-logout', label: 'Cerrar sesión', icon: 'logout' },
]

/* Alto de la navbar fija (4rem) y aire que se deja bajo la banda de oclusión:
   el punto de referencia para decidir qué sección está leyendo el usuario. */
const NAVBAR_H = 64
const OCCLUSION_AIR = 40

/* Resuelve en píxeles la banda que tapa la barra fija de navegación (y, si
   está, la barra global de verificación del correo).

   rootMargin no admite variables CSS, así que el alto de esa barra hay que
   leerlo aquí: VerifyEmailBar.tsx la mide con un ResizeObserver y escribe el
   resultado en --verify-bar-h como estilo inline de <body>, de donde
   getComputedStyle lo devuelve. El valor llega en px cuando la barra está
   montada y en rem (0rem) cuando no lo está, así que se normalizan ambos; si
   no fuera numérico se cae al mínimo conocido en vez de producir "NaNpx",
   que el navegador descartaría y dejaría el recorte en 0. */
function readOcclusionBand(): number {
  const raw = getComputedStyle(document.body).getPropertyValue('--verify-bar-h').trim()
  const value = Number.parseFloat(raw)
  if (!Number.isFinite(value)) return NAVBAR_H + OCCLUSION_AIR
  const rootSize = Number.parseFloat(
    getComputedStyle(document.documentElement).fontSize,
  )
  const px = raw.endsWith('rem') ? value * (Number.isFinite(rootSize) ? rootSize : 16) : value
  return NAVBAR_H + px + OCCLUSION_AIR
}

/* Fechas en formato legible absoluto (es-ES), consistente en todo el panel */
const DATE_FMT = new Intl.DateTimeFormat('es-ES', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
})

const DATETIME_FMT = new Intl.DateTimeFormat('es-ES', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
})

const PROVIDER_LABELS: Record<string, string> = {
  local: 'Correo electrónico y contraseña',
  email: 'Correo electrónico y contraseña',
  password: 'Correo electrónico y contraseña',
  google: 'Google Workspace',
}

function formatDate(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '—' : DATE_FMT.format(date)
}

function formatDateTime(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? '—' : DATETIME_FMT.format(date)
}

function toMessage(error: unknown): string {
  return error instanceof ApiError
    ? error.detail
    : 'No se pudo conectar con el servidor. Inténtalo de nuevo.'
}

async function fetchSessions(): Promise<AuthSession[]> {
  return apiFetch<AuthSession[]>('/api/v1/auth/sessions')
}

function SectionHead({
  id,
  title,
  subtitle,
  soon = false,
  children,
}: {
  id: string
  title: string
  subtitle: string
  soon?: boolean
  children?: ReactNode
}) {
  return (
    <div className="panel-head acct-head-row">
      <div>
        <h2 className="headline-sm" id={id}>
          {title}
        </h2>
        <p className="body-sm panel-sub">{subtitle}</p>
      </div>
      {soon ? (
        <span className="status-chip tone-brand">
          <span className="material-symbols-outlined acct-chip-icon" aria-hidden="true">
            hourglass_top
          </span>
          Próximamente
        </span>
      ) : (
        children
      )}
    </div>
  )
}

export default function UserSettingsPage() {
  const { user, updateProfile, logout } = useAuth()
  const navigate = useNavigate()

  const [activeSection, setActiveSection] = useState(SECTIONS[0].id)

  const [fullName, setFullName] = useState(() => user?.full_name ?? '')
  const [saving, setSaving] = useState(false)
  const [profileSaved, setProfileSaved] = useState(false)
  const [profileError, setProfileError] = useState<string | null>(null)

  const [sessions, setSessions] = useState<AuthSession[] | null>(null)
  const [loadingSessions, setLoadingSessions] = useState(true)
  const [sessionsError, setSessionsError] = useState<string | null>(null)
  const [revoking, setRevoking] = useState(false)
  const [revokeNotice, setRevokeNotice] = useState<string | null>(null)
  const [revokeError, setRevokeError] = useState<string | null>(null)

  const [signingOut, setSigningOut] = useState(false)

  // Índice lateral: marca la sección visible más cercana al borde superior.
  // La banda de oclusión se resuelve aparte porque depende de si hay barra de
  // verificación en pantalla, y cambia mientras la página está montada.
  const [occlusionBand, setOcclusionBand] = useState(readOcclusionBand)

  useEffect(() => {
    const nodes = SECTIONS.map((section) =>
      document.getElementById(section.id),
    ).filter((node): node is HTMLElement => node !== null)
    if (nodes.length === 0) return

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
        if (visible[0]) setActiveSection(visible[0].target.id)
      },
      // La banda llega hasta el borde inferior: con un recorte inferior fijo
      // las secciones cortas del final nunca entraban y la última podía
      // quedarse sin marcar al bajar del todo. Arriba se recorta todo lo que
      // tapan las barras fijas, que ahora son dos cuando el correo sigue sin
      // verificar.
      { rootMargin: `-${occlusionBand}px 0px 0px 0px` },
    )

    nodes.forEach((node) => observer.observe(node))
    return () => observer.disconnect()
  }, [occlusionBand])

  // La barra de verificación escribe --verify-bar-h en <body> desde un
  // ResizeObserver, y su primer callback llega después de que esta página
  // monte su observer: sin esto se calcularía la banda con la barra ya
  // presente pero aún sin medir, y el índice marcaría como activa la sección
  // tapada. Se recalcula en cuanto cambia la variable; si el valor no cambia,
  // setState no provoca render y el ciclo se detiene aquí.
  useEffect(() => {
    const { body } = document
    const sync = () => setOcclusionBand(readOcclusionBand())
    const mutations = new MutationObserver(sync)
    mutations.observe(body, { attributes: true, attributeFilter: ['style', 'class'] })
    sync()
    return () => mutations.disconnect()
  }, [])

  // Carga inicial de sesiones activas.
  useEffect(() => {
    let cancelled = false

    async function load() {
      setLoadingSessions(true)
      setSessionsError(null)
      try {
        const data = await fetchSessions()
        if (cancelled) return
        setSessions(data)
      } catch (error) {
        if (cancelled) return
        setSessionsError(toMessage(error))
      } finally {
        if (!cancelled) setLoadingSessions(false)
      }
    }

    void load()
    return () => {
      cancelled = true
    }
  }, [])

  const handleNameChange = (value: string) => {
    setFullName(value)
    setProfileSaved(false)
    setProfileError(null)
  }

  const handleSaveProfile = async (event: FormEvent) => {
    event.preventDefault()
    const nextName = fullName.trim()
    if (!nextName || nextName === user?.full_name) return

    setSaving(true)
    setProfileSaved(false)
    setProfileError(null)
    try {
      // Se adopta el nombre normalizado por el servidor: si lo guardáramos
      // con el valor local recortado, un doble espacio interno dejaría el
      // formulario sucio para siempre.
      const updated = await updateProfile(nextName)
      setFullName(updated.full_name)
      setProfileSaved(true)
    } catch (error) {
      setProfileError(toMessage(error))
    } finally {
      setSaving(false)
    }
  }

  const handleRevokeOthers = async () => {
    setRevoking(true)
    setRevokeError(null)
    setRevokeNotice(null)

    try {
      await apiFetch<void>('/api/v1/auth/sessions/revoke-others', { method: 'POST' })
    } catch (error) {
      setRevokeError(toMessage(error))
      setRevoking(false)
      return
    }

    // La revocación SÍ ocurrió (o fue un no-op con la cookie no viva): el aviso
    // se muestra siempre y el refresco de la lista va aparte, para no reportar
    // un fallo de red como si la operación hubiera fallado.
    setRevokeNotice(
      'Se cerraron las sesiones de los demás dispositivos, si las había.',
    )

    try {
      setSessions(await fetchSessions())
    } catch (error) {
      // Sin refresco no se conoce el estado real de la lista: se marca como no
      // cargada en vez de dejar dispositivos ya cerrados junto a un error.
      setSessions(null)
      setSessionsError(`No se pudo actualizar la lista de sesiones: ${toMessage(error)}`)
    } finally {
      setRevoking(false)
    }
  }

  const handleLogout = async () => {
    setSigningOut(true)
    try {
      await logout()
      navigate('/auth', { replace: true })
    } finally {
      setSigningOut(false)
    }
  }

  const displayName = user?.full_name || 'Tu cuenta'
  const isNameDirty = fullName.trim() !== (user?.full_name ?? '')
  const providerLabel =
    (user?.auth_provider && PROVIDER_LABELS[user.auth_provider]) ||
    user?.auth_provider ||
    '—'

  return (
    <div className="acct-shell">
      <div className="acct-wrap">
        <header className="acct-head">
          <div className="acct-head-top">
            <div>
              <h1 className="headline-lg">Ajustes de cuenta</h1>
              <p className="body-md acct-head-sub">
                Gestiona tu perfil, la seguridad de acceso y las sesiones abiertas
                en esta cuenta de NexusManager.
              </p>
            </div>
            <Link className="panel-link label-md" to="/dashboard">
              Volver al panel
            </Link>
          </div>
        </header>

        <nav className="acct-index" aria-label="Secciones de ajustes">
          <span className="label-sm acct-index-label">Secciones</span>
          {SECTIONS.map((section) => (
            <a
              key={section.id}
              href={`#${section.id}`}
              aria-current={activeSection === section.id ? 'true' : undefined}
              className={`side-link label-md${activeSection === section.id ? ' is-active' : ''}`}
              onClick={() => setActiveSection(section.id)}
            >
              <span className="material-symbols-outlined side-link-icon" aria-hidden="true">
                {section.icon}
              </span>
              {section.label}
            </a>
          ))}
        </nav>

        <div className="acct-content">
          {/* ---------------- Perfil ---------------- */}
          <section
            id="acct-profile"
            className="panel acct-section"
            aria-labelledby="acct-profile-title"
          >
            <SectionHead
              id="acct-profile-title"
              title="Perfil"
              subtitle="Tu identidad dentro de NexusManager"
            />

            <div className="quick-grid">
              <div className="quick-cell">
                <span className="label-sm">Nombre</span>
                <strong className="headline-sm">{user?.full_name || '—'}</strong>
              </div>
              <div className="quick-cell">
                <span className="label-sm">Correo electrónico</span>
                <strong className="headline-sm">{user?.email || '—'}</strong>
              </div>
              <div className="quick-cell">
                <span className="label-sm">Verificación del correo</span>
                <span
                  className={`status-chip ${user?.is_email_verified ? 'tone-live' : 'tone-neutral'}`}
                >
                  <span className="material-symbols-outlined acct-chip-icon" aria-hidden="true">
                    {user?.is_email_verified ? 'verified' : 'pending'}
                  </span>
                  {user?.is_email_verified ? 'Verificado' : 'Pendiente'}
                </span>
              </div>
              <div className="quick-cell">
                <span className="label-sm">Miembro desde</span>
                <strong className="headline-sm tnum">
                  {user?.created_at ? formatDate(user.created_at) : '—'}
                </strong>
              </div>
            </div>

            <div className="acct-rows">
              <div className="acct-row">
                <div className="acct-row-info">
                  <span className="label-md acct-row-title">Método de acceso</span>
                  <span className="body-sm acct-row-sub">{providerLabel}</span>
                </div>
              </div>
              <div className="acct-row">
                <div className="acct-row-info">
                  <span className="label-md acct-row-title">Último acceso</span>
                  <span className="body-sm acct-row-sub">
                    Se mostrará cuando el registro de accesos esté disponible
                  </span>
                </div>
              </div>
            </div>

            <form className="acct-form" onSubmit={handleSaveProfile} noValidate>
              <div className="acct-field">
                <label className="acct-label" htmlFor="acct-full-name">
                  Nombre y apellidos
                </label>
                <input
                  id="acct-full-name"
                  className="acct-input"
                  type="text"
                  autoComplete="name"
                  placeholder="p. ej. Roberto Silva"
                  value={fullName}
                  onChange={(event) => handleNameChange(event.target.value)}
                  minLength={2}
                  required
                />
                <span className="body-sm acct-hint">
                  Este nombre se muestra en el panel, la barra de mando y los
                  equipos de tu espacio de trabajo.
                </span>
              </div>

              {profileError && (
                <div className="acct-alert tone-error" role="alert">
                  <span className="material-symbols-outlined acct-alert-icon" aria-hidden="true">
                    error
                  </span>
                  {profileError}
                </div>
              )}

              {profileSaved && (
                <div className="acct-alert tone-ok" role="status">
                  <span className="material-symbols-outlined acct-alert-icon" aria-hidden="true">
                    check_circle
                  </span>
                  Cambios guardados correctamente.
                </div>
              )}

              <div className="acct-actions">
                <button
                  type="submit"
                  className="btn-coral label-md"
                  disabled={saving || !isNameDirty || fullName.trim().length < 2}
                >
                  <span className="material-symbols-outlined" aria-hidden="true">
                    check
                  </span>
                  {saving ? 'Guardando…' : 'Guardar cambios'}
                </button>
              </div>
            </form>
          </section>

          {/* ---------------- Seguridad ---------------- */}
          <section
            id="acct-security"
            className="panel acct-section"
            aria-labelledby="acct-security-title"
          >
            <SectionHead
              id="acct-security-title"
              title="Seguridad"
              subtitle="Credenciales y acceso a la cuenta"
              soon
            />

            <div className="acct-rows">
              <div className="acct-row">
                <div className="acct-row-info">
                  <span className="label-md acct-row-title">Cambiar contraseña</span>
                  <span className="body-sm acct-row-sub">
                    Se habilitará cuando el envío de correos transaccionales esté
                    disponible. Por ahora puedes usar el flujo de recuperación para
                    establecer una nueva contraseña.
                  </span>
                </div>
                <div className="acct-row-actions">
                  <button
                    type="button"
                    className="btn-secondary label-md"
                    disabled
                    aria-disabled="true"
                  >
                    <span className="material-symbols-outlined" aria-hidden="true">
                      key
                    </span>
                    Cambiar contraseña
                  </button>
                </div>
              </div>

              <div className="acct-row">
                <div className="acct-row-info">
                  <span className="label-md acct-row-title">¿Olvidaste tu contraseña?</span>
                  <span className="body-sm acct-row-sub">
                    Te llevamos a la pantalla de acceso, donde puedes solicitar un
                    código de 6 dígitos para el correo que indiques.
                  </span>
                </div>
                <Link className="btn-secondary label-md" to="/auth?mode=forgot">
                  <span className="material-symbols-outlined" aria-hidden="true">
                    mail
                  </span>
                  Recuperar contraseña
                </Link>
              </div>
            </div>
          </section>

          {/* ---------------- Sesiones ---------------- */}
          <section
            id="acct-sessions"
            className="panel acct-section"
            aria-labelledby="acct-sessions-title"
          >
            <SectionHead
              id="acct-sessions-title"
              title="Sesiones activas"
              subtitle="Dispositivos con una sesión abierta en tu cuenta"
            >
              <button
                type="button"
                className="btn-secondary label-md"
                onClick={() => void handleRevokeOthers()}
                disabled={revoking || loadingSessions || !sessions || sessions.length <= 1}
              >
                <span className="material-symbols-outlined" aria-hidden="true">
                  {revoking ? 'progress_activity' : 'phonelink_erase'}
                </span>
                {revoking ? 'Cerrando…' : 'Cerrar sesión en otros dispositivos'}
              </button>
            </SectionHead>

            {sessionsError && (
              <div className="acct-alert tone-error" role="alert">
                <span className="material-symbols-outlined acct-alert-icon" aria-hidden="true">
                  error
                </span>
                {sessionsError}
              </div>
            )}

            {revokeError && (
              <div className="acct-alert tone-error" role="alert">
                <span className="material-symbols-outlined acct-alert-icon" aria-hidden="true">
                  error
                </span>
                {revokeError}
              </div>
            )}

            {revokeNotice && (
              <div className="acct-alert tone-ok" role="status">
                <span className="material-symbols-outlined acct-alert-icon" aria-hidden="true">
                  check_circle
                </span>
                {revokeNotice}
              </div>
            )}

            {loadingSessions ? (
              <div className="acct-skeleton" role="status">
                <span className="acct-skeleton-row" aria-hidden="true" />
                <span className="acct-skeleton-row" aria-hidden="true" />
                <span className="acct-skeleton-row" aria-hidden="true" />
                <span className="body-sm acct-hint">Cargando sesiones…</span>
              </div>
            ) : sessions === null ? (
              <div className="acct-empty">
                <span className="material-symbols-outlined acct-empty-icon" aria-hidden="true">
                  cloud_off
                </span>
                <p className="body-sm">No se pudo cargar la lista de sesiones.</p>
                <p className="body-sm acct-hint">
                  Recarga la página para volver a intentarlo.
                </p>
              </div>
            ) : sessions.length > 0 ? (
              <div className="queue-list">
                {sessions.map((session) => (
                  <div className="queue-row" key={session.id}>
                    <span className="queue-icon acct-tone" aria-hidden="true">
                      <span className="material-symbols-outlined">
                        {session.current ? 'devices' : 'computer'}
                      </span>
                    </span>
                    <div className="queue-body">
                      <span className="queue-title body-md">
                        {session.current ? 'Este dispositivo' : 'Otro dispositivo'}
                      </span>
                      <span className="queue-when body-sm">
                        Inicio {formatDateTime(session.created_at)} · Expira{' '}
                        {formatDateTime(session.expires_at)}
                      </span>
                    </div>
                    <div className="acct-row-actions">
                      {session.remember_me && (
                        <span className="status-chip tone-brand">
                          <span
                            className="material-symbols-outlined acct-chip-icon"
                            aria-hidden="true"
                          >
                            bookmark
                          </span>
                          Recordar sesión
                        </span>
                      )}
                      {session.current && (
                        <span className="status-chip tone-live">
                          <span
                            className="material-symbols-outlined acct-chip-icon"
                            aria-hidden="true"
                          >
                            check_circle
                          </span>
                          Actual
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="acct-empty">
                <span className="material-symbols-outlined acct-empty-icon" aria-hidden="true">
                  devices_off
                </span>
                <p className="body-sm">No hay sesiones activas registradas.</p>
              </div>
            )}
          </section>

          {/* ---------------- Preferencias ---------------- */}
          <section
            id="acct-preferences"
            className="panel acct-section"
            aria-labelledby="acct-preferences-title"
          >
            <SectionHead
              id="acct-preferences-title"
              title="Preferencias"
              subtitle="Notificaciones, idioma, zona horaria y tema de la interfaz"
              soon
            />

            <div className="acct-rows">
              <div className="acct-row">
                <div className="acct-row-info">
                  <span className="label-md acct-row-title">Notificaciones</span>
                  <span className="body-sm acct-row-sub">
                    Elige qué avisos quieres recibir por correo.
                  </span>
                </div>
                <div className="acct-row-actions">
                  <span className="acct-check-item">
                    <input
                      className="acct-check"
                      type="checkbox"
                      defaultChecked
                      disabled
                      aria-disabled="true"
                      id="acct-pref-digest"
                    />
                    <label className="body-sm acct-check-label" htmlFor="acct-pref-digest">
                      Resumen de correo
                    </label>
                  </span>
                  <span className="acct-check-item">
                    <input
                      className="acct-check"
                      type="checkbox"
                      defaultChecked
                      disabled
                      aria-disabled="true"
                      id="acct-pref-alerts"
                    />
                    <label className="body-sm acct-check-label" htmlFor="acct-pref-alerts">
                      Alertas de campañas
                    </label>
                  </span>
                </div>
              </div>

              <div className="acct-row">
                <div className="acct-row-info">
                  <span className="label-md acct-row-title">Idioma</span>
                  <span className="body-sm acct-row-sub">
                    Idioma de la interfaz y de los correos.
                  </span>
                </div>
                <select
                  className="acct-select"
                  defaultValue="es"
                  disabled
                  aria-disabled="true"
                  aria-label="Idioma de la interfaz"
                >
                  <option value="es">Español</option>
                </select>
              </div>

              <div className="acct-row">
                <div className="acct-row-info">
                  <span className="label-md acct-row-title">Zona horaria</span>
                  <span className="body-sm acct-row-sub">
                    Se usa para programar publicaciones y mostrar métricas.
                  </span>
                </div>
                <select
                  className="acct-select"
                  defaultValue="local"
                  disabled
                  aria-disabled="true"
                  aria-label="Zona horaria"
                >
                  <option value="local">Detectada automáticamente</option>
                </select>
              </div>

              <div className="acct-row">
                <div className="acct-row-info">
                  <span className="label-md acct-row-title">Tema</span>
                  <span className="body-sm acct-row-sub">
                    Apariencia del cockpit de control.
                  </span>
                </div>
                <select
                  className="acct-select"
                  defaultValue="light"
                  disabled
                  aria-disabled="true"
                  aria-label="Tema de la interfaz"
                >
                  <option value="light">Claro</option>
                </select>
              </div>
            </div>
          </section>

          {/* ---------------- Zona de peligro ---------------- */}
          <section
            id="acct-danger"
            className="panel acct-section acct-danger"
            aria-labelledby="acct-danger-title"
          >
            <SectionHead
              id="acct-danger-title"
              title="Zona de peligro"
              subtitle="Acciones irreversibles sobre tu cuenta"
              soon
            />

            <div className="acct-warning">
              <span className="material-symbols-outlined acct-warning-icon" aria-hidden="true">
                warning
              </span>
              Estas acciones no se pueden deshacer. Ambas permanecerán
              deshabilitadas hasta que exista un proceso de verificación por
              correo.
            </div>

            <div className="acct-rows">
              <div className="acct-row">
                <div className="acct-row-info">
                  <span className="label-md acct-row-title">Exportar mis datos</span>
                  <span className="body-sm acct-row-sub">
                    Descarga una copia de tu perfil, sesiones y actividad en un
                    archivo.
                  </span>
                </div>
                <button
                  type="button"
                  className="btn-secondary label-md"
                  disabled
                  aria-disabled="true"
                >
                  <span className="material-symbols-outlined" aria-hidden="true">
                    download
                  </span>
                  Exportar
                </button>
              </div>

              <div className="acct-row">
                <div className="acct-row-info">
                  <span className="label-md acct-row-title">Eliminar cuenta</span>
                  <span className="body-sm acct-row-sub">
                    Elimina permanentemente la cuenta, los canales conectados y el
                    contenido programado.
                  </span>
                </div>
                <button
                  type="button"
                  className="acct-danger-btn label-md"
                  disabled
                  aria-disabled="true"
                >
                  <span className="material-symbols-outlined" aria-hidden="true">
                    delete
                  </span>
                  Eliminar cuenta
                </button>
              </div>
            </div>
          </section>

          {/* ---------------- Cerrar sesión ---------------- */}
          <section
            id="acct-logout"
            className="panel acct-section"
            aria-labelledby="acct-logout-title"
          >
            <SectionHead
              id="acct-logout-title"
              title="Cerrar sesión"
              subtitle="Finaliza la sesión en este dispositivo"
            />

            <div className="acct-row">
              <div className="acct-row-info">
                <span className="label-md acct-row-title">Sesión actual</span>
                <span className="body-sm acct-row-sub">
                  Se cerrará la sesión de {displayName} en este dispositivo. Las
                  demás sesiones seguirán abiertas.
                </span>
              </div>
              <button
                type="button"
                className="btn-secondary label-md"
                onClick={() => void handleLogout()}
                disabled={signingOut}
              >
                <span className="material-symbols-outlined" aria-hidden="true">
                  logout
                </span>
                {signingOut ? 'Cerrando…' : 'Cerrar sesión'}
              </button>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
