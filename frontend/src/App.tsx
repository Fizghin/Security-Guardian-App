import { lazy, Suspense, useEffect, useMemo } from 'react'
import { api } from './api'
import Shell from './components/Shell'
import ToastProvider from './components/ToastProvider'
import { PAGE_TITLES, useRoute } from './lib/route'
import { StatusContext } from './lib/status'
import { usePoll } from './lib/usePoll'
import InsidersPage from './pages/InsidersPage'
import LivePage from './pages/LivePage'
import RecordingsPage from './pages/RecordingsPage'
import SettingsPage from './pages/SettingsPage'

// The chart library is large; load it only when the Events page is opened.
const EventsPage = lazy(() => import('./pages/EventsPage'))

export default function App() {
  const { page, section } = useRoute()
  const { data, error, refresh } = usePoll(api.status, 1000)
  const statusValue = useMemo(() => ({ status: data, offline: !!error, refresh }), [data, error, refresh])

  const alarm = !!data && (data.threat_level >= 3 || data.panic)
  useEffect(() => {
    document.title = `${alarm ? '(!) Alarm · ' : ''}${PAGE_TITLES[page]} · Guardian`
  }, [alarm, page])

  return (
    <ToastProvider>
      <StatusContext.Provider value={statusValue}>
        <Shell page={page}>
          {page === 'live' && <LivePage />}
          {page === 'events' && (
            <Suspense fallback={<p className="text-sm text-zinc-500">Loading…</p>}>
              <EventsPage />
            </Suspense>
          )}
          {page === 'recordings' && <RecordingsPage />}
          {page === 'insiders' && <InsidersPage />}
          {page === 'settings' && <SettingsPage section={section} />}
        </Shell>
      </StatusContext.Provider>
    </ToastProvider>
  )
}
