import { Outlet } from 'react-router-dom'
import Navbar from './Navbar'

/**
 * Layout raíz de la aplicación: la Navbar es un componente GLOBAL y
 * persistente presente en TODAS las páginas (landing, /auth login/registro,
 * /dashboard y cualquier ruta futura). El contenido de cada ruta se renderiza
 * a través de <Outlet />.
 */
export default function Layout() {
  return (
    <>
      <Navbar />
      <main className="app-main">
        <Outlet />
      </main>
    </>
  )
}
