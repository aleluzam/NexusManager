import { Link } from 'react-router-dom'

const PERKS = [
  { icon: 'check_circle', label: 'Sin tarjeta de crédito' },
  { icon: 'bolt', label: 'Activación en 60 segundos' },
  { icon: 'verified_user', label: 'Integración oficial de API' },
] as const

const PLATFORMS = [
  { icon: 'photo_camera', label: 'Instagram' },
  { icon: 'play_circle', label: 'TikTok' },
  { icon: 'videocam', label: 'YouTube' },
  { icon: 'business_center', label: 'LinkedIn' },
] as const

const KPIS = [
  { lbl: 'Alcance unificado', val: '482.9k', delta: '+18.4%', coral: false },
  { lbl: 'Interacción', val: '5.7%', delta: '+1.2%', coral: false },
  { lbl: 'ROAS promedio', val: '4.2x', delta: 'on track', coral: true },
  { lbl: 'Horas ahorradas', val: '34h', delta: 'IA activa', coral: false },
] as const

const BAR_HEIGHTS = [38, 52, 44, 66, 58, 82, 72, 96] as const

function HeroSection() {
  return (
    <section className="hero">
      <div className="hero-inner">
        <div>
          <div className="hero-badge">
            <span className="hero-badge-dot" />
            <span className="hero-badge-text label-sm">
              Motor de edición automática v3.4
            </span>
          </div>

          <h1 className="hero-title headline-xl">
            Un centro de mando para tus redes, campañas y video
          </h1>
          <span className="hero-tick" aria-hidden="true" />

          <p className="hero-subtitle body-lg">
            Publica en cada red, gestiona campañas de pago y conviértete
            grabaciones en piezas listas para TikTok, Instagram y YouTube
            desde una sola consola operativa.
          </p>

          <div className="hero-ctas">
            <Link className="btn-primary-action headline-sm" to="/auth?mode=register">
              Probar gratis 14 días
            </Link>
            <a className="btn-secondary-white headline-sm" href="#funcionalidades">
              Ver funcionalidades
            </a>
          </div>

          <div className="hero-perks">
            {PERKS.map((perk) => (
              <span key={perk.icon} className="hero-perk label-sm">
                <span className="material-symbols-outlined" aria-hidden="true">
                  {perk.icon}
                </span>
                {perk.label}
              </span>
            ))}
          </div>
        </div>

        <div className="hero-cockpit" aria-hidden="true">
          <div className="hero-cockpit-bar">
            <span className="hero-cockpit-dot" />
            <span className="hero-cockpit-dot" />
            <span className="hero-cockpit-dot" />
            <span className="code-sm">panel.nexusmanager.app</span>
          </div>

          <div className="hero-cockpit-body">
            <div className="cockpit-mini-nav">
              <span className="cx is-on">Panel ejecutivo</span>
              <span className="cx">Editor IA</span>
              <span className="cx">Campañas</span>
              <span className="cx">Calendario</span>
            </div>

            <div className="cockpit-mini-kpis">
              {KPIS.map((kpi) => (
                <div className="cockpit-mini-kpi" key={kpi.lbl}>
                  <span className="lbl">{kpi.lbl}</span>
                  <span className={`val${kpi.coral ? ' coral' : ''}`}>
                    {kpi.val}
                    <small>{kpi.delta}</small>
                  </span>
                </div>
              ))}
            </div>

            <div className="cockpit-mini-chart">
              <div className="cockpit-bars">
                {BAR_HEIGHTS.map((h, i) => (
                  <span
                    key={i}
                    className={i === 5 ? 'coral' : i >= 6 ? 'hi' : undefined}
                    style={{ height: `${h}%` }}
                  />
                ))}
              </div>
              <div className="cockpit-mini-legend">
                <span>Semana 1</span>
                <span>Semana 2</span>
                <span>Semana 3</span>
                <span>Semana 4</span>
              </div>
            </div>
          </div>

          <div className="hero-roas-card">
            <span className="hero-roas-icon material-symbols-outlined" aria-hidden="true">
              trending_up
            </span>
            <div>
              <div className="hero-roas-title label-sm">ROAS promedio +4.2x</div>
              <div className="hero-roas-sub code-sm">Campaña multicanal activa</div>
            </div>
          </div>
        </div>
      </div>

      <div className="hero-platforms">
        <span className="hero-platforms-label label-sm">
          Compatible con tus canales
        </span>
        <div className="hero-platforms-list">
          {PLATFORMS.map((platform) => (
            <div key={platform.label} className="hero-platform">
              <span className="material-symbols-outlined" aria-hidden="true">
                {platform.icon}
              </span>
              <span className="label-md">{platform.label}</span>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

export default HeroSection