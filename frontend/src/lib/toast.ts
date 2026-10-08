import { createContext, useContext } from 'react'

export type ToastKind = 'success' | 'error' | 'info'
export type Notify = (message: string, kind?: ToastKind) => void

export const ToastContext = createContext<Notify>(() => {})

export const useToast = () => useContext(ToastContext)

export const errorMessage = (err: unknown) => (err instanceof Error ? err.message : String(err))
