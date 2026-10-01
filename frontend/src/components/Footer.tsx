function Footer() {
  return (
    <footer className="footer">
      <div className="footer-inner">
        <div className="footer-top">
          <div className="footer-brand">
            <img
              src="/logo.svg"
              alt="Logotipo de NexusManager"
              className="navbar-logo"
            />
            <span className="footer-wordmark">
              Nexus<span className="brand-accent">Manager</span>
            </span>
          </div>
          <div className="footer-status">
            <span className="footer-status-dot" />
            <span className="footer-status-text label-sm">
              Sistemas operando normalmente
            </span>
          </div>
        </div>

        <div className="footer-bottom">
          <p className="footer-copy body-sm">
            © 2024 NexusManager Enterprise Systems. Todos los derechos
            reservados.
          </p>
          <div className="footer-links">
            <a
              className="footer-link body-sm"
              href="#"
            >
              Términos de servicio
            </a>
            <a
              className="footer-link body-sm"
              href="#"
            >
              Privacidad y cumplimiento
            </a>
            <a
              className="footer-link body-sm"
              href="#"
            >
              Seguridad operacional
            </a>
          </div>
        </div>
      </div>
    </footer>
  )
}

export default Footer