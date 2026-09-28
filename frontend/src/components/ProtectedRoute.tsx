import { Navigate, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'
import { useAuth } from '../auth/AuthContext'

export default function ProtectedRoute({ children }: { children: ReactNode }) {
  const { status } = useAuth()
  const location = useLocation()

  if (status === 'loading') {
    return (
      <div
        style={{
          minHeight: '100svh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          background: 'var(--nx-bg)',
          color: 'var(--nx-primary)',
        }}
      >
        <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: 32 }}>
          progress_activity
        </span>
        <span style={{ marginLeft: '0.5rem' }}>Cargando sesión…</span>
      </div>
    )
  }

  if (status === 'guest') {
    return (
      <Navigate
        to="/auth"
        replace
        state={{ from: location.pathname }}
      />
    )
  }

  return children
}