import { useState, type FormEvent, type ReactNode } from 'react'
import { FlaskConical, Megaphone } from 'lucide-react'
import { api, type Status } from '../api'
import EventRow from '../components/EventRow'
import LiveVideo from '../components/LiveVideo'
import RecordingPlayer from '../components/RecordingPlayer'
import { Button, Card, Empty } from '../components/ui'
import { cx } from '../lib/cx'
import { formatDuration, LEVELS, timeAgo } from '../lib/format'
import { href } from '../lib/route'
import { useStatus } from '../lib/status'
import { errorMessage, useToast } from '../lib/toast'
import { usePoll } from '../lib/usePoll'

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 py-2 text-sm">
      <span className="shrink-0 text-zinc-500">{label}</span>
      <span className="min-w-0 text-right text-zinc-200">{children}</span>
    </div>
  )
}

function ThreatCard({ status }: { status: Status }) {
  const level = status.threat_level
  const lv = LEVELS[level]
  const now = status.server_time
  const voiceSource =
    status.last_message_source === 'llm'
      ? `via ${status.ai.model ?? 'model'}`
      : status.last_message_source === 'operator'
        ? 'typed by you'
        : status.last_message_source === 'fallback'
          ? 'pre-written line (model unavailable)'
          : ''

  return (
    <Card title="Status">
      <div className="flex items-center gap-3">
        <span className={cx('h-3 w-3 rounded-full', lv.bg)} />
        <div>
          <div className={cx('text-lg font-semibold leading-tight', lv.text)}>{level === 0 ? 'Clear' : lv.label}</div>
          <div className="text-xs text-zinc-500">
            {!status.armed
              ? 'Disarmed: detections are ignored'
              : level === 0
                ? 'Watching for unrecognised people'
                : status.manual_alarm
                  ? 'Panic alarm, reset to stand down'
                  : `Level ${level} of 4${status.test ? ' (test)' : ''}`}
          </div>
        </div>
      </div>
      <div className="mt-3 grid grid-cols-4 gap-1" aria-label={`Threat level ${level} of 4`}>
        {[1, 2, 3, 4].map((n) => (
          <div key={n} className={cx('h-1.5 rounded-full', n <= level ? LEVELS[n].bg : 'bg-zinc-800')} />
        ))}
      </div>

      <div className="mt-3 divide-y divide-zinc-800/80">
        <Row label="On camera">
          {status.persons === 0 ? (
            <span className="text-zinc-400">Nobody</span>
          ) : (
            <>
              {status.persons} {status.persons === 1 ? 'person' : 'people'}
              {status.insiders_in_view.length > 0 && (
                <span className="block text-xs text-emerald-400">Recognised: {status.insiders_in_view.join(', ')}</span>
              )}
            </>
          )}
        </Row>
        <Row label="Incident">{status.incident_started ? formatDuration(status.incident_seconds) : <span className="text-zinc-400">None</span>}</Row>
        <Row label="Recording">
          {status.recording.active ? (
            <span className="text-red-400">{status.recording.stopping ? 'Finishing clip…' : 'Recording'}</span>
          ) : (
            <span className="text-zinc-400">Idle</span>
          )}
        </Row>
        <Row label="Siren">
          {status.siren.active ? (
            <span className="text-red-400">Sounding</span>
          ) : status.siren.available ? (
            <span className="text-zinc-400">Off</span>
          ) : (
            <span className="text-amber-400">No audio player</span>
          )}
        </Row>
        <Row label="Voice">
          {status.voice.available === false ? (
            <span className="text-amber-400">No speech engine</span>
          ) : status.voice.speaking ? (
            'Speaking…'
          ) : status.ai.busy ? (
            'Writing warning…'
          ) : status.voice.error ? (
            <span className="text-amber-400" title={status.voice.error}>
              Speaker error
            </span>
          ) : (
            <span className="text-zinc-400">Ready</span>
          )}
        </Row>
      </div>

      {status.last_message && status.last_message_time && (
        <div className="mt-3 rounded-md border border-zinc-800 bg-zinc-950/60 p-3">
          <p className="text-sm text-zinc-200">“{status.last_message}”</p>
          <p className="mt-1 text-xs text-zinc-500">
            {timeAgo(status.last_message_time, now)}
            {voiceSource && ` · ${voiceSource}`}
          </p>
        </div>
      )}
    </Card>
  )
}

function Controls({ status }: { status: Status }) {
  const notify = useToast()
  const { refresh } = useStatus()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState<'test' | 'speak' | null>(null)
  const testing = status.pipeline.test_seconds_left > 0
  const voiceMissing = status.voice.available === false

  const startTest = async () => {
    setBusy('test')
    try {
      await api.testIntrusion(30)
      await refresh()
      notify('Test started: a simulated person is added to the feed for 30 seconds', 'success')
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(null)
    }
  }

  const speak = async (e: FormEvent) => {
    e.preventDefault()
    if (!text.trim()) return
    setBusy('speak')
    try {
      await api.speak(text.trim())
      setText('')
      notify('Speaking through the server speakers', 'success')
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(null)
    }
  }

  return (
    <Card title="Controls" bodyClassName="grid gap-4 md:grid-cols-[1fr_auto] md:items-end">
      <form onSubmit={speak}>
        <label className="label" htmlFor="speak-text">
          Talk through the speaker
        </label>
        <div className="flex gap-2">
          <input
            id="speak-text"
            className="input"
            maxLength={300}
            placeholder={voiceMissing ? 'No speech engine on the server' : 'e.g. Can I help you? The owner is on the way.'}
            value={text}
            disabled={voiceMissing}
            onChange={(e) => setText(e.target.value)}
          />
          <Button type="submit" icon={<Megaphone className="h-4 w-4" />} loading={busy === 'speak'} disabled={voiceMissing || !text.trim()}>
            Speak
          </Button>
        </div>
      </form>
      <div>
        <div className="label">Check the whole chain</div>
        <Button
          icon={<FlaskConical className="h-4 w-4" />}
          loading={busy === 'test'}
          disabled={testing || !status.camera.connected || !status.armed}
          onClick={startTest}
          title={!status.armed ? 'Arm the system first' : !status.camera.connected ? 'Needs a working camera' : undefined}
        >
          {testing ? `Test running · ${status.pipeline.test_seconds_left}s` : 'Run test intrusion'}
        </Button>
      </div>
    </Card>
  )
}

export default function LivePage() {
  const { status } = useStatus()
  const { data: events } = usePoll(() => api.events({}, 12), 3000)
  const [clip, setClip] = useState<string | null>(null)

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
      <div className="space-y-4">
        <LiveVideo cameraName={status?.camera.name ?? 'Camera'} />
        {status && <Controls status={status} />}
      </div>

      <div className="space-y-4">
        {status && <ThreatCard status={status} />}
        <Card
          title="Recent activity"
          actions={
            <a href={href('events')} className="text-xs font-medium text-blue-400 hover:text-blue-300">
              All events
            </a>
          }
          bodyClassName="p-0"
        >
          {events && events.items.length === 0 ? (
            <Empty title="No events yet" />
          ) : (
            <ul className="max-h-[32rem] divide-y divide-zinc-800/80 overflow-y-auto">
              {events?.items.map((e) => <EventRow key={e.id} event={e} compact onOpenClip={setClip} />)}
            </ul>
          )}
        </Card>
      </div>

      <RecordingPlayer key={clip ?? ''} file={clip} onClose={() => setClip(null)} />
    </div>
  )
}
