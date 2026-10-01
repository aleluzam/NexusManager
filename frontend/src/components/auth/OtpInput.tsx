import {
  useImperativeHandle,
  useRef,
  type ClipboardEvent,
  type KeyboardEvent,
  type Ref,
} from 'react'
import './auth.css'

/* Código de un solo uso de 6 dígitos. Se acepta tanto "042731" como
   "042 731": cualquier carácter que no sea un dígito se descarta antes de
   colocar el valor en las casillas. */
const CODE_LENGTH = 6
const NON_DIGITS = /\D/g
const EMPTY = ''

interface OtpInputProps {
  /** Prefijo de id: la casilla n es `${id}-${n}`. La primera caja queda
      enlazable con un <label htmlFor> externo. */
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  onComplete?: (value: string) => void
  errorId?: string
  invalid?: boolean
  disabled?: boolean
  autoFocus?: boolean
  ref?: Ref<{ focus: (index?: number) => void }>
}

export default function OtpInput({
  id,
  label,
  value,
  onChange,
  onComplete,
  errorId,
  invalid = false,
  disabled = false,
  autoFocus = false,
  ref,
}: OtpInputProps) {
  const boxesRef = useRef<(HTMLInputElement | null)[]>([])
  const digits = value.slice(0, CODE_LENGTH)
  const isComplete = digits.length === CODE_LENGTH

  const focusBox = (index = 0) => {
    const target =
      boxesRef.current[Math.min(Math.max(index, 0), CODE_LENGTH - 1)]
    if (!target) return
    target.focus()
    target.select()
  }

  useImperativeHandle(ref, () => ({ focus: focusBox }))

  /* El valor expuesto al padre es siempre una secuencia densa de dígitos: lo
     que se escribe (o se pega) se coloca a partir de la casilla enfocada y
     el resto se compacta a la izquierda. Así `value` coincide literalmente
     con el código que se ha introducido y se puede enviar tal cual. */
  const cells = (): string[] => {
    const list: string[] = []
    for (let i = 0; i < CODE_LENGTH; i += 1) list.push(digits[i] ?? EMPTY)
    return list
  }

  const commit = (next: string, focusIndex: number) => {
    onChange(next)
    if (next.length === CODE_LENGTH && !isComplete) onComplete?.(next)
    focusBox(focusIndex)
  }

  const write = (typed: string, start: number) => {
    const list = cells()
    for (let i = 0; i < typed.length && start + i < CODE_LENGTH; i += 1) {
      list[start + i] = typed[i]
    }
    const filled = list.filter((cell) => cell !== EMPTY)
    commit(filled.join(EMPTY), Math.min(filled.length, CODE_LENGTH - 1))
  }

  const erase = (index: number) => {
    const list = cells()
    list[index] = EMPTY
    const kept = list.filter((cell) => cell !== EMPTY)
    commit(kept.join(EMPTY), Math.min(kept.length, CODE_LENGTH - 1))
  }

  const handleChange = (index: number, raw: string) => {
    const typed = raw.replace(NON_DIGITS, '').slice(0, CODE_LENGTH)
    if (typed.length === 0) {
      erase(index)
      return
    }
    // Si la casilla ya tenía un dígito, el carácter nuevo es el último de lo
    // tecleado; si estaba vacía, puede venir el código entero de un solo
    // golpe (autocompletado del sistema operativo) y se reparte entero.
    write(digits[index] ? typed.slice(-1) : typed, index)
  }

  const handleKeyDown = (index: number, event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Backspace') {
      // Con un dígito en la casilla, el borrado por defecto del input ya
      // dispara onChange ywrite() se encarga del resto.
      if (digits[index]) return
      event.preventDefault()
      if (index > 0) erase(index - 1)
      return
    }
    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      focusBox(index - 1)
      return
    }
    if (event.key === 'ArrowRight') {
      event.preventDefault()
      focusBox(index + 1)
      return
    }
    if (event.key === 'Delete') {
      event.preventDefault()
      erase(index)
    }
  }

  const handlePaste = (index: number, event: ClipboardEvent<HTMLInputElement>) => {
    event.preventDefault()
    const typed = event.clipboardData
      .getData('text')
      .replace(NON_DIGITS, '')
      .slice(0, CODE_LENGTH)
    if (typed.length === 0) return
    write(typed, index)
  }

  return (
    <div className="auth-otp" role="group" aria-label={label}>
      <div className="auth-otp-boxes">
        {Array.from({ length: CODE_LENGTH }, (_, index) => (
          <input
            key={index}
            ref={(node) => {
              boxesRef.current[index] = node
            }}
            id={`${id}-${index + 1}`}
            className={`auth-otp-box${invalid ? ' is-invalid' : ''}`}
            type="text"
            inputMode="numeric"
            pattern="[0-9]*"
            autoComplete="one-time-code"
            value={digits[index] ?? EMPTY}
            onChange={(event) => handleChange(index, event.target.value)}
            onKeyDown={(event) => handleKeyDown(index, event)}
            onPaste={(event) => handlePaste(index, event)}
            onFocus={(event) => event.target.select()}
            disabled={disabled}
            autoFocus={autoFocus && index === 0}
            aria-label={`${label}: dígito ${index + 1} de ${CODE_LENGTH}`}
            aria-invalid={invalid || undefined}
            aria-describedby={errorId}
          />
        ))}
      </div>

      {/* Lectores de pantalla: las casillas ya se anuncian una a una con su
          posición, aquí solo se informa del progreso y del cierre. */}
      <span className="auth-visually-hidden" aria-live="polite">
        {isComplete
          ? 'Código completo introducido.'
          : `Dígitos introducidos: ${digits.length} de ${CODE_LENGTH}.`}
      </span>
    </div>
  )
}
