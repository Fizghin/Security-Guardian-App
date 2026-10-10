import { useEffect, useState } from 'react'

export type Page = 'live' | 'map' | 'events' | 'recordings' | 'insiders' | 'visitors' | 'settings'
const PAGES: Page[] = ['live', 'map', 'events', 'recordings', 'insiders', 'visitors', 'settings']

export const PAGE_TITLES: Record<Page, string> = {
  live: 'Live',
  map: 'Map',
  events: 'Events',
  recordings: 'Recordings',
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
