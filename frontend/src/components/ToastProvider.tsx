import { useCallback, useState, type ReactNode } from 'react'
import { CheckCircle2, Info, XCircle } from 'lucide-react'
import { ToastContext, type ToastKind } from '../lib/toast'
import { cx } from '../lib/cx'

interface Toast {
  id: number
  message: string
  kind: ToastKind
}

let nextId = 1

export default function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const notify = useCallback((message: string, kind: ToastKind = 'info') => {
    const id = nextId++
    setToasts((t) => [...t.slice(-3), { id, message, kind }])
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === 'error' ? 7000 : 4000)
  }, [])

  return (
    <ToastContext.Provider value={notify}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2" aria-live="polite">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={cx(
              'pointer-events-auto flex items-start gap-2.5 rounded-md border px-3 py-2.5 text-sm shadow-lg',
              t.kind === 'error' && 'border-red-900 bg-red-950 text-red-200',
              t.kind === 'success' && 'border-zinc-700 bg-zinc-900 text-zinc-100',
              t.kind === 'info' && 'border-zinc-700 bg-zinc-900 text-zinc-100',
            )}
          >
            {t.kind === 'error' ? (
              <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />
            ) : t.kind === 'success' ? (
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />
            ) : (
              <Info className="mt-0.5 h-4 w-4 shrink-0 text-blue-400" />
            )}
            <span>{t.message}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}
