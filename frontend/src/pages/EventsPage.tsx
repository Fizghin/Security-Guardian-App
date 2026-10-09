import { useEffect, useState } from 'react'
import { Download, ScrollText, Trash2 } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, SEVERITIES, type EventFilters, type SecurityEvent } from '../api'
import EventRow, { EventPicture } from '../components/EventRow'
import RecordingPlayer from '../components/RecordingPlayer'
import { Button, Card, ConfirmDialog, Empty, ErrorNote, Segmented } from '../components/ui'
import { eventLabel, formatDay, formatHour, SEVERITY_COLOR } from '../lib/format'
import { errorMessage, useToast } from '../lib/toast'
import { usePoll } from '../lib/usePoll'

const RANGES = [
  { value: 24, label: '24 hours' },
  { value: 168, label: '7 days' },
  { value: 720, label: '30 days' },
]
const PAGE_SIZE = 50

export default function EventsPage() {
  const notify = useToast()
  const [hours, setHours] = useState(24)
  const [type, setType] = useState('')
  const [severity, setSeverity] = useState('')
  const [camera, setCamera] = useState('')
  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  const [clip, setClip] = useState<string | null>(null)
  const [picture, setPicture] = useState<SecurityEvent | null>(null)
  const [confirmClear, setConfirmClear] = useState(false)
  const [clearing, setClearing] = useState(false)

  useEffect(() => {
    const t = setTimeout(() => {
      setSearch(searchInput.trim())
      setOffset(0)
    }, 300)
    return () => clearTimeout(t)
  }, [searchInput])

  const filters = (): EventFilters => ({
    type,
    severity,
    camera,
    search,
    since: new Date(Date.now() - hours * 3600_000).toISOString(),
  })

  const summary = usePoll(() => api.eventSummary(hours), 10000, [hours])
  const bySeverity = summary.data?.by_severity
  const events = usePoll(() => api.events(filters(), PAGE_SIZE, offset), 5000, [hours, type, severity, camera, search, offset])

  const bucket = summary.data?.bucket
  const chartData =
    summary.data?.series.map((b) => ({ ...b, label: bucket === 'hour' ? formatHour(b.start) : formatDay(b.start) })) ?? []

  const total = events.data?.total ?? 0
  const setFilter = (fn: () => void) => {
    fn()
    setOffset(0)
  }

  const clearLog = async () => {
    setClearing(true)
    try {
      const { deleted } = await api.clearEvents()
      notify(`Deleted ${deleted} events`, 'success')
      setConfirmClear(false)
      events.refresh()
      summary.refresh()
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setClearing(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Segmented value={hours} options={RANGES} onChange={(v) => setFilter(() => setHours(v))} />
        <select className="input w-auto" value={type} onChange={(e) => setFilter(() => setType(e.target.value))} aria-label="Event type">
          <option value="">All types</option>
          {summary.data?.types.map((t) => (
            <option key={t} value={t}>
              {eventLabel(t)}
            </option>
          ))}
        </select>
        {(summary.data?.cameras.length ?? 0) > 0 && (
          <select className="input w-auto" value={camera} onChange={(e) => setFilter(() => setCamera(e.target.value))} aria-label="Camera">
            <option value="">All cameras</option>
            {summary.data?.cameras.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        )}
        <select className="input w-auto" value={severity} onChange={(e) => setFilter(() => setSeverity(e.target.value))} aria-label="Severity">
          <option value="">All severities</option>
          {SEVERITIES.map((s) => (
            <option key={s} value={s}>
              {s.charAt(0) + s.slice(1).toLowerCase()}
            </option>
          ))}
        </select>
        <input
          className="input w-full sm:w-56"
          placeholder="Search descriptions"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
        />
        <div className="ml-auto flex gap-2">
          <a href={api.eventsCsvUrl(filters())}>
            <Button icon={<Download className="h-4 w-4" />}>Export CSV</Button>
          </a>
          <Button variant="ghost" icon={<Trash2 className="h-4 w-4" />} onClick={() => setConfirmClear(true)}>
            Clear log
          </Button>
        </div>
      </div>

      <Card
        title={`Activity in the last ${RANGES.find((r) => r.value === hours)?.label}`}
        actions={
          bySeverity && (
            <div className="flex flex-wrap gap-3 text-xs text-zinc-400">
              {SEVERITIES.map((s) => (
                <span key={s} className="flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-sm" style={{ background: SEVERITY_COLOR[s] }} />
                  {s.charAt(0) + s.slice(1).toLowerCase()}
                  <span className="tabular-nums text-zinc-200">{bySeverity[s]}</span>
                </span>
              ))}
            </div>
          )
        }
      >
        <div className="h-48">
          <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 800, height: 192 }}>
            <BarChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: -24 }}>
              <CartesianGrid vertical={false} stroke="#27272a" />
              <XAxis dataKey="label" tick={{ fill: '#71717a', fontSize: 11 }} tickLine={false} axisLine={{ stroke: '#3f3f46' }} minTickGap={24} />
              <YAxis allowDecimals={false} tick={{ fill: '#71717a', fontSize: 11 }} tickLine={false} axisLine={false} />
              <Tooltip
                cursor={{ fill: '#27272a' }}
                contentStyle={{ background: '#18181b', border: '1px solid #3f3f46', borderRadius: 6, fontSize: 12 }}
                labelStyle={{ color: '#e4e4e7' }}
              />
              {SEVERITIES.map((s) => (
                <Bar key={s} dataKey={s} stackId="a" fill={SEVERITY_COLOR[s]} name={s.charAt(0) + s.slice(1).toLowerCase()} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      </Card>

      <Card
        title="Event log"
        actions={
          total > 0 && (
            <div className="flex items-center gap-2 text-xs text-zinc-400">
              <span className="tabular-nums">
                {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}
              </span>
              <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                Newer
              </Button>
              <Button size="sm" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>
                Older
              </Button>
            </div>
          )
        }
        bodyClassName="p-0"
      >
        {events.error && (
          <div className="p-4">
            <ErrorNote>{events.error.message}</ErrorNote>
          </div>
        )}
        {events.data && events.data.items.length === 0 ? (
          <Empty icon={<ScrollText className="h-8 w-8" />} title="No matching events">
            Try a longer time range or clear the filters.
          </Empty>
        ) : (
          <ul className="divide-y divide-zinc-800/80">
            {events.data?.items.map((e) => <EventRow key={e.id} event={e} onOpenClip={setClip} onOpenPicture={setPicture} />)}
          </ul>
        )}
      </Card>

      <EventPicture event={picture} onClose={() => setPicture(null)} onOpenClip={setClip} />
      <RecordingPlayer key={clip ?? ''} file={clip} onClose={() => setClip(null)} />
      <ConfirmDialog
        open={confirmClear}
        title="Clear the event log?"
        message="All events are permanently deleted. Recordings are kept."
        confirmLabel="Delete all events"
        busy={clearing}
        onConfirm={clearLog}
        onCancel={() => setConfirmClear(false)}
      />
    </div>
  )
}
