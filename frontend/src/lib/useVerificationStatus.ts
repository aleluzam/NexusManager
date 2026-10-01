import { useCallback, useEffect, useState } from 'react'
import { ApiError, fetchVerificationStatus, type VerificationStatus } from './api'

/**
 * Estado de la verificación del correo del usuario de la sesión.
 *
 * Vive en un hook propio y no en `AuthContext` por tres razones:
 * 1. `AuthContext` modela la IDENTIDAD (`user.is_email_verified` es lo que
 *    decide si la barra se monta); esto es el estado del CÓDIGO, que solo el
 *    servidor puede medir y que caduca por su cuenta.
 * 2. La barra es hoy la única consumidora. Meterlo en el contexto ampliaría el
 *    valor para Navbar, Dashboard y el panel de ajustes sin que ninguno lo use.
 * 3. La cuenta atrás se actualiza cada segundo. Si viviera en el contexto,
 *    ese tick re-renderizaría el árbol entero en cada segundo en lugar de a la
 *    barra.
 *
 * Único hoy: cuando aparezca un segundo consumidor (un aviso en el cockpit, un
 * chip con el tiempo restante) esto se sube a un provider; mientras tanto, un
 * hook es la opción que menos superficie toca.
 */

/**
 * Escalera de reintentos para la CARRERA DEL REGISTRO.
 *
 * `register` emite el código en una `BackgroundTask`, es decir DESPUÉS de
 * responder: un `/verification-status` inmediato puede devolver
 * `pending=false, codes_used_last_hour=0` y 1,5 s después
 * `pending=true, codes_used_last_hour=1` (medido contra uvicorn real). Un
 * fetch único dejaría la barra con un estado obsoleto —sin cuenta atrás y sin
 * contador— durante toda la vida de la página.
 *
 * Los reintentos se esquivan solo mientras `pending === false` Y la cuenta
 * sigue sin verificar, y paran en cuanto aparece el código: no es un polling
 * continuo, es una ventana de cortesía acotada (~9,6 s) que solo se abre en el
 * registro, porque un `pending === false` con cuenta sin verificar no significa
 * nada más (o hay un código sin consumir, o no lo hay todavía).
 *
 * Por qué esto NO reintroduce el oráculo de tiempo que el backend evitó al
 * mover el envío a una `BackgroundTask`: aquel oráculo era la DURACIÓN del
 * envío de correo medida desde fuera (un atacante comparaba la latencia de
 * `forgot-password` con y sin cuenta existente, 154 ms frente a 2,9 ms, y
 * acertaba el 100 %). Aquí no se mide ninguna duración de envío: se pregunta
 * por un recurso de la cuenta del propio solicitante, y encima el endpoint es
 * de solo lectura —no emite, no consume códigos y consulta el cubo del reenvío
 * con `retry_after()`, que NO consume plaza—, así que repetirlo no le cuesta
 * nada al usuario ni adelanta ni retrasa la disponibilidad de un reenvío. No
 * se está midiendo un tiempo de servidor: se está leyendo un estado.
 */
const REGISTRATION_RACE_DELAYS_MS = [600, 1200, 1800, 2500, 3500]

export interface VerificationStatusView {
  /** Última respuesta del servidor, o `null` mientras no haya llegado ninguna. */
  status: VerificationStatus | null
  /**
   * Segundos que le quedan al código. `null` mientras no se sabe (primera
   * petición en vuelo, o carrera del registro sin código aún emitido); `0`
   * cuando el código existe pero ha caducado.
   */
  remaining: number | null
  /**
   * Segundos que el SERVIDOR dice que faltan para poder pedir otro código:
   * el cubo de `POST /resend-verification` (3 por hora), que es el límite de
   * verdad. `null` cuando no hay nada pendiente en el cubo o aún no se ha
   * leído el estado. Es distinto del cooldown local de 60 s del componente: ese
   * solo evita el clic repetido antes de que la red conteste, este es el que
   * hace el servidor cumplir. Los dos se cuentan y manda el mayor.
   */
  resendRemaining: number | null
  /** Fuerza una reconsulta (tras un reenvío, por ejemplo). */
  refresh: () => void
  /**
   * Fija el enfriamiento del cubo a partir de un 429 (`retry_after_seconds`).
   * El 429 es la única respuesta que trae el tiempo restante actualizado, así
   * que sin esto el botón volvería a quedar disponible con la cuenta atrás
   * local ya agotada y el usuario se llevaría otro 429 al instante.
   */
  blockResend: (seconds: number) => void
}

export function useVerificationStatus(
  userId: string | null,
  /** La cuenta ya está verificada: se deja de tolerar la carrera del registro. */
  isVerified: boolean,
): VerificationStatusView {
  /* Todo lo que llega del servidor va en UN estado porque siempre llega
     emparejado: la respuesta, el plazo del código que se deriva de ella y el
     enfriamiento del cubo de reenvío. `userId` viaja dentro para poder detectar
     abajo que el estado guardado es de otra cuenta.
     Los dos plazos son instantes absolutos en epoch-ms, fijados en el momento
     en que LLEGA la respuesta (no en cada render): las cuentas atrás se
     derivan de `deadline - Date.now()`. Restar de un contador con un
     `setInterval` haría que la deriva se acumulara con cada tick perdido —una
     pestaña en segundo plano con los timers recortados durante 60 s dejaría el
     reloj 60 s atrasado—, mientras que con un plazo absoluto el valor se
     recalcula solo en cuanto el navegador vuelve a pintar. */
  const [server, setServer] = useState<{
    userId: string | null
    status: VerificationStatus | null
    deadline: number | null
    resendDeadline: number | null
  }>({ userId, status: null, deadline: null, resendDeadline: null })
  /* Reloj de pared para las cuentas atrás; solo lo mueve el intervalo de abajo. */
  const [now, setNow] = useState(() => Date.now())
  /* Contador de reconsultas explícitas: subirlo relanza el efecto de abajo con
     su limpieza, en vez de duplicar la lógica de fetch en dos sitios. */
  const [nonce, setNonce] = useState(0)

  /* Si el estado guardado es de OTRO usuario se descarta en el acto, durante el
     render y no en un efecto: la barra es `position: fixed` y el componente NO
     se desmonta al cerrar sesión (sigue montado desde Layout, solo devuelve
     null sin usuario), así que sin esto el nuevo usuario vería, durante el
     hueco hasta que responde la petición, el plazo y el contador del anterior.
     React documenta este ajuste —"ajustar el estado cuando cambia una prop"— y
     re-renderiza en el acto sin llegar a pintar el estado viejo, que es
     justamente lo que se quiere. Compara solo `userId` a propósito: al
     verificar hay que CONSERVAR el estado hasta que llegue la relectura. */
  if (server.userId !== userId) {
    setServer({ userId, status: null, deadline: null, resendDeadline: null })
  }
  const { status, deadline, resendDeadline } = server

  const refresh = useCallback(() => setNonce((n) => n + 1), [])

  const blockResend = useCallback((seconds: number) => {
    if (!(seconds > 0)) return
    setServer((prev) => ({ ...prev, resendDeadline: Date.now() + seconds * 1000 }))
    setNow(Date.now())
  }, [])

  useEffect(() => {
    /* Sin sesión no hay estado que leer: el endpoint pide bearer. */
    if (userId === null) return

    let cancelled = false
    let timer: number | undefined
    let attempt = 0

    const read = async () => {
      /* `unresolved` es lo que decide si toca reintentar. Se inicializa a
         `true` —"no sabemos si hay código"—, que es justo el estado en el que
         un fallo de red o un 5xx dejan la barra. El `finally` la lee, así que
         programar la escalera ahí cubre también el camino de error: si se
         dejara dentro del `try`, el primer fallo saltaría la escalera entera y
         la barra se quedaría en "--:--" sin contador hasta que el usuario
         recargara o reenviara. */
      let unresolved = true
      try {
        const next = await fetchVerificationStatus()
        if (cancelled) return
        unresolved = !next.pending
        /* Sin código pendiente no hay plazo: se deja en `null` y la cuenta
           atrás se apaga en vez de mostrar un "00:00" que no significa nada.
           Un código caducado (pending con 0 segundos) sí tiene plazo, ya
           vencido: por eso la condición mira `pending` y no el tiempo. */
        setServer({
          userId,
          status: next,
          deadline: next.pending ? Date.now() + next.expires_in_seconds * 1000 : null,
          /* El enfriamiento del cubo de reenvío llega como segundos que
             faltan, y se convierte en plazo por la misma razón que el código:
             para que la cuenta atrás se recalcule sola. Con 0 no hay plazo. */
          resendDeadline:
            next.resend_available_in_seconds > 0
              ? Date.now() + next.resend_available_in_seconds * 1000
              : null,
        })
        setNow(Date.now())
      } catch (err) {
        /* Lectura fallida: no se confirma un estado a medias ni se lanza nada a
           la UI. La barra sigue mostrando lo que ya tenía, que es lo que se
           debe ver, y el `finally` programa el siguiente intento.
           Salvo en 401/403: ahí no es un fallo transitorio sino que la sesión
           no existe, y repetir cinco veces solo gastaría cinco refresh
           silenciosos con un token que no va a volver. */
        if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
          unresolved = false
        }
      } finally {
        if (!cancelled && unresolved && !isVerified && attempt < REGISTRATION_RACE_DELAYS_MS.length) {
          const delay = REGISTRATION_RACE_DELAYS_MS[attempt]
          attempt += 1
          timer = window.setTimeout(() => void read(), delay)
        }
      }
    }

    void read()
    return () => {
      cancelled = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [userId, isVerified, nonce])

  /* Un solo intervalo para las dos cuentas atrás, y solo mientras quede algo
     que contar: se crea en cuanto hay un plazo vivo y se desmonta solo cuando
     ambos han terminado. React Compiler memoiza el render pero no limpia
     timers, así que el `clearInterval` del cleanup es lo que evita un intervalo
     huérfano por montaje. */
  useEffect(() => {
    const vivo = (d: number | null) => d !== null && Date.now() < d
    if (!vivo(deadline) && !vivo(resendDeadline)) return
    const timer = window.setInterval(() => {
      const at = Date.now()
      setNow(at)
      if (!vivo(deadline) && !vivo(resendDeadline)) window.clearInterval(timer)
    }, 1000)
    return () => window.clearInterval(timer)
  }, [deadline, resendDeadline])

  const restantes = (d: number | null) =>
    d === null ? null : Math.max(0, Math.ceil((d - now) / 1000))
  const remaining = restantes(deadline)
  const resendRemaining = restantes(resendDeadline)

  return { status, remaining, resendRemaining, refresh, blockResend }
}
