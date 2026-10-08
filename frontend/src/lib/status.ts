import { createContext, useContext } from 'react'
import type { Status } from '../api'

export interface StatusState {
  status: Status | null
  /** true when the last poll failed (server stopped or unreachable) */
  offline: boolean
  refresh: () => Promise<void>
}

export const StatusContext = createContext<StatusState>({ status: null, offline: false, refresh: async () => {} })

export const useStatus = () => useContext(StatusContext)
