import { Link } from 'react-router-dom'

const STATS = [
  { k: 'Alcance total unificado', v: '482.910', accent: false, d: '+18.4% vs mes previo' },
  { k: 'Tasa de interacción', v: '5.7%', accent: false, d: '+1.2% compromiso orgánico' },
  { k: 'Conversión atribuida a anuncios', v: '€4.820', accent: true, d: 'ROAS 4.2x neto' },
  { k: 'Producción ahorrada', v: '34 h', accent: false, d: 'Cómputo del agente IA' },
] as const

const FEATURES = [
  {
    icon: 'hub',
    coral: false,
    title: 'Publicación multicanal',
    text: 'Un solo panel para componer, programar y enviar contenido a cada plataforma desde un calendario unificado.',
    meta: 'Programación inteligente',
  },
  {
    icon: 'campaign',
    coral: true,
    title: 'Campañas de pago',
    text: 'Atribución de conversión, ROAS por canal y reglas de puja para mantener el gasto alineado con objetivos.',
    meta: 'Optimización en vivo',
  },
  {
    icon: 'auto_videocam',
    coral: false,
    title: 'Flujos de video IA',
    text: 'De grabación bruta a pieza optimizada: subtítulos, cortes y formatos verticales generados automáticamente.',
    meta: 'Motor v3.4',
  },
] as const

function Features() {
  return (
    <>
      <section className="landing-band">
        <div className="landing-band-inner">
          {STATS.map((stat) => (
            <div className="landing-stat" key={stat.k}>
              <div className="k">{stat.k}</div>
              <div className={`v${stat.accent ? ' accent' : ''}`}>{stat.v}</div>
              <div className="d">{stat.d}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="features" id="funcionalidades">
        <div className="features-head">
          <div className="label-md">Operación en un solo plano</div>
          <h2 className="headline-lg">
            Densidad de datos de un cockpit, simplicidad de una herramienta para tu negocio
          </h2>
          <p className="body-lg">
            Cada vista está construida para lectura inmediata: métricas
            tabulares, estados operativos claros y acciones directas.
          </p>
        </div>

        <div className="features-grid">
          {FEATURES.map((feature) => (
            <article className="feature-card" key={feature.title}>
              <span
                className={`feature-icon${feature.coral ? ' coral' : ''}`}
                aria-hidden="true"
              >
                <span className="material-symbols-outlined">{feature.icon}</span>
              </span>
              <h3 className="headline-sm">{feature.title}</h3>
              <p className="body-md">{feature.text}</p>
              <div className="feature-meta label-sm">
                <span className="status-chip">{feature.meta}</span>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="cta-band">
        <div className="cta-panel">
          <h2 className="headline-lg">
            Pon tu operación social en un solo panel
          </h2>
          <p className="body-lg">
            Crea tu cuenta en menos de un minuto. Sin tarjeta de crédito,
            con todas las integraciones activas.
          </p>
          <div className="hero-ctas">
            <Link className="btn-primary-action headline-sm" to="/auth?mode=register">
              Comenzar gratis
            </Link>
            <a className="btn-secondary-white headline-sm" href="/auth">
              Iniciar sesión
            </a>
          </div>
        </div>
      </section>
    </>
  )
}

export default Features