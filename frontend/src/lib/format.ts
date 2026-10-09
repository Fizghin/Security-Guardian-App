import type { ScheduleStatus } from '../api'

export function formatBytes(bytes: number): string {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)))
  const value = bytes / 1024 ** i
  return `${value >= 10 || i === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[i]}`
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '–'
  const s = Math.max(0, Math.round(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (h) return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
  return `${m}:${String(sec).padStart(2, '0')}`
}

export function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400)
  const h = Math.floor((seconds % 86400) / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (d) return `${d}d ${h}h`
  if (h) return `${h}h ${m}m`
  return `${m}m`
}

const timeFmt = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
const dateTimeFmt = new Intl.DateTimeFormat(undefined, {
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
})
const dayFmt = new Intl.DateTimeFormat(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
const shortDateFmt = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })
const hourFmt = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit' })
// Wall-clock formats: they show a time as written, whatever the browser's time zone
const wallHourFmt = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })
const wallWeekdayTimeFmt = new Intl.DateTimeFormat(undefined, { weekday: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })

export const formatTime = (iso: string) => timeFmt.format(new Date(iso))
export const formatDateTime = (iso: string) => dateTimeFmt.format(new Date(iso))
export const formatDay = (iso: string) => dayFmt.format(new Date(iso))
export const formatShortDate = (iso: string) => shortDateFmt.format(new Date(iso))
export const formatHour = (iso: string) => hourFmt.format(new Date(iso))

/**
 * "07:00" when it is less than a day away, otherwise "Mon 22:00". The time is shown as the clock that
 * wrote `iso` shows it (the Guardian computer's), not converted to the browser's time zone.
 */
export function formatUpcoming(iso: string, now = Date.now()): string {
  const wall = new Date(`${iso.slice(0, 19)}Z`)
  return new Date(iso).getTime() - now < 20 * 3600 * 1000 ? wallHourFmt.format(wall) : wallWeekdayTimeFmt.format(wall)
}

/** What the schedule does next, for the header (short) and Settings (long), given the real armed state. */
export function describeSchedule(schedule: ScheduleStatus | undefined, armed: boolean): { short: string | null; long: string } | null {
  if (!schedule?.enabled || schedule.active == null) return null
  if (schedule.waiting) {
    return { short: 'Schedule: disarms when the alarm ends', long: 'Still armed: the scheduled disarm waits until the alarm is reset or clears.' }
  }
  const byHand = armed !== schedule.active
  const state = `${armed ? 'Armed' : 'Disarmed'} by ${byHand ? 'hand' : 'the schedule'}`
  if (!schedule.next_change) return { short: byHand ? state : null, long: `${state}.` }
  const next = `${armed ? 'disarms' : 'arms'} ${formatUpcoming(schedule.next_change)}`
  return { short: byHand ? `${state}; ${next}` : `Schedule: ${next}`, long: `${state}; ${next}.` }
}

export function isToday(iso: string): boolean {
  const d = new Date(iso)
  const now = new Date()
  return d.toDateString() === now.toDateString()
}

export function timeAgo(epochSeconds: number, now = Date.now() / 1000): string {
  const diff = Math.max(0, Math.round(now - epochSeconds))
  if (diff < 5) return 'just now'
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

export const LEVELS = [
  { label: 'Clear', text: 'text-emerald-400', bg: 'bg-emerald-500', soft: 'bg-emerald-500/10 text-emerald-300 ring-emerald-500/30' },
  { label: 'Person detected', text: 'text-yellow-300', bg: 'bg-yellow-400', soft: 'bg-yellow-400/10 text-yellow-200 ring-yellow-400/30' },
  { label: 'Loitering', text: 'text-amber-400', bg: 'bg-amber-500', soft: 'bg-amber-500/10 text-amber-300 ring-amber-500/30' },
  { label: 'Intruder', text: 'text-orange-400', bg: 'bg-orange-500', soft: 'bg-orange-500/15 text-orange-300 ring-orange-500/40' },
  { label: 'Alarm', text: 'text-red-400', bg: 'bg-red-600', soft: 'bg-red-600/20 text-red-300 ring-red-500/50' },
] as const

export const SEVERITY_STYLE: Record<string, string> = {
  INFO: 'bg-zinc-800 text-zinc-300 ring-zinc-700',
  LOW: 'bg-yellow-400/10 text-yellow-200 ring-yellow-400/25',
  MEDIUM: 'bg-amber-500/10 text-amber-300 ring-amber-500/30',
  HIGH: 'bg-orange-500/15 text-orange-300 ring-orange-500/35',
  CRITICAL: 'bg-red-600/20 text-red-300 ring-red-500/40',
}

export const SEVERITY_COLOR: Record<string, string> = {
  INFO: '#52525b',
  LOW: '#facc15',
  MEDIUM: '#f59e0b',
  HIGH: '#f97316',
  CRITICAL: '#dc2626',
}

const EVENT_LABELS: Record<string, string> = {
  DETECTION: 'Detection',
  ESCALATION: 'Escalation',
  CLEARED: 'Cleared',
  VOICE: 'Voice',
  RECORDING: 'Recording',
  CLIP_SAVED: 'Clip saved',
  ALERT: 'Alert',
  SIREN: 'Siren',
  PANIC: 'Panic',
  RESET: 'Reset',
  ARMED: 'Armed',
  DISARMED: 'Disarmed',
  INSIDER: 'Insider',
  NOTIFICATION: 'Notification',
  TEST: 'Test',
  SYSTEM: 'System',
  GREETING: 'Greeting',
  CAMERA_OFFLINE: 'Camera offline',
  CAMERA_ONLINE: 'Camera online',
}

export const eventLabel = (type: string) =>
  EVENT_LABELS[type] ?? type.charAt(0) + type.slice(1).toLowerCase().replace(/[_-]/g, ' ')

export const reasonLabel = (reason: string) =>
  ({ intruder: 'Intrusion', panic: 'Panic', test: 'Test' })[reason] ?? reason
