import { Link } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import profileAvatar from '../assets/profile-avatar.png'

const NAV_LINKS: { label: string; path: string; active?: boolean }[] = [
  { label: 'Inicio', path: 'inicio', active: true },
  { label: 'Editor IA', path: 'editor-ia' },
  { label: 'Métricas', path: 'metricas' },
  { label: 'Campañas', path: 'campanas' },
  { label: 'Precios', path: 'precios' },
]

function Navbar() {
  const { status, user } = useAuth()
  const authenticated = status === 'authenticated'

  return (
    <header className="navbar">
      <div className="navbar-inner">
        <div className="navbar-brand">
          <img
            src="/logo.svg"
            alt="Logotipo de NexusManager"
            className="navbar-logo"
          />
          <span className="navbar-wordmark">
            Nexus<span className="brand-accent">Manager</span>
          </span>
        </div>

        <nav className="navbar-links">
          {NAV_LINKS.map((link) => (
            <a
              key={link.path}
              href="#"
              aria-current={link.active ? 'page' : undefined}
              className={link.active ? 'navbar-link is-active' : 'navbar-link label-md'}
            >
              {link.label}
            </a>
          ))}
        </nav>

        <div className="navbar-actions">
          {authenticated ? (
            <Link className="btn-signup label-md" to="/dashboard">
              Ir al panel
            </Link>
          ) : (
            <>
              <Link className="btn-login label-md" to="/auth">
                Iniciar sesión
              </Link>
              <Link className="btn-signup label-md" to="/auth?mode=register">
                Comenzar gratis
              </Link>
            </>
          )}
          {authenticated && (
            /* Única parada de tab hacia /settings: el texto del enlace lo
               aporta el alt de la imagen, que antes quedaba eclipsado por el
               aria-label del Link. */
            <Link className="navbar-avatar-link" to="/settings">
              <img
                src={profileAvatar}
                alt={`Ajustes de ${user?.full_name || 'tu cuenta'}`}
                className="navbar-avatar"
              />
            </Link>
          )}
        </div>
      </div>
    </header>
  )
}

export default Navbar