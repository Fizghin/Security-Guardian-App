import { useState } from 'react'
import { Monitor, Moon, Sun } from 'lucide-react'
import { alertPrefs, enableNotifications, notificationsSupported, playChime, primeAudio, useAlertPrefs } from '../lib/alerts'
import { cx } from '../lib/cx'
import type { ThemePref } from '../lib/theme'
import { useToast } from '../lib/toast'
import { Button, Toggle } from './ui'

/** Browser notification and chime switches, shared by the top bar popover and Settings → Appearance. */
export function AlertControls() {
  const prefs = useAlertPrefs()
  const notify = useToast()
  const [denied, setDenied] = useState(() => notificationsSupported() && Notification.permission === 'denied')

  const setNotify = async (on: boolean) => {
    if (on && !(await enableNotifications())) {
      setDenied(notificationsSupported() && Notification.permission === 'denied')
      notify(notificationsSupported() ? 'The browser blocked notifications for this page' : 'This browser has no notifications', 'error')
      return
    }
    alertPrefs.set({ notify: on })
  }

  return (
    <div className="space-y-4">
      <Toggle
        checked={prefs.notify}
        onChange={setNotify}
        label="Desktop notifications"
        description={denied ? 'Blocked by the browser: allow notifications with the icon next to the address.' : 'A notification when an alarm starts, even when this tab is in the background.'}
      />
      <Toggle
        checked={prefs.sound}
        onChange={(on) => {
          if (on) primeAudio()
          alertPrefs.set({ sound: on })
        }}
        label="Alarm chime"
        description="Plays a chime in this browser when an alarm starts or gets worse."
      />
      <Button size="sm" onClick={() => playChime(1)}>
        Play the chime
      </Button>
      <p className="hint">These only work while the dashboard is open in this browser. Phone alerts are set up under Notifications.</p>
    </div>
  )
}

const THEMES: { value: ThemePref; label: string; icon: typeof Sun }[] = [
  { value: 'light', label: 'Light', icon: Sun },
  { value: 'dark', label: 'Dark', icon: Moon },
  { value: 'system', label: 'System', icon: Monitor },
]

export function ThemePicker({ value, onChange }: { value: ThemePref; onChange: (t: ThemePref) => void }) {
  return (
    <div className="grid grid-cols-3 gap-2" role="radiogroup" aria-label="Theme">
      {THEMES.map(({ value: v, label, icon: Icon }) => (
        <button
          key={v}
          type="button"
          role="radio"
          aria-checked={value === v}
          onClick={() => onChange(v)}
          className={cx(
            'flex flex-col items-center gap-2 rounded-xl border p-3 text-xs font-medium transition-colors',
            value === v ? 'border-blue-500 bg-blue-500/10 text-zinc-100' : 'border-zinc-800 text-zinc-400 hover:border-zinc-600 hover:text-zinc-200',
          )}
        >
          {/* Fixed colours: a preview of each theme, whatever the current one is */}
          <span
            className="flex h-10 w-full items-center justify-center rounded-lg border"
            style={v === 'light' ? { background: '#fff', color: '#3f3f46', borderColor: '#d4d4d8' } : v === 'dark' ? { background: '#09090b', color: '#d4d4d8', borderColor: '#3f3f46' } : { background: 'linear-gradient(90deg,#fff 50%,#09090b 50%)', color: '#71717a' }}
          >
            <Icon className="h-4 w-4" />
          </span>
          {label}
        </button>
      ))}
    </div>
  )
}
