import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import {
  apiFetch,
  setAccessToken,
  type AuthResponse,
  type User,
} from '../lib/api'

export type AuthStatus = 'loading' | 'guest' | 'authenticated'

interface AuthContextValue {
  status: AuthStatus
  user: User | null
  login: (email: string, password: string, rememberMe: boolean) => Promise<void>
  register: (fullName: string, email: string, password: string) => Promise<void>
  updateProfile: (fullName: string) => Promise<User>
  verifyEmail: (code: string) => Promise<User>
  resendVerificationCode: () => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

type AuthState = { user: User | null }

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ user: null })
  const [status, setStatus] = useState<AuthStatus>('loading')

  // Carga de la sesión al montar: /me directo; si falla con 401 se intenta
  // un refresh silencioso (cookie httpOnly) antes de declarar al usuario
  // como invitado. Los setState ocurren tras await (fuera de la fase síncrona).
  useEffect(() => {
    let cancelled = false

    async function loadSession() {
      try {
        const me = await apiFetch<User>('/api/v1/auth/me')
        if (cancelled) return
        setState({ user: me })
        setStatus('authenticated')
      } catch {
        if (cancelled) return
        setAccessToken(null)
        setState({ user: null })
        setStatus('guest')
      }
    }

    void loadSession()
    return () => {
      cancelled = true
    }
  }, [])

  const applyAuthResponse = useCallback((res: AuthResponse) => {
    setAccessToken(res.access_token)
    setState({ user: res.user })
    setStatus('authenticated')
  }, [])

  const login = useCallback(
    async (email: string, password: string, rememberMe: boolean) => {
      const res = await apiFetch<AuthResponse>('/api/v1/auth/login', {
        method: 'POST',
        body: JSON.stringify({ email, password, remember_me: rememberMe }),
      })
      applyAuthResponse(res)
    },
    [applyAuthResponse],
  )

  const register = useCallback(
    async (fullName: string, email: string, password: string) => {
      const res = await apiFetch<AuthResponse>('/api/v1/auth/register', {
        method: 'POST',
        body: JSON.stringify({ full_name: fullName, email, password }),
      })
      applyAuthResponse(res)
    },
    [applyAuthResponse],
  )

  const logout = useCallback(async () => {
    try {
      await apiFetch<void>('/api/v1/auth/logout', { method: 'POST' })
    } finally {
      setAccessToken(null)
      setState({ user: null })
      setStatus('guest')
    }
  }, [])

  // Actualiza el nombre en el estado para que Navbar y Dashboard lo reflejen
  // sin necesidad de recargar la página. Devuelve el User ya normalizado por
  // el servidor (colapsa espacios internos) para que el formulario quede
  // sincronizado con lo realmente guardado.
  const updateProfile = useCallback(async (fullName: string): Promise<User> => {
    const updated = await apiFetch<User>('/api/v1/auth/me', {
      method: 'PATCH',
      body: JSON.stringify({ full_name: fullName }),
    })
    setState({ user: updated })
    return updated
  }, [])

  // Verificación del correo: el servidor devuelve el User ya con
  // is_email_verified en true, así que se adopta su respuesta tal cual (igual
  // que updateProfile) y el aviso de verificación del cockpit se aparta solo.
  const verifyEmail = useCallback(async (code: string): Promise<User> => {
    const updated = await apiFetch<User>('/api/v1/auth/verify-email', {
      method: 'POST',
      body: JSON.stringify({ code }),
    })
    setState({ user: updated })
    return updated
  }, [])

  // Reenvío del código de verificación. Lanza ApiError para que la UI muestre
  // el `detail` del servidor (caducado, límite de intentos, fallo de envío).
  const resendVerificationCode = useCallback(async (): Promise<void> => {
    await apiFetch<{ detail: string }>('/api/v1/auth/resend-verification', {
      method: 'POST',
    })
  }, [])

  const value = useMemo(
    () => ({
      status,
      user: state.user,
      login,
      register,
      updateProfile,
      verifyEmail,
      resendVerificationCode,
      logout,
    }),
    [
      status,
      state.user,
      login,
      register,
      updateProfile,
      verifyEmail,
      resendVerificationCode,
      logout,
    ],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// oxlint-disable-next-line react/only-export-components
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth debe usarse dentro de <AuthProvider>')
  }
  return ctx
}