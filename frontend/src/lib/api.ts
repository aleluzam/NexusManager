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

export interface ValidationIssue {
  loc: (string | number)[]
  msg: string
}

export class ApiError extends Error {
  readonly status: number
  readonly detail: string
  readonly issues?: ValidationIssue[]

  constructor(status: number, detail: string, issues?: ValidationIssue[]) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.issues = issues
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

async function parseError(res: Response): Promise<ApiError> {
  let detail = res.statusText
  let issues: ValidationIssue[] | undefined
  try {
    const body = await res.json()
    if (typeof body.detail === 'string') {
      detail = body.detail
    } else {
      detail = 'Solicitud inválida'
      issues = body.detail ?? undefined
    }
  } catch {
    /* cuerpo no JSON */
  }
  return new ApiError(res.status, detail, issues)
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