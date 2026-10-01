import { useEffect, useRef, useState } from 'react'
import { useAuth } from '../../auth/AuthContext'
import { ApiError } from '../../lib/api'
import { useResendCooldown } from '../../lib/useResendCooldown'
import { useVerificationStatus } from '../../lib/useVerificationStatus'
import OtpInput from './OtpInput'
import './verify-bar.css'

const OTP_LENGTH = 6
const RESEND_COOLDOWN = 60

/* Margen por debajo del cual la cuenta atrás pasa a color de aviso: dos
   minutos es lo que tarda de media una persona en abrir el correo, leer el
   mensaje y pegar el código. */
const EXPIRY_WARNING_SECONDS = 120

/* Texto reservado antes de que llegue la primera respuesta (y durante la
   carrera del registro, mientras el código aún no existe en el servidor). Son
   guiones, no un "00:00": un 00:00 afirma que hay un código caducado. */
const COUNTDOWN_PLACEHOLDER = '--:--'

/* Landmark de la ruta (el <main className="app-main"> de Layout.tsx) al que
   vuelve el foco cuando la barra se desmonta. Se selecciona por clase y no
   por etiqueta porque cada página trae además su propio <main> (cockpit-main,
   landing-main) y el del Layout es el primero del DOM. */
const ROUTE_LANDMARK = '.app-main'

/* Enmascara el correo para mostrarlo en una barra compartida en pantalla:
   r***@empresa.com. Si el formato no es reconocido se devuelve tal cual. */
function maskEmail(email: string): string {
  const at = email.indexOf('@')
  if (at < 1) return email
  return `${email.slice(0, 1)}***${email.slice(at)}`
}

/* Segundos → "mm:ss". Se rellena con ceros a la izquierda para que el ancho
   en caracteres sea SIEMPRE 5 (ver .verify-bar-expiry-value): el TTL del
   código son 10 minutos, así que los minutos nunca pasan de dos dígitos y el
   formato no cambia de longitud durante toda la cuenta atrás. */
function formatCountdown(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  const minutes = Math.floor(total / 60)
  return `${String(minutes).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}

/* Segundos → texto legible en español, para decir el tiempo REAL que queda tras
   un 429 en vez de repetir un texto genérico. Es el mismo redondeo que
   `humanize_wait` en backend/app/api/rate_limit.py: hacia ARRIBA, porque
   `retry_after_seconds` es un mínimo ("no antes de") y decir "en 1 minuto"
   cuando faltan 119 segundos hace que el usuario reintente y reciba otro 429. */
function humanizeWait(seconds: number): string {
  if (seconds <= 0) return 'un momento'
  if (seconds < 60) return seconds === 1 ? '1 segundo' : `${seconds} segundos`

  if (seconds < 3600) {
    const minutes = Math.ceil(seconds / 60)
    return minutes === 1 ? '1 minuto' : `${minutes} minutos`
  }

  const hours = Math.floor(seconds / 3600)
  const rest = seconds % 3600
  const partes = [hours === 1 ? '1 hora' : `${hours} horas`]
  if (rest > 0) {
    const minutes = Math.ceil(rest / 60)
    partes.push(minutes === 1 ? '1 minuto' : `${minutes} minutos`)
  }
  return partes.join(' ')
}

/* Lo mismo que humanizeWait pero para DENTRO del botón, donde el espacio es
  tight y la unidad hace falta siempre: "45s", "17 min", "1 h 5 min". Sin esto
   el botón que espera al cubo del servidor se leería "Reenviar en 1 hora 5
   minutos", que no cabe en el ancho reservado. */
function compactWait(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.ceil(seconds / 60)} min`
  const hours = Math.floor(seconds / 3600)
  const rest = Math.ceil((seconds % 3600) / 60)
  return rest > 0 ? `${hours} h ${rest} min` : `${hours} h`
}

/* Mensaje de un reenvío fallido. El 429 del limitador trae el tiempo real en
   `retry_after_seconds` y se dice con ese número; cualquier otro error (503 del
   proveedor, tope de códigos por hora —un 429 que NO trae el campo—, red)
   conserva el `detail` del servidor, que es lo que se pintaba antes. */
function resendErrorMessage(err: unknown): string {
  if (!(err instanceof ApiError)) {
    return 'No se pudo enviar el código. Inténtalo de nuevo.'
  }
  const wait = err.retry_after_seconds
  if (err.status === 429 && typeof wait === 'number' && wait > 0) {
    return `Has alcanzado el límite de reenvíos. Podrás pedir otro en ${humanizeWait(wait)}.`
  }
  return err.detail
}

/* Barra global de verificación del correo. Se monta UNA vez desde Layout.tsx
   (bajo la navbar) y aparece en todas las rutas mientras la cuenta siga sin
   verificar. Se desmonta sola: cuando verifyEmail devuelve el User verificado,
   el contexto lo propaga, `pending` pasa a false y este bloque desaparece. No
   se guarda nada en localStorage a propósito, el estado de verificación vive
   en el servidor y se lee de él con useVerificationStatus.

   Al ser `position: fixed` no reserva hueco por sí sola: la altura se publica
   en --verify-bar-h y cada página la suma a su padding-top (ver index.css y
   components/auth/verify-bar.css). Esa altura se mide con un ResizeObserver,
   así que NADA de lo que hay dentro puede cambiar de ancho con el tiempo: dos
   de las tres piezas nuevas (cuenta atrás y contador) reservan su ancho fijo
   por eso, y el comentario de verify-bar.css explica el medida a medida. */
export default function VerifyEmailBar() {
  const { user, verifyEmail, resendVerificationCode } = useAuth()
  const [code, setCode] = useState('')
  const [verifying, setVerifying] = useState(false)
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const resend = useResendCooldown(RESEND_COOLDOWN)
  /* Estado del código en el servidor: se lee al montar, al cambiar de usuario,
     tras cada reenvío (`refresh`) y cuando la cuenta pasa a verificada (es una
     de las dependencias del hook, así que el propio `is_email_verified` que
     devuelve `verifyEmail` dispara la relectura: no hace falta pedirla a mano
     ni duplicar aquí la llamada). `resendRemaining` es el enfriamiento del
     CUBO del servidor (3 por hora) y `blockResend` lo actualiza cuando llega
     un 429, que es la única respuesta con el tiempo restante al día. */
  const { status, remaining, resendRemaining, refresh, blockResend } =
    useVerificationStatus(user?.id ?? null, user?.is_email_verified ?? false)
  /* Cerrojo imperativo: dos disparos en el mismo tick (pegado + botón) aún
     leerían `verifying === false` desde el estado aún no aplicado. */
  const busy = useRef(false)
  const barRef = useRef<HTMLElement | null>(null)

  /* La barra solo existe con sesión abierta y cuenta sin verificar: en la
     landing y en /auth no hay usuario, así que no hay nada que mostrar. Este
     predicado es el único: lo usan tanto el efecto como el guard de render,
     y usa el mismo criterio truthy que el JSON del backend. */
  const pending = user !== null && !user.is_email_verified

  /* Todo lo que hay en la barra es de UNA cuenta: lo que se teclea, el error,
     el aviso y el cooldown del reenvío. El componente no se desmonta al cerrar
     sesión (Layout lo monta siempre y aquí solo se devuelve null), así que sin
     este descarte el usuario B se encontraría en pantalla el "El código es
     inválido o ha expirado" que el usuario A dejó a medias, o el cooldown de
     A todavía corriendo. El id va DENTRO del estado en vez de en un useEffect a
     propósito: ajustar el estado durante el render es el patrón que documenta
     React para "cambió una prop" y evita el parpadeo de un frame con el estado
     del usuario anterior, que es justo lo que hay que evitar. Ojo al orden: va
     ANTES del guard de render de abajo, porque al cerrar sesión el usuario es
     null y el ajuste tiene que ocurrir igualmente. */
  const ownerId = user?.id ?? null
  const [owner, setOwner] = useState(ownerId)
  if (owner !== ownerId) {
    setOwner(ownerId)
    setCode('')
    setError(null)
    setNotice(null)
    resend.reset()
  }

  /* Estado de la cuenta atrás en tres casos, y son tres porque el contrato
     distingue "caducado" de "todavía no emitido":
     - `pending === false` con cuenta sin verificar: la carrera del registro,
       el código aún no está en el servidor. No se afirma nada: hueco reservado
       con guiones hasta que el poll traiga la respuesta buena.
     - `pending === true` y 0 segundos: SÍ hay código y ha caducado sin
       consumirse. Se pide otro.
     - resto: cuenta atrás normal. */
  const codePending = status?.pending === true
  const expired = codePending && remaining === 0
  const waitingForCode = status !== null && !status.pending && !user?.is_email_verified
  const countdown = expired
    ? null
    : remaining === null || waitingForCode
      ? COUNTDOWN_PLACEHOLDER
      : formatCountdown(remaining)
  /* Aviso de caducidad por debajo del margen. El color lo pone la hoja con
     tokens del design system, no un color suelto aquí. */
  const warnExpiry = !expired && remaining !== null && remaining <= EXPIRY_WARNING_SECONDS
  /* Contador de códigos de esta hora. Con la cuenta ya verificada el tope
     viene a 0 y "0 de 5" sería un dato que no existe: no se pinta nada. */
  const codeLimit = status?.codes_limit_per_hour ?? 0
  const codesUsed = status?.codes_used_last_hour ?? 0
  const showCodes = codeLimit > 0

  /* ------ Enfriamientos del botón de reenviar: dos cosas distintas y las dos
     cuentan. `resend.remaining` es el cooldown LOCAL de 60 s, que solo existe
     para que un doble clic no salga disparado antes de que la red conteste.
     `resendRemaining` es el cubo del SERVIDOR (3 por hora) leído de
     `resend_available_in_seconds`: ese es el límite de verdad, y va a seguir
     latiendo aunque el local se haya agotado. Se disables el que MANDA, el
     mayor de los dos, y se explica cuál es: si el cubo del servidor es el que
     bloquea, la etiqueta lo dice, porque un minuto de espera y una hora de
     espera no son el mismo problema para quien está delante. `null` (aún sin
     leer el estado) cuenta como 0: no se bloquea por un dato que no tenemos. */
  const bucketWait = resendRemaining ?? 0
  const localWait = resend.remaining
  const wait = Math.max(localWait, bucketWait)
  const bucketIsTheBlocker = bucketWait > 0 && bucketWait >= localWait
  const resendBlocked = wait > 0

  /* Marca de estado en <body> (útil en inspección y en tests) y, sobre todo,
     ciclo de vida de la publicación de la altura: se quita en el cleanup para
     no dejar el hueco fantasma si la barra se desmonta (por ejemplo, al cerrar
     sesión en otra pestaña). El alto como tal no sale de aquí sino del
     observer de abajo, que escribe --verify-bar-h. */
  useEffect(() => {
    if (!pending) return
    const { body } = document
    body.classList.add('verify-pend')

    const node = barRef.current
    if (!node) {
      return () => body.classList.remove('verify-pend')
    }

    /* El min-height de la hoja (verify-bar.css) solo cubre el caso en fila
       única: un mensaje de error o el apilado en móvil hacen la barra más
       alta. Se publica por eso la medida real en la variable que leen las
       páginas, y así el contenido nunca queda debajo de la barra. Se mide con
       getBoundingClientRect porque devuelve siempre la caja de borde: el
       alto de la barra incluye padding y border-bottom, y contentRect los
       excluiría dejando 21px de contenido bajo la barra. El min-height vive
       en la hoja (no en la variable) para que la medida no se realimente a sí
       misma, y se redondea para no escribir un valor distinto por fracción
       de píxel en cada notificación del observer. */
    const observer = new ResizeObserver(() => {
      const height = Math.round(node.getBoundingClientRect().height)
      if (height > 0) body.style.setProperty('--verify-bar-h', `${height}px`)
    })
    observer.observe(node)

    return () => {
      observer.disconnect()
      body.classList.remove('verify-pend')
      body.style.removeProperty('--verify-bar-h')
    }
  }, [pending])

  /* Al teclear los 6 dígitos el submit actualiza el usuario, la barra se
     desmonta y el <input> que tenía el foco desaparece: React no lo reubica,
     el foco cae a <body> y el siguiente Tab reinicia desde la navbar. Al
     pasar a verificado se devuelve al landmark de la ruta, salvo que el
     usuario ya lo haya llevado a otra parte (Tab hacia la navbar, clic en un
     enlace…), en cuyo caso no se le quita. */
  const wasPending = useRef(false)
  useEffect(() => {
    if (pending) {
      wasPending.current = true
      return
    }
    if (!wasPending.current) return
    wasPending.current = false
    const active = document.activeElement
    const focusOrphan =
      active === null || active === document.body || active === document.documentElement
    if (focusOrphan) document.querySelector<HTMLElement>(ROUTE_LANDMARK)?.focus()
  }, [pending])

  /* Al caducar el código se vacía el input y se avisa UNA vez por caducidad.
     El ref es lo que marca "ya avisado": el efecto se vuelve a disparar en
     cada tick mientras siga caducado, y sin él el `setCode('')` se repetiría
     sin parar. El `busy` es el mismo cerrojo de los envíos: si el usuario ha
     pulsado Verificar justo cuando venció, no se le borra lo que está
     validando. El aviso al lector de pantalla NO va por `notice`: esa banda
     ocupa una fila entera y hace la barra 49px más alta —un salto de página en
     el instante exacto de caducar—, y además repetiría en grande un texto que
     la cuenta atrás ya está diciendo. Va por la región viva oculta de abajo. */
  const wasExpired = useRef(false)
  useEffect(() => {
    if (!expired) {
      wasExpired.current = false
      return
    }
    if (wasExpired.current) return
    wasExpired.current = true
    if (!busy.current) setCode('')
  }, [expired])

  /* `!user` no repite la lógica: está solo para que TypeScript estreche el
     tipo de `user` (un predicado guardado en una variable no estrecha). */
  if (!user || !pending) return null

  const submit = async (value: string) => {
    if (busy.current) return
    if (value.length !== OTP_LENGTH) {
      setError('Introduce los 6 dígitos del código.')
      return
    }
    busy.current = true
    setVerifying(true)
    setError(null)
    setNotice(null)
    try {
      await verifyEmail(value)
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail
          : 'No se pudo verificar el código. Inténtalo de nuevo.',
      )
      setCode('')
    } finally {
      busy.current = false
      setVerifying(false)
    }
  }

  const resendCode = async () => {
    /* Cerrojo por los DOS enfriamientos, no solo por el local: si el cubo del
       servidor dice que todavía no, el clic se traga igual (el botón está
       deshabilitado, pero la guarda evita que un clic programático o el mismo
       tick de un doble toque se cuele). */
    if (busy.current || wait > 0) return
    setSending(true)
    setError(null)
    setNotice(null)
    try {
      await resendVerificationCode()
      setNotice(
        'Si tu correo sigue sin verificar, te enviamos un código nuevo en unos segundos.',
      )
      resend.start()
      setCode('')
      /* El reenvío emite un código nuevo: el contador de esta hora, el plazo de
         caducidad y el enfriamiento del cubo los dice el servidor, no se
         estiman aquí. */
      refresh()
    } catch (err) {
      setError(resendErrorMessage(err))
      /* Un 429 trae el tiempo real que queda en el cubo, y es más fresco que
         el último `verification-status`: se pasa al hook para que el botón
         quede bloqueado ese tiempo exacto. Sin esto, a los 60 s el cooldown
         local lo dejaría pulsar otra vez y el usuario recibiría otro 429. */
      if (err instanceof ApiError && err.status === 429) {
        blockResend(err.retry_after_seconds ?? 0)
      }
    } finally {
      setSending(false)
    }
  }

  return (
    <section className="verify-bar" aria-labelledby="verify-bar-title" ref={barRef}>
      <div className="verify-bar-main">
        <span className="material-symbols-outlined verify-bar-icon" aria-hidden="true">
          mark_email_unread
        </span>
        <div className="verify-bar-info">
          <h2 className="headline-sm" id="verify-bar-title">
            Verifica tu correo para asegurar tu cuenta
          </h2>
          <p className="body-sm verify-bar-sub">
            Te enviamos un código de 6 dígitos a{' '}
            <span className="code-sm verify-bar-mail">{maskEmail(user.email)}</span>.
            Puede tardar unos segundos: revisa la carpeta de correo no deseado
            si no lo ves.
          </p>
        </div>
      </div>

      <div className="verify-bar-form">
        <OtpInput
          id="verify-otp"
          label="Código de verificación"
          value={code}
          onChange={setCode}
          onComplete={(value) => void submit(value)}
          errorId={error ? 'verify-otp-error' : undefined}
          invalid={!!error}
          disabled={verifying}
        />

        {/* Cuenta atrás del código. `role="timer"` porque es un temporizador:
            su aria-live implícito es "off" y así el cambio de texto de cada
            segundo NO se anuncia (un lector de pantalla reciting 600 números
            sería peor que no decir nada). El cambio de estado que sí importa,
            la caducidad, se anuncia por la región viva oculta de al lado. */}
        <p
          className={[
            'label-sm verify-bar-expiry',
            expired ? 'is-expired' : warnExpiry ? 'is-warning' : '',
          ]
            .filter(Boolean)
            .join(' ')}
          role="timer"
        >
          {expired ? (
            'Código caducado. Pide otro.'
          ) : (
            <>
              Caduca en <span className="verify-bar-expiry-value tnum">{countdown}</span>
            </>
          )}
        </p>

        {/* Región viva para la caducidad. Va oculta y SIEMPRE montada (vacía
            mientras hay código): una región que aparece ya con el texto no la
            anuncian los lectores de pantalla, y una que viviera en la banda de
            mensajes sumaría una fila y un salto de altura justo al caducar.
            Misma clase que usa OtpInput para su cuenta de dígitos. */}
        <span className="auth-visually-hidden" role="status">
          {expired ? 'El código ha caducado. Pide otro con el botón Reenviar.' : ''}
        </span>

        <div className="verify-bar-actions">
          <button
            type="button"
            className="btn-coral label-md"
            onClick={() => void submit(code)}
            disabled={verifying}
          >
            <span className="material-symbols-outlined" aria-hidden="true">
              {verifying ? 'progress_activity' : 'verified'}
            </span>
            {verifying ? 'Verificando…' : 'Verificar'}
          </button>
          <button
            type="button"
            className="btn-secondary label-md verify-bar-resend"
            onClick={() => void resendCode()}
            /* `verifying` también entra: resendCode sale por el cerrojo busy
               mientras hay una verificación en vuelo, y sin esto el clic se
               perdía sin feedback con el botón aparentemente habilitado.
               `resendBlocked` es el máximo de los dos enfriamientos (local y
               cubo del servidor), así que el botón no se reactiva solo porque
               se agote el de 60 s si el del servidor sigue corriendo. */
            disabled={sending || verifying || resendBlocked}
            /* El `title` es la MISMA información en frase entera, no un extra:
               la etiqueta abrevia a "47 min" y aquí está el motivo DEVELOPADO
               para el puntero. No es el único sitio donde se dice: el texto
               visible ya nombra el motivo ("Límite horario · 47 min") y dentro
               del botón hay una frase para lector de pantalla, porque un
               `title` no se anuncia. */
            title={
              resendBlocked
                ? bucketIsTheBlocker
                  ? `Límite de reenvíos del servidor: quedan ${humanizeWait(bucketWait)}`
                  : `Espera del botón: quedan ${humanizeWait(localWait)}`
                : undefined
            }
          >
            <span className="material-symbols-outlined" aria-hidden="true">
              {sending ? 'progress_activity' : 'forward_to_inbox'}
            </span>
            {sending ? (
              'Enviando…'
            ) : resendBlocked ? (
              /* Dos textos distintos porque son dos esperas distintas, y el
                 ancho está reservado para el más largo de los dos (ver
                 .verify-bar-resend). Cifras tabulares en ambos: el contador
                 baja cada segundo y no debe desplazar ni un píxel la etiqueta.
                 - local: el cooldown de 60 s, que es un clic de más que el
                   cliente se puso a sí mismo;
                 - cubo del servidor: el límite horario de verdad. Aquí no se
                   puede decir "Reenviar en…" y ya, porque "en 1 hora" sin
                   motivo parece un fallo: la etiqueta cambia de verbo a
                   "Límite horario" y así se explica sin necesitar un tooltip. */
              <>
                {bucketIsTheBlocker ? (
                  <>
                    Límite horario · <span className="tnum">{compactWait(wait)}</span>
                  </>
                ) : (
                  <>
                    Reenviar en <span className="tnum">{compactWait(wait)}</span>
                  </>
                )}
                {/* El texto visible es corto; para lector de pantalla se
                    traduce a una frase, que es lo que suena bien auralmente y
                    no ocupa ancho. */}
                <span className="auth-visually-hidden">
                  {bucketIsTheBlocker
                    ? `. Límite horario del servidor: se puede pedir otro código en ${humanizeWait(bucketWait)}.`
                    : `. Espera del botón: quedan ${humanizeWait(localWait)}.`}
                </span>
              </>
            ) : (
              'Reenviar código'
            )}
          </button>

          {/* Cuántos códigos se han pedido esta hora. Con la cuenta ya
              verificada el tope llega a 0 y aquí no se pinta nada: "0 de 0
              códigos" se leería como un dato roto. */}
          {showCodes && (
            <span className="label-sm verify-bar-codes tnum">
              Has pedido {codesUsed} de {codeLimit} códigos esta hora
            </span>
          )}
        </div>
      </div>

      {error && (
        <p className="verify-bar-msg is-error" id="verify-otp-error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="verify-bar-msg is-ok" role="status">
          {notice}
        </p>
      )}
    </section>
  )
}
