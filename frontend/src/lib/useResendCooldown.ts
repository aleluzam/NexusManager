import { useEffect, useState } from 'react'

/**
 * Cuenta atrás para limitar el reenvío de códigos de un solo uso.
 *
 * El intervalo se crea solo mientras queda tiempo y se desmonta en el cleanup
 * del efecto: React Compiler memoiza el render pero no limpia timers, así que
 * sin este `clearInterval` quedaría un intervalo huérfano por montaje.
 */
export function useResendCooldown(seconds: number): {
  remaining: number
  start: (duration?: number) => void
  reset: () => void
} {
  const [remaining, setRemaining] = useState(0)

  useEffect(() => {
    if (remaining <= 0) return
    const timer = window.setInterval(() => {
      setRemaining((prev) => (prev > 0 ? prev - 1 : 0))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [remaining])

  return {
    remaining,
    start: (duration) => setRemaining(duration ?? seconds),
    reset: () => setRemaining(0),
  }
}
