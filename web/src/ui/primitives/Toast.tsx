import { createContext, useCallback, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

export type ToastTone = 'info' | 'ok' | 'warn' | 'danger'

export interface ToastItem {
  id: number
  message: string
  tone: ToastTone
}

export interface ToastApi {
  push: (message: string, tone?: ToastTone) => void
}

const ToastContext = createContext<ToastApi>({ push: () => undefined })

export function useToast(): ToastApi {
  return useContext(ToastContext)
}

const TONES: Record<ToastTone, string> = {
  info: 'bg-info-bg/40',
  ok: 'bg-ok-bg/40',
  warn: 'bg-warn-bg/40',
  danger: 'bg-danger-bg/40',
}

/** 极简 toast：只做「发生了什么」的短提示，不做动作确认（审批另有队列）。 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])

  const push = useCallback((message: string, tone: ToastTone = 'info') => {
    setItems((current) => [...current, { id: Date.now() + current.length, message, tone }])
  }, [])

  const api = useMemo(() => ({ push }), [push])

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        role="status"
        aria-live="polite"
        className="pointer-events-none fixed bottom-4 right-4 z-toast flex flex-col gap-2"
      >
        {items.map((item) => (
          <p
            key={item.id}
            className={`sketch-chip max-w-sm px-3 py-2 text-sm ${TONES[item.tone]}`}
            onAnimationEnd={() =>
              setItems((current) => current.filter((entry) => entry.id !== item.id))
            }
          >
            {item.message}
          </p>
        ))}
      </div>
    </ToastContext.Provider>
  )
}
