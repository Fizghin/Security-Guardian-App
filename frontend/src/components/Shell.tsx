import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { Bell, BellOff, BellRing, ChevronsLeft, ChevronsRight, Menu, Monitor, Moon, MoreHorizontal, Search, Shield, ShieldAlert, ShieldOff, Sun, X } from 'lucide-react'
import { api } from '../api'
import { useAlertPrefs } from '../lib/alerts'
import { cx } from '../lib/cx'
import { describeSchedule, formatBytes, formatDuration, LEVELS } from '../lib/format'
import { GO_KEYS, href, NAV, PAGE_SUBTITLES, PAGE_TITLES, type Page } from '../lib/route'
import { useStatus } from '../lib/status'
import { local } from '../lib/storage'
import { useThemeContext, type ThemePref } from '../lib/theme'
import { errorMessage, useToast } from '../lib/toast'
import { usePoll } from '../lib/usePoll'
import { AlertControls } from './AlertControls'
import CommandPalette from './CommandPalette'
import { Button, ConfirmDialog, Dot } from './ui'

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)
const MOD = isMac ? '⌘' : 'Ctrl'

function Meter({ label, value, detail }: { label: string; value: number; detail: string }) {
  return (
    <div>
      <div className="mb-1 flex justify-between text-[11px] text-zinc-500">
        <span>{label}</span>
        <span className="tabular-nums text-zinc-400">{detail}</span>
      </div>
      <div className="h-1 overflow-hidden rounded-full bg-zinc-800">
        <div
          className={cx('h-full rounded-full transition-[width] duration-700', value > 85 ? 'bg-red-500' : value > 65 ? 'bg-amber-500' : 'bg-blue-500')}
          style={{ width: `${Math.min(100, value)}%` }}
        />
      </div>
    </div>
  )
}

function SystemCard({ collapsed }: { collapsed: boolean }) {
  const { status } = useStatus()
  if (!status) return null
  const level = status.threat_level
  const alarm = level >= 3 || status.panic
  const live = status.cameras.filter((c) => c.connected).length
  const recording = status.cameras.filter((c) => c.recording.active).length
  const dot = (
    <span className="relative flex h-2.5 w-2.5">
      {status.armed && <span className={cx('absolute inline-flex h-full w-full animate-ping rounded-full opacity-60', LEVELS[level].bg)} />}
      <span className={cx('relative inline-flex h-2.5 w-2.5 rounded-full', status.armed ? LEVELS[level].bg : 'bg-zinc-600')} />
    </span>
  )
  const label = status.panic ? 'Panic alarm' : status.armed ? (level ? LEVELS[level].label : 'Armed · all clear') : 'Disarmed'
  if (collapsed) {
    return (
      <div className="mx-auto mt-3 flex h-10 w-10 items-center justify-center rounded-xl border border-zinc-800 bg-zinc-900/60" title={label}>
        {dot}
      </div>
    )
  }
  return (
    <div
      className={cx(
        'mx-3 mt-3 rounded-xl border p-3',
        alarm ? 'border-red-800 bg-red-950/60' : status.armed ? 'border-emerald-900/70 bg-gradient-to-br from-emerald-950/60 to-zinc-900/40' : 'border-zinc-800 bg-zinc-900/60',
      )}
    >
      <div className="flex items-center gap-2">
        {dot}
        <span className="truncate text-sm font-semibold text-zinc-100">{label}</span>
      </div>
      <div className="mt-1.5 text-xs text-zinc-400">
        {live} of {status.cameras.length} camera{status.cameras.length === 1 ? '' : 's'} live
        {recording > 0 && <span className="text-red-300"> · {recording} recording</span>}
      </div>
    </div>
  )
}

function Sidebar({
  page,
  open,
  collapsed,
  onClose,
  onToggleCollapse,
  onSearch,
}: {
  page: Page
  open: boolean
  collapsed: boolean
  onClose: () => void
  onToggleCollapse: () => void
  onSearch: () => void
}) {
  const { status, offline } = useStatus()
  const { data: sys } = usePoll(api.system, 5000)
  // The drawer on small screens always shows labels
  const rail = collapsed && !open

  return (
    <>
      <div className={cx('fixed inset-0 z-30 bg-black/60 backdrop-blur-sm lg:hidden', open ? 'block' : 'hidden')} onClick={onClose} />
      <aside
        className={cx(
          'fixed inset-y-0 left-0 z-40 flex flex-col border-r border-zinc-800 bg-zinc-950 transition-[transform,width] duration-200 lg:static lg:translate-x-0',
          rail ? 'w-[72px]' : 'w-64',
          open ? 'translate-x-0' : '-translate-x-full',
        )}
      >
        <div className={cx('flex h-16 shrink-0 items-center border-b border-zinc-800', rail ? 'justify-center' : 'justify-between px-4')}>
          <a href={href('live')} className="flex items-center gap-2.5 font-semibold tracking-tight">
            <span className="flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-blue-500 to-indigo-600 shadow-lg shadow-blue-900/30">
              <Shield className="h-4 w-4 text-white" />
            </span>
            {!rail && (
              <span className="leading-tight">
                Guardian
                <span className="block text-[10px] font-medium uppercase tracking-widest text-zinc-500">Home security</span>
              </span>
            )}
          </a>
          {!rail && (
            <button type="button" className="rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-800 lg:hidden" onClick={onClose} aria-label="Close menu">
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        <button
          type="button"
          onClick={onSearch}
          className={cx(
            'mt-3 flex items-center gap-2 rounded-xl border border-zinc-800 bg-zinc-900/60 text-sm text-zinc-500 transition-colors hover:border-zinc-700 hover:text-zinc-300',
            rail ? 'mx-auto h-10 w-10 justify-center' : 'mx-3 h-10 px-3',
          )}
          title={`Search and commands (${MOD}+K)`}
        >
          <Search className="h-4 w-4" />
          {!rail && (
            <>
              <span className="flex-1 text-left">Search…</span>
              <span className="kbd">{MOD} K</span>
            </>
          )}
        </button>

        <SystemCard collapsed={rail} />

        <nav className={cx('flex-1 space-y-4 overflow-y-auto py-3', rail ? 'px-2' : 'px-3')}>
          {NAV.map(({ group, items }) => (
            <div key={group}>
              {rail ? (
                <div className="mx-auto mb-1 h-px w-6 bg-zinc-800" />
              ) : (
                <div className="mb-1 px-2.5 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">{group}</div>
              )}
              <div className="space-y-0.5">
                {items.map(({ page: p, label, icon: Icon }) => {
                  const current = p === page
                  const alarmDot = p === 'live' && status && (status.threat_level >= 3 || status.panic)
                  return (
                    <a
                      key={p}
                      href={href(p)}
                      onClick={onClose}
                      title={rail ? label : undefined}
                      aria-current={current ? 'page' : undefined}
                      className={cx(
                        'relative flex items-center rounded-xl text-sm transition-colors',
                        rail ? 'h-10 justify-center' : 'gap-3 px-3 py-2',
                        current ? 'bg-blue-500/10 font-medium text-zinc-50' : 'text-zinc-400 hover:bg-zinc-900 hover:text-zinc-100',
                      )}
                    >
                      {current && <span className="absolute inset-y-2 left-0 w-[3px] rounded-full bg-blue-500" />}
                      <Icon className={cx('h-[18px] w-[18px] shrink-0', current && 'text-blue-400')} />
                      {!rail && label}
                      {alarmDot && <span className={cx('h-2 w-2 animate-pulse rounded-full bg-red-500', rail ? 'absolute right-2 top-2' : 'ml-auto')} />}
                    </a>
                  )
                })}
              </div>
            </div>
          ))}
        </nav>

        <div className={cx('space-y-3 border-t border-zinc-800', rail ? 'p-2' : 'p-4')}>
          {sys && !rail && (
            <>
              <Meter label="CPU" value={sys.cpu_percent} detail={`${Math.round(sys.cpu_percent)}%`} />
              <Meter label="Memory" value={sys.memory_percent} detail={`${formatBytes(sys.memory_used)} / ${formatBytes(sys.memory_total)}`} />
              <Meter label="Disk" value={100 - (sys.disk_free / sys.disk_total) * 100} detail={`${formatBytes(sys.disk_free)} free`} />
            </>
          )}
          <div className={cx('flex items-center gap-2 text-xs text-zinc-400', rail && 'justify-center')} title={offline ? 'Server unreachable' : 'Connected'}>
            <Dot className={offline ? 'bg-red-500' : status ? 'bg-emerald-500' : 'bg-zinc-600'} />
            {!rail && (offline ? 'Server unreachable' : status ? 'Connected' : 'Connecting…')}
            <button
              type="button"
              onClick={onToggleCollapse}
              className={cx('hidden rounded-lg p-1 text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200 lg:block', !rail && 'ml-auto')}
              aria-label={rail ? 'Expand sidebar' : 'Collapse sidebar'}
              title={rail ? 'Expand sidebar' : 'Collapse sidebar'}
            >
              {rail ? <ChevronsRight className="h-4 w-4" /> : <ChevronsLeft className="h-4 w-4" />}
            </button>
          </div>
        </div>
      </aside>
    </>
  )
}

const THEME_NEXT: Record<ThemePref, ThemePref> = { system: 'light', light: 'dark', dark: 'system' }
const THEME_ICON = { system: Monitor, light: Sun, dark: Moon }

function AlertsPopover() {
  const prefs = useAlertPrefs()
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false)
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', esc)
    }
  }, [open])
  const on = prefs.notify || prefs.sound
  const Icon = on ? BellRing : Bell
  return (
    <div ref={box} className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className={cx('flex h-9 w-9 items-center justify-center rounded-xl text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100', on && 'text-blue-400')}
        aria-label="Browser alerts"
        aria-expanded={open}
        title="Browser alerts"
      >
        <Icon className="h-[18px] w-[18px]" />
      </button>
      {open && (
        <div className="absolute right-0 top-11 z-50 w-80 animate-scale-in rounded-2xl border border-zinc-700 bg-zinc-900 p-4 shadow-2xl shadow-black/40">
          <h3 className="mb-3 text-sm font-semibold text-zinc-100">Alerts in this browser</h3>
          <AlertControls />
        </div>
      )}
    </div>
  )
}

function TopBar({ page, onMenu, onSearch, confirm, setConfirm }: { page: Page; onMenu: () => void; onSearch: () => void; confirm: 'panic' | 'disarm' | null; setConfirm: (c: 'panic' | 'disarm' | null) => void }) {
  const { status, refresh } = useStatus()
  const theme = useThemeContext()
  const notify = useToast()
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
  const ThemeIcon = THEME_ICON[theme.pref]

  return (
    <header className="glass sticky top-0 z-20 flex h-16 shrink-0 items-center gap-3 border-b px-4 lg:px-6">
      <button type="button" className="-ml-1 rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-800 lg:hidden" onClick={onMenu} aria-label="Open menu">
        <Menu className="h-5 w-5" />
      </button>
      <div className="min-w-0">
        <h1 className="truncate text-base font-semibold tracking-tight text-zinc-50">{PAGE_TITLES[page]}</h1>
        <p className="hidden truncate text-xs text-zinc-500 md:block">{PAGE_SUBTITLES[page]}</p>
      </div>

      <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
        {status && (
          <span className={cx('hidden items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset sm:inline-flex', lv.soft)}>
            <Dot className={lv.bg} />
            {level === 0 ? 'Clear' : <><span className="hidden md:inline">Level {level} · </span>{lv.label}</>}
          </span>
        )}
        {schedule && (
          <a href={href('settings', 'schedule')} className="hidden text-xs text-zinc-500 hover:text-zinc-300 xl:inline" title="Change the schedule">
            {schedule}
          </a>
        )}
        <button type="button" onClick={onSearch} className="flex h-9 w-9 items-center justify-center rounded-xl text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100 lg:hidden" aria-label="Search">
          <Search className="h-[18px] w-[18px]" />
        </button>
        <button
          type="button"
          onClick={() => theme.choose(THEME_NEXT[theme.pref])}
          className="hidden h-9 w-9 items-center justify-center rounded-xl text-zinc-400 hover:bg-zinc-800 hover:text-zinc-100 sm:flex"
          aria-label={`Theme: ${theme.pref}. Click to change.`}
          title={`Theme: ${theme.pref} (click to change)`}
        >
          <ThemeIcon className="h-[18px] w-[18px]" />
        </button>
        <AlertsPopover />
        {status && (
          <>
            <Button
              size="sm"
              variant="secondary"
              disabled={busy}
              className="rounded-xl"
              onClick={() => (status.armed ? setConfirm('disarm') : run(() => api.arm(true), 'System armed'))}
              icon={status.armed ? <ShieldAlert className="h-4 w-4 text-emerald-400" /> : <ShieldOff className="h-4 w-4 text-zinc-500" />}
              title={status.armed ? 'People on camera raise alarms. Click to disarm.' : 'Detections are ignored. Click to arm.'}
            >
              <span className="hidden sm:inline">{status.armed ? 'Armed' : 'Disarmed'}</span>
            </Button>
            <Button size="sm" variant="danger" className="rounded-xl" disabled={busy} onClick={() => setConfirm('panic')}>
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

  const alarming = status.cameras.filter((c) => c.threat_level >= 3 || c.manual_alarm).sort((a, b) => b.threat_level - a.threat_level)
  const worst = alarming[0]
  const siren = status.siren.active || status.cameras.some((c) => c.siren_active)

  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-red-800 bg-gradient-to-r from-red-700 to-red-600 px-4 py-2 text-sm font-medium text-white lg:px-6">
      <ShieldAlert className="h-4 w-4 animate-pulse" />
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

/** Phones: the main pages one tap away at the bottom of the screen. */
function TabBar({ page, onMore }: { page: Page; onMore: () => void }) {
  const { status } = useStatus()
  const tabs = NAV.flatMap((g) => g.items).filter((i) => ['live', 'events', 'recordings', 'insights'].includes(i.page))
  const inMore = !tabs.some((t) => t.page === page)
  return (
    <nav className="glass fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t pb-[env(safe-area-inset-bottom)] lg:hidden">
      {tabs.map(({ page: p, label, icon: Icon }) => (
        <a key={p} href={href(p)} aria-current={p === page ? 'page' : undefined} className={cx('relative flex flex-col items-center gap-0.5 py-2 text-[11px] font-medium', p === page ? 'text-blue-400' : 'text-zinc-500')}>
          <Icon className="h-5 w-5" />
          {label}
          {p === 'live' && status && (status.threat_level >= 3 || status.panic) && <span className="absolute right-[30%] top-1.5 h-2 w-2 animate-pulse rounded-full bg-red-500" />}
        </a>
      ))}
      <button type="button" onClick={onMore} className={cx('flex flex-col items-center gap-0.5 py-2 text-[11px] font-medium', inMore ? 'text-blue-400' : 'text-zinc-500')}>
        <MoreHorizontal className="h-5 w-5" />
        More
      </button>
    </nav>
  )
}

const typing = (el: EventTarget | null) =>
  el instanceof HTMLElement && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName))

export default function Shell({ page, children }: { page: Page; children: ReactNode }) {
  const theme = useThemeContext()
  const [menuOpen, setMenuOpen] = useState(false)
  const [palette, setPalette] = useState(false)
  const [confirm, setConfirm] = useState<'panic' | 'disarm' | null>(null)
  const [collapsed, setCollapsed] = useState(() => local.get('guardian.sidebar') === 'rail')
  const goPending = useRef(0)

  const toggleCollapse = () => {
    local.set('guardian.sidebar', collapsed ? 'full' : 'rail')
    setCollapsed(!collapsed)
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPalette((p) => !p)
        return
      }
      if (e.metaKey || e.ctrlKey || e.altKey || typing(e.target) || palette) return
      if (e.key === '/') {
        e.preventDefault()
        setPalette(true)
      } else if (e.key === 'g') {
        goPending.current = Date.now()
      } else if (Date.now() - goPending.current < 1200 && GO_KEYS[e.key]) {
        goPending.current = 0
        location.hash = href(GO_KEYS[e.key])
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [palette])

  const closePalette = useCallback(() => setPalette(false), [])
  const panic = useCallback(() => setConfirm('panic'), [])

  return (
    <div className="flex h-full">
      <Sidebar
        page={page}
        open={menuOpen}
        collapsed={collapsed}
        onClose={() => setMenuOpen(false)}
        onToggleCollapse={toggleCollapse}
        onSearch={() => {
          setMenuOpen(false)
          setPalette(true)
        }}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar page={page} onMenu={() => setMenuOpen(true)} onSearch={() => setPalette(true)} confirm={confirm} setConfirm={setConfirm} />
        <AlarmBanner />
        <main className="flex-1 overflow-y-auto pb-20 lg:pb-0">
          <div key={page} className="mx-auto max-w-[1440px] animate-fade-in p-4 lg:p-6">
            {children}
          </div>
        </main>
      </div>
      <TabBar page={page} onMore={() => setMenuOpen(true)} />
      <CommandPalette open={palette} onClose={closePalette} onTheme={theme.choose} onPanic={panic} />
    </div>
  )
}
