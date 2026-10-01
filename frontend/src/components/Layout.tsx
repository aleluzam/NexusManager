import { Outlet } from 'react-router-dom'
import Navbar from './Navbar'
import VerifyEmailBar from './auth/VerifyEmailBar'

/**
 * Layout raíz de la aplicación: la Navbar es un componente GLOBAL y
 * persistente presente en TODAS las páginas (landing, /auth login/registro,
 * /dashboard y cualquier ruta futura). El contenido de cada ruta se renderiza
 * a través de <Outlet />.
 *
 * La barra de verificación del correo cuelga de aquí y no de cada página: se
 * dibuja bajo la navbar en todas las rutas y el propio componente se apaga
 * (no renderiza nada) cuando no hay sesión o el correo ya está verificado, de
 * modo que en la landing y en /auth no aparece nada.
 *
 * El <main> lleva tabIndex -1 para que sea un destino de foco válido: al
 * verificar el correo la barra desaparece y VerifyEmailBar.tsx devuelve ahí el
 * foco, que si no caería a <body>. No entra en el orden de tabulación.
 */
export default function Layout() {
  return (
    <>
      <Navbar />
      <VerifyEmailBar />
      <main className="app-main" tabIndex={-1}>
        <Outlet />
      </main>
    </>
  )
}
