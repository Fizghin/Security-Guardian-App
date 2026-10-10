import { useEffect, useState } from 'react'
import { BarChart3, Film, Lock, ScrollText, Settings as SettingsIcon, UserSearch, Users, Video } from 'lucide-react'

export type Page = 'live' | 'events' | 'recordings' | 'insights' | 'evidence' | 'insiders' | 'visitors' | 'settings'
const PAGES: Page[] = ['live', 'events', 'recordings', 'insights', 'evidence', 'insiders', 'visitors', 'settings']

export const PAGE_TITLES: Record<Page, string> = {
  live: 'Live',
  events: 'Events',
  recordings: 'Recordings',
  insights: 'Insights',
  evidence: 'Evidence vault',
  insiders: 'Insiders',
  visitors: 'Visitors',
  settings: 'Settings',
}

function parse(): { page: Page; section: string } {
  const [page, section = ''] = location.hash.replace(/^#\/?/, '').split('/')
  return { page: (PAGES as string[]).includes(page) ? (page as Page) : 'live', section }
}

/** Hash routing keeps deep links working when the backend serves the built files. */
export function useRoute() {
  const [route, setRoute] = useState(parse)
  useEffect(() => {
    const onChange = () => setRoute(parse())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}

export const href = (page: Page, section?: string) => `#/${page}${section ? `/${section}` : ''}`

export const SETTINGS_SECTIONS = [
  ['cameras', 'Cameras'],
  ['schedule', 'Schedule'],
  ['ai', 'Language model'],
  ['voice', 'Voice'],
  ['detection', 'Detection'],
  ['escalation', 'Escalation'],
  ['recording', 'Recording'],
  ['notifications', 'Notifications'],
  ['digest', 'Daily digest'],
  ['appearance', 'Appearance'],
  ['system', 'System'],
] as const

export const PAGE_SUBTITLES: Record<Page, string> = {
  live: 'Cameras, alarms and what is happening right now',
  events: 'Everything Guardian noticed, with pictures and clips',
  recordings: 'Incident clips, sealed as evidence the moment they are saved',
  insights: 'When and where people show up, and what is unusual',
  evidence: 'Proof that every clip is exactly as it was recorded',
  insiders: 'People who belong here and never raise the alarm',
  visitors: 'Strangers Guardian remembers, and when they come back',
  settings: 'Cameras, detection, alerts and the rest',
}

export const NAV: { group: string; items: { page: Page; label: string; icon: typeof Video }[] }[] = [
  {
    group: 'Monitor',
    items: [
      { page: 'live', label: 'Live', icon: Video },
      { page: 'events', label: 'Events', icon: ScrollText },
      { page: 'recordings', label: 'Recordings', icon: Film },
    ],
  },
  {
    group: 'Intelligence',
    items: [
      { page: 'insights', label: 'Insights', icon: BarChart3 },
      { page: 'evidence', label: 'Evidence vault', icon: Lock },
    ],
  },
  {
    group: 'People',
    items: [
      { page: 'insiders', label: 'Insiders', icon: Users },
      { page: 'visitors', label: 'Visitors', icon: UserSearch },
    ],
  },
  { group: 'System', items: [{ page: 'settings', label: 'Settings', icon: SettingsIcon }] },
]

/** Go-to shortcuts: press g, then the letter. */
export const GO_KEYS: Record<string, Page> = {
  l: 'live',
  e: 'events',
  r: 'recordings',
  i: 'insights',
  v: 'evidence',
  p: 'insiders',
  s: 'visitors',
  ',': 'settings',
}
