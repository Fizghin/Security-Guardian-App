import { useState, type FormEvent, type ReactNode } from 'react'
import { Camera, FlaskConical, LayoutGrid, Megaphone, Square } from 'lucide-react'
import { api, type CameraStatus, type SecurityEvent, type Status } from '../api'
import EventRow, { EventPicture } from '../components/EventRow'
import LiveAudio from '../components/LiveAudio'
import LiveVideo from '../components/LiveVideo'
import RecordingPlayer from '../components/RecordingPlayer'
import { Button, Card, Empty } from '../components/ui'
import { cx } from '../lib/cx'
import { formatDuration, LEVELS, timeAgo } from '../lib/format'
import { href } from '../lib/route'
import { useStatus } from '../lib/status'
import { errorMessage, useToast } from '../lib/toast'
import { usePoll } from '../lib/usePoll'

const store = {
  get: (key: string) => {
    try {
      return localStorage.getItem(key)
    } catch {
      return null
    }
  },
  set: (key: string, value: string) => {
    try {
      localStorage.setItem(key, value)
    } catch {
      // private mode or storage disabled: the choice just isn't remembered
    }
  },
}

const SOURCE_LABEL: Record<string, string> = {
  llm: 'written by the model',
  cached: 'prepared in advance by the model',
  fallback: 'pre-written line',
  operator: 'typed by you',
  greeting: 'greeting',
}

const speakerText = (c: CameraStatus) =>
  c.kind !== 'phone' || c.audio.output === 'server' ? 'this computer' : c.audio.output === 'both' ? 'the phone and this computer' : 'the phone'

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 py-2 text-sm">
      <span className="shrink-0 text-zinc-500">{label}</span>
      <span className="min-w-0 text-right text-zinc-200">{children}</span>
    </div>
  )
}

function CameraCard({ camera, status }: { camera: CameraStatus; status: Status }) {
  const level = camera.threat_level
  const lv = LEVELS[level]
  const phone = camera.kind === 'phone' ? camera.phone : undefined
  const speakerOk =
    camera.kind === 'phone' && camera.audio.output !== 'server' ? !!phone?.online : status.voice.available !== false

  return (
    <Card title={camera.name}>
      <div className="flex items-center gap-3">
        <span className={cx('h-3 w-3 rounded-full', lv.bg)} />
        <div>
          <div className={cx('text-lg font-semibold leading-tight', lv.text)}>{level === 0 ? 'Clear' : lv.label}</div>
          <div className="text-xs text-zinc-500">
            {!status.armed
              ? 'Disarmed: detections are ignored'
              : camera.manual_alarm
                ? 'Panic alarm, reset to stand down'
                : level === 0
                  ? 'Watching for unrecognised people'
                  : `Level ${level} of 4${camera.test ? ' (test)' : ''}`}
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
          {camera.persons === 0 ? (
            <span className="text-zinc-400">Nobody</span>
          ) : (
            <>
              {camera.persons} {camera.persons === 1 ? 'person' : 'people'}
              {camera.insiders_in_view.length > 0 && (
                <span className="block text-xs text-emerald-400">Recognised: {camera.insiders_in_view.join(', ')}</span>
              )}
              {camera.pending > 0 && <span className="block text-xs text-blue-300">Identifying {camera.pending}…</span>}
            </>
          )}
          {camera.ignored > 0 && (
            <a href={href('learning')} className="block text-xs text-zinc-500 hover:text-zinc-300">
              {camera.ignored} ignored on a learned spot
            </a>
          )}
        </Row>
        <Row label="Incident">{camera.incident_started ? formatDuration(camera.incident_seconds) : <span className="text-zinc-400">None</span>}</Row>
        <Row label="Recording">
          {camera.recording.active ? (
            <span className="text-red-400">{camera.recording.stopping ? 'Finishing clip…' : 'Recording'}</span>
          ) : (
            <span className="text-zinc-400">Idle</span>
          )}
        </Row>
        <Row label="Siren">{camera.siren_active ? <span className="text-red-400">Sounding</span> : <span className="text-zinc-400">Off</span>}</Row>
        <Row label="Speaker">
          <span className={speakerOk ? 'text-zinc-400' : 'text-amber-400'}>
            {speakerText(camera)}
            {!speakerOk && (camera.kind === 'phone' && camera.audio.output !== 'server' ? ' (phone offline)' : ' (no speech engine)')}
            {camera.audio.talking && <span className="block text-xs text-blue-300">Someone is talking through it</span>}
          </span>
        </Row>
        {phone && (
          <Row label="Phone">
            <span className={phone.online ? 'text-zinc-300' : 'text-amber-400'}>
              {phone.online ? 'Online' : 'Offline'}
              {phone.battery != null && ` · ${phone.battery}%${phone.charging ? ' charging' : ''}`}
              {phone.camera && ` · ${phone.camera} camera`}
            </span>
          </Row>
        )}
        {phone && (
          <Row label="Microphone">
            {camera.audio.mic ? (
              <span className="tabular-nums text-zinc-300">
                Live{camera.audio.level_db != null && ` · ${Math.round(camera.audio.level_db)} dB`}
                {camera.audio.listeners > 0 && ` · ${camera.audio.listeners} listening`}
              </span>
            ) : (
              <span className="text-zinc-400">Off</span>
            )}
          </Row>
        )}
      </div>

      {camera.last_message && camera.last_message_time && (
        <div className="mt-3 rounded-md border border-zinc-800 bg-zinc-950/60 p-3">
          <p className="text-sm text-zinc-200">“{camera.last_message}”</p>
          <p className="mt-1 text-xs text-zinc-500">
            {timeAgo(camera.last_message_time, status.server_time)}
            {camera.last_message_source && ` · ${SOURCE_LABEL[camera.last_message_source] ?? camera.last_message_source}`}
          </p>
        </div>
      )}
    </Card>
  )
}

function Controls({ camera, status }: { camera: CameraStatus; status: Status }) {
  const notify = useToast()
  const { refresh } = useStatus()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState<'test' | 'speak' | null>(null)
  const testing = camera.pipeline.test_seconds_left > 0

  const startTest = async () => {
    setBusy('test')
    try {
      await api.testIntrusion(camera.id, 30)
      await refresh()
      notify(`Test started on ${camera.name}: a simulated person is added for 30 seconds`, 'success')
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
      await api.speak(text.trim(), camera.id)
      setText('')
      notify(`Speaking on ${speakerText(camera)}`, 'success')
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(null)
    }
  }

  return (
    <Card title={`Controls · ${camera.name}`} bodyClassName="grid gap-4 md:grid-cols-[1fr_auto] md:items-end">
      <form onSubmit={speak}>
        <label className="label" htmlFor="speak-text">
          Talk through {speakerText(camera)}
        </label>
        <div className="flex gap-2">
          <input
            id="speak-text"
            className="input"
            maxLength={300}
            placeholder="e.g. Can I help you? The owner is on the way."
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <Button type="submit" icon={<Megaphone className="h-4 w-4" />} loading={busy === 'speak'} disabled={!text.trim()}>
            Speak
          </Button>
        </div>
      </form>
      <div>
        <div className="label">Check the whole chain</div>
        <Button
          icon={<FlaskConical className="h-4 w-4" />}
          loading={busy === 'test'}
          disabled={testing || !camera.connected || !status.armed}
          onClick={startTest}
          title={!status.armed ? 'Arm the system first' : !camera.connected ? 'Needs this camera to be connected' : undefined}
        >
          {testing ? `Test running · ${camera.pipeline.test_seconds_left}s` : 'Run test intrusion'}
        </Button>
      </div>
      <LiveAudio key={camera.id} camera={camera} player={status.talk} />
    </Card>
  )
}

export default function LivePage() {
  const { status } = useStatus()
  const { data: events } = usePoll(() => api.events({}, 15), 3000)
  const [clip, setClip] = useState<string | null>(null)
  const [picture, setPicture] = useState<SecurityEvent | null>(null)
  const [selectedId, setSelectedId] = useState(() => store.get('live.camera'))
  const [layout, setLayout] = useState<'grid' | 'single'>(() => (store.get('live.layout') === 'single' ? 'single' : 'grid'))

  const cameras = status?.cameras ?? []
  const selected = cameras.find((c) => c.id === selectedId) ?? cameras[0]
  const select = (id: string) => {
    setSelectedId(id)
    store.set('live.camera', id)
  }
  const changeLayout = (l: 'grid' | 'single') => {
    setLayout(l)
    store.set('live.layout', l)
  }

  if (status && cameras.length === 0) {
    return (
      <Card>
        <Empty icon={<Camera className="h-8 w-8" />} title="No cameras are running">
          Add a webcam, an IP camera, or an old phone as a camera.{' '}
          <a href={href('settings', 'cameras')} className="text-blue-400 hover:text-blue-300">
            Set up cameras
          </a>
        </Empty>
      </Card>
    )
  }

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
      <div className="min-w-0 space-y-4">
        {cameras.length > 1 && (
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm text-zinc-400">
              {cameras.filter((c) => c.connected).length} of {cameras.length} cameras live
            </span>
            <div className="inline-flex rounded-md border border-zinc-700 bg-zinc-900 p-0.5">
              {(
                [
                  ['grid', LayoutGrid, 'All cameras'],
                  ['single', Square, 'One camera'],
                ] as const
              ).map(([value, Icon, label]) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => changeLayout(value)}
                  title={label}
                  className={cx('flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium', layout === value ? 'bg-zinc-700 text-zinc-50' : 'text-zinc-400 hover:text-zinc-200')}
                >
                  <Icon className="h-3.5 w-3.5" />
                  {label}
                </button>
              ))}
            </div>
          </div>
        )}

        {selected && (cameras.length === 1 || layout === 'single') && (
          <>
            <LiveVideo camera={selected} />
            {cameras.length > 1 && (
              <div className="grid grid-cols-3 gap-2 lg:grid-cols-4">
                {cameras.filter((c) => c.id !== selected.id).map((c) => (
                  <LiveVideo key={c.id} camera={c} compact onSelect={() => select(c.id)} />
                ))}
              </div>
            )}
          </>
        )}
        {selected && cameras.length > 1 && layout === 'grid' && (
          <div className="grid gap-3 sm:grid-cols-2">
            {cameras.map((c) => (
              <LiveVideo key={c.id} camera={c} compact={cameras.length > 2} selected={c.id === selected.id} onSelect={() => select(c.id)} />
            ))}
          </div>
        )}

        {status && selected && <Controls camera={selected} status={status} />}
      </div>

      <div className="space-y-4">
        {status && selected && <CameraCard camera={selected} status={status} />}
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
              {events?.items.map((e) => <EventRow key={e.id} event={e} compact onOpenClip={setClip} onOpenPicture={setPicture} />)}
            </ul>
          )}
        </Card>
      </div>

      <EventPicture event={picture} onClose={() => setPicture(null)} onOpenClip={setClip} />
      <RecordingPlayer key={clip ?? ''} file={clip} onClose={() => setClip(null)} />
    </div>
  )
}
