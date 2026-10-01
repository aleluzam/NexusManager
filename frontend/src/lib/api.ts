/**
 * Cliente HTTP tipado para la API de NexusManager.
 *
 * Seguridad:
 * - El access token (JWT) vive SOLO en memoria (nunca en localStorage),
 *   mitigando robo por XSS.
 * - El refresh token viaja en cookie httpOnly + SameSite=Lax, invisible a JS.
 * - Ante un 401 se intenta un refresh silencioso (rotación de cookie) y se
 *   reintenta la petición original una vez.
 */

export interface User {
  id: string
  email: string
  full_name: string
  auth_provider: string
  is_email_verified: boolean
  created_at: string
}

export interface AuthResponse {
  user: User
  access_token: string
  token_type: string
  expires_in: number
}

/* Sesión activa tal y como la expone GET /api/v1/auth/sessions: nunca
   incluye el token ni su hash (equivalente a SessionPublic del backend). */
export interface AuthSession {
  id: number
  created_at: string
  expires_at: string
  remember_me: boolean
  current: boolean
}

export interface ValidationIssue {
  loc: (string | number)[]
  msg: string
}

/**
 * Estado de la verificación del correo, tal y como lo expone
 * GET /api/v1/auth/verification-status (solo lectura).
 *
 * Tres matices del contrato que la UI tiene que respetar:
 * - `pending` con `expires_in_seconds: 0` es un código CADUCADO sin consumir:
 *   hay que pedir otro, no es lo mismo que "no hay código".
 * - `resend_available_in_seconds` es lo que falta para que el CUBO del
 *   limitador de reenvío (3 por hora) vuelva a admitir una petición. No es el
 *   cooldown de 60 s que el cliente se aplica a sí mismo: aquel solo evita el
 *   clic repetido antes de que la red conteste, este es el límite que el
 *   servidor impone. 0 significa "disponible ahora mismo".
 * - Con la cuenta ya verificada los cinco contadores vienen a 0, incluido
 *   `codes_limit_per_hour`. Por eso la UI no pinta contador cuando el tope es
 *   0: "0 de 0 códigos" se leería como un dato roto, no como una respuesta.
 */
export interface VerificationStatus {
  pending: boolean
  expires_in_seconds: number
  resend_available_in_seconds: number
  codes_used_last_hour: number
  codes_limit_per_hour: number
}

export class ApiError extends Error {
  readonly status: number
  readonly detail: string
  readonly issues?: ValidationIssue[]
  /**
   * Segundos que faltan para poder reintentar. Solo lo traen los 429
   * estructurados que emite `rate_limit_exceeded_handler`
   * (`{"detail": ..., "retry_after_seconds": N}` + cabecera `Retry-After`).
   *
   * No todos los 429 del backend lo llevan: el tope de códigos por hora de
   * `resend-verification` es un `HTTPException` normal y solo trae `detail`.
   * Por eso es opcional y quien lo pinte tiene que caer en `detail` cuando no
   * esté, en vez de mostrar un tiempo inventado.
   */
  readonly retry_after_seconds?: number

  constructor(
    status: number,
    detail: string,
    issues?: ValidationIssue[],
    retry_after_seconds?: number,
  ) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.issues = issues
    this.retry_after_seconds = retry_after_seconds
  }
}

const BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? ''
const REFRESH_URL = `${BASE_URL}/api/v1/auth/refresh`

/* El único lugar del frontend donde vive el access token */
let accessToken: string | null = null

/* Un solo refresh en vuelo para peticiones 401 concurrentes */
let refreshInFlight: Promise<boolean> | null = null

export function setAccessToken(token: string | null): void {
  accessToken = token
}

export function getAccessToken(): string | null {
  return accessToken
}

export function isAuthenticated(): boolean {
  return accessToken !== null
}

async function rawFetch(
  path: string,
  options: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(options.headers)
  headers.set('Accept', 'application/json')
  if (!(options.body instanceof FormData)) {
    headers.set('Content-Type', 'application/json')
  }
  if (accessToken) {
    headers.set('Authorization', `Bearer ${accessToken}`)
  }
  return fetch(`${BASE_URL}${path}`, {
    ...options,
    headers,
    credentials: 'include',
  })
}

/* Segundos de un `Retry-After`. La cabecera es un mínimo ("no antes de"), así
   que solo se acepta un entero no negativo: en HTTP admite también una fecha,
   y un `NaN` colándose en `retry_after_seconds` se convertiría en un "quedan
   NaN minutos" en la UI. */
function parseRetryAfterHeader(value: string | null): number | undefined {
  if (value === null) return undefined
  const seconds = Number.parseInt(value, 10)
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : undefined
}

async function parseError(res: Response): Promise<ApiError> {
  let detail = res.statusText
  let issues: ValidationIssue[] | undefined
  let retryAfter: number | undefined
  try {
    const body = await res.json()
    if (typeof body.detail === 'string') {
      detail = body.detail
    } else {
      detail = 'Solicitud inválida'
      issues = body.detail ?? undefined
    }
    // 429 estructurado: el `detail` de arriba ya es el texto legible del
    // servidor; aquí solo se añade el número para que la UI pueda decir el
    // tiempo exacto que le queda en vez de repetir una frase genérica.
    if (typeof body.retry_after_seconds === 'number') {
      retryAfter = body.retry_after_seconds
    }
  } catch {
    /* cuerpo no JSON */
  }
  if (retryAfter === undefined) {
    // La cabecera sale del mismo cálculo que el cuerpo, así que si el cuerpo
    // no vino en JSON (o vino de otra forma) la cabecera sigue siendo la
    // fuente estándar del valor.
    retryAfter = parseRetryAfterHeader(res.headers.get('Retry-After'))
  }
  return new ApiError(res.status, detail, issues, retryAfter)
}

async function silentRefresh(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const res = await fetch(REFRESH_URL, {
          method: 'POST',
          credentials: 'include',
          headers: { Accept: 'application/json' },
        })
        if (!res.ok) {
          setAccessToken(null)
          return false
        }
        const body = (await res.json()) as AuthResponse
        setAccessToken(body.access_token)
        return true
      } catch {
        setAccessToken(null)
        return false
      } finally {
        refreshInFlight = null
      }
    })()
  }
  return refreshInFlight
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  let res = await rawFetch(path, options)

  if (res.status === 401 && options.method !== 'POST' && options.method !== 'PATCH') {
    // No intentar refresh en el propio endpoint de refresh (loop infinito)
    if (!path.includes('/auth/refresh') && !path.includes('/auth/logout')) {
      const refreshed = await silentRefresh()
      if (refreshed) {
        res = await rawFetch(path, options)
      }
    }
  }

  if (!res.ok) {
    throw await parseError(res)
  }
  if (res.status === 204) {
    return undefined as T
  }
  return (await res.json()) as T
}

/**
 * Estado de la verificación del correo.
 *
 * GET, autenticado y de SOLO LECTURA: no emite códigos, no los consume y no
 * toca el cubo del limitador de reenvío (por eso el endpoint puede mirar ese
 * enfriamiento con `retry_after()` sin gastarle un reenvío al usuario). Es
 * justo esa propiedad la que permite reconsultarlo para tolerar la carrera del
 * registro sin introducir ningún coste para el usuario.
 */
export function fetchVerificationStatus(): Promise<VerificationStatus> {
  return apiFetch<VerificationStatus>('/api/v1/auth/verification-status')
}