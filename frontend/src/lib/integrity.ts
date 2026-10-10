import type { IntegrityStatus } from '../api'

export const INTEGRITY: Record<IntegrityStatus, { label: string; style: string }> = {
  verified: { label: 'Verified', style: 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30' },
  tampered: { label: 'Tampered', style: 'bg-red-600/20 text-red-300 ring-red-500/50' },
  missing: { label: 'Missing', style: 'bg-red-600/20 text-red-300 ring-red-500/50' },
  unsealed: { label: 'Not sealed', style: 'bg-amber-500/10 text-amber-300 ring-amber-500/30' },
  removed: { label: 'Deleted', style: 'bg-zinc-800 text-zinc-300 ring-zinc-700' },
}
