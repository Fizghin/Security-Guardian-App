import { useState, type ReactNode } from 'react'
import { BellOff, Film, Map as MapIcon, Menu, Settings as SettingsIcon, ScrollText, Shield, ShieldAlert, ShieldOff, UserSearch, Users, Video, X } from 'lucide-react'
import { api } from '../api'
import { cx } from '../lib/cx'
import { describeSchedule, formatBytes, formatDuration, LEVELS } from '../lib/format'
import { href, PAGE_TITLES, type Page } from '../lib/route'
import { useStatus } from '../lib/status'
import { errorMessage, useToast } from '../lib/toast'
import { usePoll } from '../lib/usePoll'
import { Button, ConfirmDialog, Dot } from './ui'

const NAV: { page: Page; label: string; icon: typeof Video }[] = [
  { page: 'live', label: 'Live', icon: Video },
  { page: 'map', label: 'Map', icon: MapIcon },
  { page: 'events', label: 'Events', icon: ScrollText },
  { page: 'recordings', label: 'Recordings', icon: Film },
  { page: 'insiders', label: 'Insiders', icon: Users },
  { page: 'visitors', label: 'Visitors', icon: UserSearch },
  { page: 'settings', label: 'Settings', icon: SettingsIcon },
]

function Meter({ label, value, detail }: { label: string; value: number; detail: string }) {
  return (
    <div>
      <div className="mb-1 flex justify-between text-[11px] text-zinc-500">
        <span>{label}</span>
        <span className="tabular-nums text-zinc-400">{detail}</span>
      </div>
      <div className="h-1 overflow-hidden rounded-full bg-zinc-800">
        <div
          className={cx('h-full rounded-full', value > 85 ? 'bg-red-500' : value > 65 ? 'bg-amber-500' : 'bg-zinc-400')}
          style={{ width: `${Math.min(100, value)}%` }}
        />
      </div>
    </div>
  )
}

function Sidebar({ page, open, onClose }: { page: Page; open: boolean; onClose: () => void }) {
  const { status, offline } = useStatus()
  const { data: sys } = usePoll(api.system, 5000)

  return (
    <>
      <div className={cx('fixed inset-0 z-30 bg-black/60 lg:hidden', open ? 'block' : 'hidden')} onClick={onClose} />
      <aside
        className={cx(
          'fixed inset-y-0 left-0 z-40 flex w-56 flex-col border-r border-zinc-800 bg-zinc-950 transition-transform lg:static lg:translate-x-0',
          open ? 'translate-x-0' : '-translate-x-full',
        )}
      >
        <div className="flex h-14 items-center justify-between border-b border-zinc-800 px-4">
          <div className="flex items-center gap-2 font-semibold">
            <Shield className="h-5 w-5 text-zinc-300" />
            Guardian
          </div>
          <button type="button" className="rounded p-1 text-zinc-400 hover:bg-zinc-800 lg:hidden" onClick={onClose} aria-label="Close menu">
            <X className="h-4 w-4" />
          </button>
        </div>

        <nav className="flex-1 space-y-0.5 p-2">
          {NAV.map(({ page: p, label, icon: Icon }) => (
            <a
              key={p}
              href={href(p)}
              onClick={onClose}
              className={cx(
                'flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors',
                p === page ? 'bg-zinc-800 font-medium text-zinc-50' : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-100',
              )}
            >
              <Icon className="h-4 w-4" />
              {label}
            </a>
          ))}
        </nav>

        <div className="space-y-3 border-t border-zinc-800 p-4">
          {sys && (
            <>
              <Meter label="CPU" value={sys.cpu_percent} detail={`${Math.round(sys.cpu_percent)}%`} />
              <Meter
                label="Memory"
                value={sys.memory_percent}
                detail={`${formatBytes(sys.memory_used)} / ${formatBytes(sys.memory_total)}`}
              />
              <Meter
                label="Disk"
                value={100 - (sys.disk_free / sys.disk_total) * 100}
                detail={`${formatBytes(sys.disk_free)} free`}
              />
            </>
          )}
          <div className="flex items-center gap-2 text-xs text-zinc-400">
            <Dot className={offline ? 'bg-red-500' : status ? 'bg-emerald-500' : 'bg-zinc-600'} />
            {offline ? 'Server unreachable' : status ? 'Connected' : 'Connecting…'}
          </div>
        </div>
      </aside>
    </>
  )
}

function TopBar({ page, onMenu }: { page: Page; onMenu: () => void }) {
  const { status, refresh } = useStatus()
  const notify = useToast()
  const [confirm, setConfirm] = useState<'panic' | 'disarm' | null>(null)
  const [busy, setBusy] = useState(false)

  const run = async (fn: () => Promise<unknown>, success: string) => {
    setBusy(true)
    try {
      await fn()
      await refresh()
      notify(success, 'success')
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(false)
      setConfirm(null)
    }
  }

  const level = status?.threat_level ?? 0
  const lv = LEVELS[level]
  const schedule = status && describeSchedule(status.schedule, status.armed)?.short

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-zinc-800 px-4">
      <button type="button" className="-ml-1 rounded p-1.5 text-zinc-400 hover:bg-zinc-800 lg:hidden" onClick={onMenu} aria-label="Open menu">
        <Menu className="h-5 w-5" />
      </button>
      <h1 className="text-base font-semibold">{PAGE_TITLES[page]}</h1>

      <div className="ml-auto flex items-center gap-2">
        {status && (
          <>
            <span className={cx('inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium ring-1 ring-inset', lv.soft)}>
              <Dot className={lv.bg} />
              {level === 0 ? 'Clear' : <><span className="hidden sm:inline">Level {level} · </span>{lv.label}</>}
            </span>
            {schedule && (
              <a href={href('settings', 'schedule')} className="hidden text-xs text-zinc-500 hover:text-zinc-300 md:inline" title="Change the schedule">
                {schedule}
              </a>
            )}
            <Button
              size="sm"
              variant="secondary"
              disabled={busy}
              onClick={() => (status.armed ? setConfirm('disarm') : run(() => api.arm(true), 'System armed'))}
              icon={status.armed ? <ShieldAlert className="h-4 w-4 text-emerald-400" /> : <ShieldOff className="h-4 w-4 text-zinc-500" />}
              title={status.armed ? 'People on camera raise alarms. Click to disarm.' : 'Detections are ignored. Click to arm.'}
            >
              {status.armed ? 'Armed' : 'Disarmed'}
            </Button>
            <Button size="sm" variant="danger" disabled={busy} onClick={() => setConfirm('panic')}>
              Panic
            </Button>
          </>
        )}
      </div>

      <ConfirmDialog
        open={confirm === 'panic'}
        title="Raise the alarm?"
        message="This immediately goes to level 4: siren, recording, voice warning and owner alerts. It stays on until you reset it."
        confirmLabel="Raise alarm"
        busy={busy}
        onConfirm={() => run(api.panic, 'Alarm raised')}
        onCancel={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm === 'disarm'}
        title="Disarm the system?"
        message="People on camera will still be shown, but nothing will be recorded, spoken or alerted. Any running incident ends."
        confirmLabel="Disarm"
        busy={busy}
        onConfirm={() => run(() => api.arm(false), 'System disarmed')}
        onCancel={() => setConfirm(null)}
      />
    </header>
  )
}

function AlarmBanner() {
  const { status, offline, refresh } = useStatus()
  const notify = useToast()
  const [busy, setBusy] = useState(false)

  if (offline) {
    return (
      <div className="flex items-center gap-2 border-b border-red-900 bg-red-950/60 px-4 py-2 text-sm text-red-200">
        <BellOff className="h-4 w-4" />
        Cannot reach the Guardian server. Retrying…
      </div>
    )
  }
  if (!status || (status.threat_level < 3 && !status.panic)) return null

  const reset = async () => {
    setBusy(true)
    try {
      await api.resetAlarm()
      await refresh()
      notify('Alarm reset', 'success')
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(false)
    }
  }

  const alarming = status.cameras
    .filter((c) => c.threat_level >= 3 || c.manual_alarm)
    .sort((a, b) => b.threat_level - a.threat_level)
  const worst = alarming[0]
  const siren = status.siren.active || status.cameras.some((c) => c.siren_active)

  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-red-800 bg-red-700 px-4 py-2 text-sm font-medium text-white">
      <ShieldAlert className="h-4 w-4" />
      <span>
        {status.panic || !worst
          ? 'Panic alarm active'
          : `${LEVELS[worst.threat_level].label} at ${worst.name}: unrecognised person for ${formatDuration(worst.incident_seconds)}`}
        {!status.panic && alarming.length > 1 && ` (+${alarming.length - 1} more)`}
        {worst?.test && ' (test)'}
        {siren && ' · siren sounding'}
      </span>
      <Button size="sm" className="ml-auto" variant="inverse" loading={busy} onClick={reset}>
        Reset alarm
      </Button>
    </div>
  )
}

export default function Shell({ page, children }: { page: Page; children: ReactNode }) {
  const [menuOpen, setMenuOpen] = useState(false)
  return (
    <div className="flex h-full">
      <Sidebar page={page} open={menuOpen} onClose={() => setMenuOpen(false)} />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar page={page} onMenu={() => setMenuOpen(true)} />
        <AlarmBanner />
        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-[1400px] p-4 lg:p-6">{children}</div>
        </main>
      </div>
    </div>
  )
}
