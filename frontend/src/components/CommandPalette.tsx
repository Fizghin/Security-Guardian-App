import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  ArrowRight,
  Bell,
  CornerDownLeft,
  Monitor,
  Moon,
  Search,
  Send,
  Settings as SettingsIcon,
  ShieldAlert,
  ShieldCheck,
  ShieldOff,
  Siren,
  Sun,
  Video,
} from 'lucide-react'
import { api } from '../api'
import { cx } from '../lib/cx'
import { GO_KEYS, href, NAV, PAGE_TITLES, SETTINGS_SECTIONS, type Page } from '../lib/route'
import { useStatus } from '../lib/status'
import type { ThemePref } from '../lib/theme'
import { errorMessage, useToast } from '../lib/toast'

interface Command {
  id: string
  label: string
  group: string
  icon: ReactNode
  hint?: string
  keywords?: string
  run: () => void | Promise<void>
}

const GO_LETTER = Object.fromEntries(Object.entries(GO_KEYS).map(([k, p]) => [p, k])) as Record<Page, string>

const go = (to: string) => {
  location.hash = to
}

function score(cmd: Command, q: string): number {
  if (!q) return 1
  const hay = `${cmd.label} ${cmd.group} ${cmd.keywords ?? ''}`.toLowerCase()
  const words = q.toLowerCase().split(/\s+/).filter(Boolean)
  if (!words.every((w) => hay.includes(w))) return 0
  return cmd.label.toLowerCase().startsWith(words[0]) ? 3 : 2
}

interface Props {
  onClose: () => void
  onTheme: (t: ThemePref) => void
  onPanic: () => void
}

/** Mounted only while open, so every opening starts with an empty search. */
export default function CommandPalette({ open, ...props }: Props & { open: boolean }) {
  return open ? <Palette {...props} /> : null
}

function Palette({ onClose, onTheme, onPanic }: Props) {
  const { status, refresh } = useStatus()
  const notify = useToast()
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const listRef = useRef<HTMLUListElement>(null)

  const commands = useMemo<Command[]>(() => {
    const action = (fn: () => Promise<unknown>, done: string) => async () => {
      try {
        await fn()
        refresh()
        notify(done, 'success')
      } catch (err) {
        notify(errorMessage(err), 'error')
      }
    }
    const list: Command[] = NAV.flatMap((g) =>
      g.items.map((p) => ({
        id: `page:${p.page}`,
        label: PAGE_TITLES[p.page],
        group: 'Go to',
        icon: <p.icon className="h-4 w-4" />,
        hint: `G ${GO_LETTER[p.page].toUpperCase()}`,
        run: () => go(href(p.page)),
      })),
    )
    status?.cameras.forEach((c) =>
      list.push({ id: `cam:${c.id}`, label: c.name, group: 'Cameras', icon: <Video className="h-4 w-4" />, keywords: 'camera live view', run: () => go(href('live')) }),
    )
    if (status) {
      list.push(
        status.armed
          ? { id: 'disarm', label: 'Disarm the system', group: 'Actions', icon: <ShieldOff className="h-4 w-4" />, keywords: 'off stop', run: action(() => api.arm(false), 'System disarmed') }
          : { id: 'arm', label: 'Arm the system', group: 'Actions', icon: <ShieldCheck className="h-4 w-4" />, keywords: 'on start', run: action(() => api.arm(true), 'System armed') },
      )
      if (status.threat_level >= 3 || status.panic) {
        list.push({ id: 'reset', label: 'Reset the alarm', group: 'Actions', icon: <ShieldAlert className="h-4 w-4" />, keywords: 'stop siren', run: action(api.resetAlarm, 'Alarm reset') })
      }
      list.push({ id: 'panic', label: 'Raise the panic alarm…', group: 'Actions', icon: <Siren className="h-4 w-4 text-red-400" />, keywords: 'siren emergency', run: onPanic })
    }
    list.push(
      { id: 'digest', label: 'Send the daily digest now', group: 'Actions', icon: <Send className="h-4 w-4" />, keywords: 'summary report notification', run: action(api.sendDigest, 'Digest sent') },
      {
        id: 'audit',
        label: 'Verify all clips',
        group: 'Actions',
        icon: <ShieldCheck className="h-4 w-4" />,
        keywords: 'evidence integrity audit vault',
        run: async () => {
          try {
            const r = await api.evidenceAudit()
            notify(r.ok ? `All ${r.clips.length} sealed clips are intact` : 'Some clips failed the check: see Evidence vault', r.ok ? 'success' : 'error')
            if (!r.ok) go(href('evidence'))
          } catch (err) {
            notify(errorMessage(err), 'error')
          }
        },
      },
      { id: 'alerts', label: 'Browser alerts', group: 'Actions', icon: <Bell className="h-4 w-4" />, keywords: 'notifications sound chime', run: () => go(href('settings', 'appearance')) },
      { id: 'theme:light', label: 'Light theme', group: 'Theme', icon: <Sun className="h-4 w-4" />, keywords: 'appearance', run: () => onTheme('light') },
      { id: 'theme:dark', label: 'Dark theme', group: 'Theme', icon: <Moon className="h-4 w-4" />, keywords: 'appearance', run: () => onTheme('dark') },
      { id: 'theme:system', label: 'Match the system theme', group: 'Theme', icon: <Monitor className="h-4 w-4" />, keywords: 'appearance auto', run: () => onTheme('system') },
    )
    SETTINGS_SECTIONS.forEach(([id, label]) =>
      list.push({ id: `settings:${id}`, label, group: 'Settings', icon: <SettingsIcon className="h-4 w-4" />, keywords: 'settings', run: () => go(href('settings', id)) }),
    )
    return list
  }, [status, refresh, notify, onPanic, onTheme])

  const results = useMemo(
    () =>
      commands
        .map((c) => ({ c, s: score(c, query.trim()) }))
        .filter((r) => r.s > 0)
        .sort((a, b) => b.s - a.s)
        .map((r) => r.c)
        .slice(0, 40),
    [commands, query],
  )

  useEffect(() => {
    listRef.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [active])

  const run = (cmd: Command | undefined) => {
    if (!cmd) return
    onClose()
    void cmd.run()
  }

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setActive((a) => Math.min(results.length - 1, a + 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((a) => Math.max(0, a - 1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      run(results[active])
    } else if (e.key === 'Escape') {
      onClose()
    }
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center bg-black/60 p-4 pt-[12vh] backdrop-blur-sm" onMouseDown={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="w-full max-w-xl animate-scale-in overflow-hidden rounded-2xl border border-zinc-700 bg-zinc-900 shadow-2xl shadow-black/40"
        onMouseDown={(e) => e.stopPropagation()}
        onKeyDown={onKey}
      >
        <div className="flex items-center gap-3 border-b border-zinc-800 px-4">
          <Search className="h-4 w-4 shrink-0 text-zinc-500" />
          <input
            autoFocus
            value={query}
            onChange={(e) => {
              setQuery(e.target.value)
              setActive(0)
            }}
            placeholder="Search pages, cameras, actions…"
            className="h-12 w-full bg-transparent text-sm text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus-visible:ring-0"
            role="combobox"
            aria-expanded="true"
            aria-controls="palette-list"
            aria-activedescendant={results[active] ? `cmd-${results[active].id}` : undefined}
          />
          <span className="kbd">Esc</span>
        </div>
        <ul ref={listRef} id="palette-list" role="listbox" className="max-h-[50vh] overflow-y-auto p-2">
          {results.length === 0 && <li className="px-3 py-8 text-center text-sm text-zinc-500">Nothing matches “{query}”.</li>}
          {results.map((cmd, i) => {
            const header = !query && (i === 0 || results[i - 1].group !== cmd.group)
            return (
              <li key={cmd.id}>
                {header && <div className="px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">{cmd.group}</div>}
                <button
                  type="button"
                  id={`cmd-${cmd.id}`}
                  data-index={i}
                  role="option"
                  aria-selected={i === active}
                  onMouseMove={() => setActive(i)}
                  onClick={() => run(cmd)}
                  className={cx(
                    'flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm',
                    i === active ? 'bg-blue-600 text-white' : 'text-zinc-200',
                  )}
                >
                  <span className={cx(i === active ? 'text-white' : 'text-zinc-400')}>{cmd.icon}</span>
                  <span className="flex-1 truncate">{cmd.label}</span>
                  {query && <span className={cx('text-xs', i === active ? 'text-white/75' : 'text-zinc-500')}>{cmd.group}</span>}
                  {cmd.hint && !query && <span className={cx('font-mono text-[10px]', i === active ? 'text-white/75' : 'text-zinc-500')}>{cmd.hint}</span>}
                  {i === active && (cmd.group === 'Go to' || cmd.group === 'Settings' ? <ArrowRight className="h-3.5 w-3.5" /> : <CornerDownLeft className="h-3.5 w-3.5" />)}
                </button>
              </li>
            )
          })}
        </ul>
        <div className="flex items-center gap-4 border-t border-zinc-800 px-4 py-2 text-[11px] text-zinc-500">
          <span className="flex items-center gap-1">
            <span className="kbd">↑</span>
            <span className="kbd">↓</span> move
          </span>
          <span className="flex items-center gap-1">
            <span className="kbd">↵</span> run
          </span>
          <span className="ml-auto flex items-center gap-1">
            <span className="kbd">G</span> then a letter jumps to a page
          </span>
        </div>
      </div>
    </div>
  )
}
