import { Link } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'
import './dashboard.css'

const NAV_ITEMS: { icon: string; label: string; active?: boolean }[] = [
  { icon: 'analytics', label: 'Panel ejecutivo', active: true },
  { icon: 'movie_edit', label: 'Editor IA' },
  { icon: 'campaign', label: 'Campañas' },
  { icon: 'bar_chart', label: 'Métricas' },
  { icon: 'calendar_month', label: 'Calendario' },
  { icon: 'video_library', label: 'Biblioteca de medios' },
] as const

interface KpiProps {
  label: string
  icon: string
  value: string
  chip?: string
  chipTone?: 'up' | 'brand' | 'neutral'
  note: string
}

const KPIS: KpiProps[] = [
  {
    label: 'Alcance total unificado',
    icon: 'hub',
    value: '482.910',
    chip: '+18.4%',
    chipTone: 'up',
    note: 'comparado con mes previo',
  },
  {
    label: 'Tasa de interacción promedio',
    icon: 'thumb_up',
    value: '5.7%',
    chip: '+1.2%',
    chipTone: 'up',
    note: 'compromiso activo orgánico',
  },
  {
    label: 'Conversión atribuida a anuncios',
    icon: 'payments',
    value: '€4.820',
    chip: 'ROAS 4.2x',
    chipTone: 'neutral',
    note: 'eficiencia publicitaria neta',
  },
  {
    label: 'Tiempo de producción ahorrado',
    icon: 'schedule',
    value: '34 horas',
    chip: 'Agente IA',
    chipTone: 'brand',
    note: 'cómputo directo este mes',
  },
]

const CHANNELS = [
  { color: '#E1306C', name: 'Instagram', delta: '+2.840 seguidores', tone: 'up', time: 'Hora óptima: 19:30 - 21:00', roas: 'ROAS 4.8x' },
  { color: '#511877', name: 'TikTok', delta: '+6.190 seguidores', tone: 'up', time: 'Hora óptima: 13:00 - 15:30', roas: 'ROAS 3.9x' },
  { color: '#0A66C2', name: 'LinkedIn (B2B Granel)', delta: '+410 contactos', tone: 'up', time: 'Hora óptima: 08:30 - 10:00', roas: 'ROAS 5.1x' },
  { color: '#1877F2', name: 'Facebook & Marketplace', delta: '+195 seguidores', tone: 'flat', time: 'Hora óptima: 20:00 - 22:00', roas: 'ROAS 2.7x' },
]

const QUEUE = [
  { icon: 'photo_camera', channel: 'Instagram', title: 'Reel promocional Cafetería San Telmo', when: 'Hoy · 19:30', status: 'sched', statusLabel: 'Programada' },
  { icon: 'play_circle', channel: 'TikTok', title: 'Clip OTO Campaña Q4', when: 'Hoy · 13:00', status: 'sched', statusLabel: 'Programada' },
  { icon: 'business_center', channel: 'LinkedIn', title: 'Informe B2B semanal de crecimiento', when: 'Mañana · 08:30', status: 'sched', statusLabel: 'Programada' },
  { icon: 'photo_camera', channel: 'Instagram', title: 'Story cafetería — detalle del blend', when: 'Mañana · 10:00', status: 'sched', statusLabel: 'Programada' },
  { icon: 'videocam', channel: 'YouTube', title: 'Tutorial editor IA · capítulo 3', when: 'Mié · 12:00', status: 'proc', statusLabel: 'Procesando' },
]

const WORKFLOWS = [
  { title: 'Reel → TikTok 9:16', meta: '2 clips · 45 seg', progress: 72, status: 'proc', statusLabel: 'IA generando' },
  { title: 'Subtitulado automático español', meta: 'Video largo · 8 min', progress: 100, status: 'live', statusLabel: 'Listo para revisión' },
  { title: 'Pack campaña Q4 — 3 formatos', meta: '16:9 + 1:1 + 9:16', progress: 34, status: 'proc', statusLabel: 'Renderizando' },
]

function KpiCard({ label, icon, value, chip, chipTone, note }: KpiProps) {
  return (
    <article className="kpi-card">
      <div className="kpi-head">
        <span className="kpi-label label-md">{label}</span>
        <span className="material-symbols-outlined kpi-icon" aria-hidden="true">
          {icon}
        </span>
      </div>
      <div className="kpi-value tnum headline-lg">{value}</div>
      <div className="kpi-foot">
        {chip && (
          <span className={`status-chip tone-${chipTone ?? 'neutral'}`}>
            {chipTone === 'up' && (
              <span className="material-symbols-outlined chip-arrow" aria-hidden="true">
                arrow_upward
              </span>
            )}
            {chip}
          </span>
        )}
        <span className="kpi-note body-sm">{note}</span>
      </div>
    </article>
  )
}

function Dashboard() {
  const { user, logout } = useAuth()

  return (
    <div className="cockpit">
      <aside className="cockpit-side">
        <div className="side-head">
          <span className="material-symbols-outlined side-ws-icon" aria-hidden="true">
            storefront
          </span>
          <span className="side-ws-name label-md">Cafetería & Tueste San Telmo</span>
          <span className="material-symbols-outlined side-chevron" aria-hidden="true">
            expand_more
          </span>
        </div>

        <nav className="side-nav">
          {NAV_ITEMS.map((item) => (
            <a
              key={item.label}
              href="#"
              className={`side-link label-md${item.active ? ' is-active' : ''}`}
              aria-current={item.active ? 'page' : undefined}
            >
              <span className="material-symbols-outlined side-link-icon" aria-hidden="true">
                {item.icon}
              </span>
              {item.label}
            </a>
          ))}
        </nav>

        <div className="side-foot">
          <div className="side-status">
            <span className="footer-status-dot" />
            <span className="label-sm">Sistemas operativos</span>
          </div>
          <div className="side-user">
            <Link
              className="side-user-link"
              to="/settings"
              title="Abrir los ajustes de cuenta"
            >
              <span className="side-user-info">
                <span className="side-user-name label-sm">{user?.full_name}</span>
                <span className="side-user-mail body-sm">{user?.email}</span>
              </span>
            </Link>
            <button
              type="button"
              className="side-logout"
              aria-label="Cerrar sesión"
              onClick={() => void logout()}
            >
              <span className="material-symbols-outlined" aria-hidden="true">
                logout
              </span>
            </button>
          </div>
        </div>
      </aside>

      <main className="cockpit-main">
        <header className="exec-bar">
          <div>
            <h1 className="headline-lg">Panel de control principal</h1>
            <p className="body-sm exec-sub">
              Supervisión operativa multicanal, rendimiento publicitario y motor de renderizado neuronal
            </p>
          </div>
          <div className="exec-controls">
            <div className="exec-select">
              <span className="material-symbols-outlined" aria-hidden="true">calendar_today</span>
              <select className="label-md" aria-label="Rango de fechas">
                <option>Últimos 30 días</option>
                <option>Últimos 7 días</option>
                <option>Trimestre actual</option>
                <option>Año fiscal en curso</option>
              </select>
            </div>
            <button type="button" className="btn-secondary label-md">
              Programar publicación
            </button>
            <button type="button" className="btn-coral label-md">
              <span className="material-symbols-outlined" aria-hidden="true">auto_videocam</span>
              Nuevo video con IA
            </button>
          </div>
        </header>

        <div className="kpi-grid">
          {KPIS.map((kpi) => (
            <KpiCard key={kpi.label} {...kpi} />
          ))}
        </div>

        <div className="cockpit-grid">
          <section className="panel panel-chart">
            <div className="panel-head">
              <div>
                <h2 className="headline-sm">Evolución de alcance y engagement semanal</h2>
                <p className="body-sm panel-sub">
                  Consolida impresiones brutas e interacciones verificadas por red
                </p>
              </div>
              <div className="chart-legend">
                <span className="legend-item"><i className="lg-instagram" />Instagram</span>
                <span className="legend-item"><i className="lg-tiktok" />TikTok</span>
                <span className="legend-item"><i className="lg-linkedin" />LinkedIn</span>
                <span className="legend-item"><i className="lg-facebook" />Facebook</span>
              </div>
            </div>

            <div className="chart-box">
              <svg className="chart-svg" viewBox="0 0 760 260" preserveAspectRatio="none" aria-hidden="true">
                <line className="chart-guide" x1="0" y1="215" x2="760" y2="215" />
                <line className="chart-guide" x1="0" y1="155" x2="760" y2="155" />
                <line className="chart-guide" x1="0" y1="95" x2="760" y2="95" />
                <line className="chart-guide" x1="0" y1="35" x2="760" y2="35" />
                <text className="chart-axis" x="6" y="39">150k</text>
                <text className="chart-axis" x="6" y="99">100k</text>
                <text className="chart-axis" x="6" y="159">50k</text>
                <text className="chart-axis" x="6" y="219">0</text>
                <polygon className="chart-area-tiktok" points="40,180 160,140 280,110 400,90 520,60 640,45 740,30 740,220 40,220" />
                <polyline className="chart-line-tiktok" points="40,180 160,140 280,110 400,90 520,60 640,45 740,30" />
                <polygon className="chart-area-instagram" points="40,160 160,120 280,135 400,80 520,95 640,65 740,45 740,220 40,220" />
                <polyline className="chart-line-instagram" points="40,160 160,120 280,135 400,80 520,95 640,65 740,45" />
                <polyline className="chart-line-linkedin" points="40,205 160,190 280,175 400,160 520,150 640,135 740,120" />
                <polyline className="chart-line-facebook" points="40,195 160,185 280,180 400,175 520,168 640,160 740,155" />
                <circle className="chart-point" cx="520" cy="60" r="4" />
                <circle className="chart-point" cx="740" cy="30" r="4" />
              </svg>
              <div className="chart-x code-sm">
                <span>Semana 1</span>
                <span>Semana 2</span>
                <span>Semana 3</span>
                <span>Semana 4</span>
                <span>Cierre mensual</span>
              </div>
            </div>

            <div className="quick-grid">
              <div className="quick-cell">
                <span className="label-sm">Top formato</span>
                <strong className="headline-sm tnum">Short vertical</strong>
                <span className="body-sm">72% del tráfico total</span>
              </div>
              <div className="quick-cell">
                <span className="label-sm">Retención 3s</span>
                <strong className="headline-sm tnum">68.4%</strong>
                <span className="body-sm">Optimizado por IA</span>
              </div>
              <div className="quick-cell">
                <span className="label-sm">Clics a carrito</span>
                <strong className="headline-sm tnum">1.940</strong>
                <span className="body-sm">Conversión 3.4%</span>
              </div>
              <div className="quick-cell">
                <span className="label-sm">Coste por adquisición</span>
                <strong className="headline-sm tnum">€2.48</strong>
                <span className="body-sm">-14% vs meta</span>
              </div>
            </div>
          </section>

          <section className="panel">
            <div className="panel-head">
              <div>
                <h2 className="headline-sm">Eficiencia de canales</h2>
                <p className="body-sm panel-sub">Crecimiento y ventanas óptimas por red</p>
              </div>
            </div>

            <div className="channel-list">
              {CHANNELS.map((channel) => (
                <div className="channel-row" key={channel.name}>
                  <div className="channel-top">
                    <span className="label-md channel-name">
                      <i style={{ background: channel.color }} />
                      {channel.name}
                    </span>
                    <span className="label-sm channel-delta">{channel.delta}</span>
                  </div>
                  <div className="channel-bottom body-sm">
                    <span>{channel.time}</span>
                    <span className="label-sm tnum">{channel.roas}</span>
                  </div>
                </div>
              ))}
            </div>

            <button type="button" className="panel-action label-md">
              Configurar reglas de publicación inteligente
            </button>
          </section>
        </div>

        <div className="cockpit-grid">
          <section className="panel">
            <div className="panel-head">
              <div>
                <h2 className="headline-sm">Próximas publicaciones</h2>
                <p className="body-sm panel-sub">Contenido programado en el calendario multicanal</p>
              </div>
              <Link className="panel-link label-md" to="/">
                Ver calendario
              </Link>
            </div>

            <div className="queue-list">
              {QUEUE.map((item) => (
                <div className="queue-row" key={item.title}>
                  <span className={`queue-icon ch-${item.channel.toLowerCase()}`} aria-hidden="true">
                    <span className="material-symbols-outlined">{item.icon}</span>
                  </span>
                  <div className="queue-body">
                    <span className="queue-title body-md">{item.title}</span>
                    <span className="queue-when body-sm">{item.when}</span>
                  </div>
                  <span className={`status-chip tone-${item.status}`}>{item.statusLabel}</span>
                </div>
              ))}
            </div>
          </section>

          <section className="panel">
            <div className="panel-head">
              <div>
                <h2 className="headline-sm">Flujos de video IA</h2>
                <p className="body-sm panel-sub">Trabajos de renderizado del motor automático</p>
              </div>
              <Link className="panel-link label-md" to="/">
                Nuevo flujo
              </Link>
            </div>

            <div className="workflow-list">
              {WORKFLOWS.map((wf) => (
                <div className="workflow-row" key={wf.title}>
                  <div className="workflow-top">
                    <span className="workflow-title body-md">{wf.title}</span>
                    <span className={`status-chip tone-${wf.status}`}>{wf.statusLabel}</span>
                  </div>
                  <span className="workflow-meta body-sm">{wf.meta}</span>
                  <div className="progress">
                    <div className={`progress-fill${wf.status === 'live' ? ' is-live' : ''}`} style={{ width: `${wf.progress}%` }} />
                  </div>
                  <span className="workflow-pct code-sm tnum">{wf.progress}%</span>
                </div>
              ))}
            </div>
          </section>
        </div>
      </main>
    </div>
  )
}

export default Dashboard