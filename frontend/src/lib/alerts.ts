import { useEffect, useRef, useSyncExternalStore } from 'react'
import type { Status } from '../api'
import { LEVELS } from './format'
import { local } from './storage'

/** Browser-side alarm alerts: a system notification and a chime when an alarm starts, while the dashboard is open. */
export interface AlertPrefs {
  notify: boolean
  sound: boolean
}

const KEY = 'guardian.alerts'
const listeners = new Set<() => void>()
let prefs: AlertPrefs = (() => {
  try {
    return { notify: false, sound: false, ...JSON.parse(local.get(KEY) ?? '{}') }
  } catch {
    return { notify: false, sound: false }
  }
})()

export const alertPrefs = {
  get: () => prefs,
  subscribe(fn: () => void) {
    listeners.add(fn)
    return () => listeners.delete(fn)
  },
  set(next: Partial<AlertPrefs>) {
    prefs = { ...prefs, ...next }
    local.set(KEY, JSON.stringify(prefs))
    listeners.forEach((fn) => fn())
  },
}

export const useAlertPrefs = () => useSyncExternalStore(alertPrefs.subscribe, alertPrefs.get)

export const notificationsSupported = () => typeof window !== 'undefined' && 'Notification' in window

/** Asks for permission (needs a click). Returns whether notifications may be shown. */
export async function enableNotifications(): Promise<boolean> {
  if (!notificationsSupported()) return false
  if (Notification.permission === 'granted') return true
  if (Notification.permission === 'denied') return false
  return (await Notification.requestPermission()) === 'granted'
}

let ctx: AudioContext | null = null

/** Unlocks audio; browsers only allow it after a click, so call this from one. */
export function primeAudio() {
  try {
    ctx ??= new AudioContext()
    if (ctx.state === 'suspended') void ctx.resume()
  } catch {
    ctx = null
  }
}

/** Two-tone chime, repeated: urgent but not the siren. */
export function playChime(repeats = 3) {
  primeAudio()
  if (!ctx) return
  const start = ctx.currentTime + 0.05
  for (let r = 0; r < repeats; r++) {
    ;[880, 660].forEach((freq, i) => {
      const t = start + r * 0.7 + i * 0.22
      const osc = ctx!.createOscillator()
      const gain = ctx!.createGain()
      osc.type = 'sine'
      osc.frequency.value = freq
      gain.gain.setValueAtTime(0.0001, t)
      gain.gain.exponentialRampToValueAtTime(0.25, t + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.2)
      osc.connect(gain).connect(ctx!.destination)
      osc.start(t)
      osc.stop(t + 0.22)
    })
  }
}

/** How bad the alarm is: 0 none, 3 intruder, 4 alarm, 5 panic. */
function alarmRank(s: Status): number {
  if (s.panic) return 5
  const worst = Math.max(0, ...s.cameras.map((c) => c.threat_level))
  return worst >= 3 ? worst : 0
}

/** Notifies and chimes when an alarm starts or escalates, if the owner turned that on. */
export function useAlarmAlerts(status: Status | null | undefined) {
  const last = useRef(0)
  useEffect(() => {
    if (!status) return
    const rank = alarmRank(status)
    const previous = last.current
    last.current = rank
    if (rank <= previous) return
    const p = alertPrefs.get()
    const worst = [...status.cameras].sort((a, b) => b.threat_level - a.threat_level)[0]
    const title = status.panic || !worst ? 'Panic alarm raised' : `${LEVELS[worst.threat_level].label} at ${worst.name}`
    const body = status.panic || !worst ? 'The panic button was pressed.' : `Unrecognised person on camera for ${Math.round(worst.incident_seconds)} s.`
    if (p.sound) playChime()
    if (p.notify && notificationsSupported() && Notification.permission === 'granted') {
      try {
        const n = new Notification(title, { body, tag: 'guardian-alarm', icon: '/favicon.svg', requireInteraction: true })
        n.onclick = () => {
          window.focus()
          location.hash = '#/live'
          n.close()
        }
      } catch {
        // some mobile browsers only allow notifications from a service worker
      }
    }
  }, [status])
}
