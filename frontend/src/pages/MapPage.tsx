import { useEffect, useState } from 'react'
import { Camera, Map as MapIcon, Pause, Play, Settings2 } from 'lucide-react'
import MapCanvas from '../components/MapCanvas'
import MapSetup from '../components/MapSetup'
import { Button, Card, Dot, Empty, ErrorNote } from '../components/ui'
import { cx } from '../lib/cx'
import { formatDuration } from '../lib/format'
import { mapApi, STATUS_COLOR, STATUS_TEXT, type HistorySample, type MapFrame, type MapInfo, type MapPerson, type PersonStatus } from '../lib/mapApi'
import { href } from '../lib/route'
import { errorMessage } from '../lib/toast'

const REPLAY_SECONDS = 600
const TRAIL_SECONDS = 30
const STILL = 0.3 // m/s; slower counts as standing still

type Shown = Pick<MapPerson, 'id' | 'x' | 'y' | 'label' | 'status' | 'trail'> & { speed: number | null; live?: MapPerson }

/** People at `t` from the recorded samples: where each was last seen in the 2 s before, with their trail. */
function replayAt(samples: HistorySample[], t: number, mpp: number): Shown[] {
  const tracks = new Map<number, HistorySample[]>()
  for (const s of samples) {
    if (s[0] > t || s[0] < t - TRAIL_SECONDS) continue
    tracks.set(s[1], [...(tracks.get(s[1]) ?? []), s])
  }
  const out: Shown[] = []
  for (const [id, track] of tracks) {
    const last = track[track.length - 1]
    if (t - last[0] > 2) continue
    const prev = track[track.length - 2]
    const speed = prev && last[0] > prev[0] ? (Math.hypot(last[2] - prev[2], last[3] - prev[3]) * mpp) / (last[0] - prev[0]) : null
    out.push({ id, x: last[2], y: last[3], status: last[4], label: last[5], speed, trail: track.map((s) => [s[2], s[3], s[0]]) })
  }
  return out
}

function useLiveMap() {
  const [frame, setFrame] = useState<MapFrame | null>(null)
  const [connected, setConnected] = useState(false)
  useEffect(() => {
    let ws: WebSocket | null = null
    let retry: ReturnType<typeof setTimeout> | undefined
    let delay = 1000
    let closed = false
    const connect = () => {
      ws = new WebSocket(mapApi.liveUrl())
      ws.onopen = () => {
        delay = 1000
        setConnected(true)
      }
      ws.onmessage = (e) => setFrame(JSON.parse(e.data))
      ws.onclose = () => {
        setConnected(false)
        if (!closed) {
          retry = setTimeout(connect, delay)
          delay = Math.min(delay * 2, 10000)
        }
      }
      ws.onerror = () => ws?.close()
    }
    connect()
    return () => {
      closed = true
      clearTimeout(retry)
      ws?.close()
    }
  }, [])
  return { frame, connected }
}

function Details({ person, now, replaying }: { person: Shown; now: number; replaying: boolean }) {
  const live = person.live
  const visitor = person.status === 'unknown' && person.label !== 'Unknown'
  const who =
    person.status === 'known'
      ? `Insider${live?.identified_on ? `, recognised by face on ${live.identified_on}` : ''}`
      : visitor
        ? `Remembered visitor, not an insider${live?.identified_on ? `; face seen on ${live.identified_on}` : ''}`
        : STATUS_TEXT[person.status]
  return (
    <div className="space-y-2 text-sm">
      <div className="flex items-center gap-2">
        <span className="h-3 w-3 rounded-full" style={{ background: STATUS_COLOR[person.status] }} />
        <span className="font-medium text-zinc-100">{person.label}</span>
        <span className="text-xs text-zinc-500">#{person.id}</span>
      </div>
      <p className="text-zinc-400">{who}</p>
      {person.speed !== null && <p className="text-zinc-400">{person.speed >= STILL ? `Moving at about ${person.speed.toFixed(1)} m/s` : 'Standing still'}</p>}
      {live && (
        <>
          <div>
            <p className="text-xs text-zinc-500">Seen by</p>
            <ul className="mt-0.5 space-y-0.5">
              {live.views.map((v, i) => (
                <li key={i} className="flex items-center gap-1.5 text-zinc-300">
                  <Camera className="h-3.5 w-3.5 text-zinc-500" />
                  {v.camera}: <span style={{ color: STATUS_COLOR[v.status] }}>{v.label}</span>
                </li>
              ))}
            </ul>
          </div>
          <p className="text-xs text-zinc-500">On the map for {formatDuration(now - live.since)}</p>
        </>
      )}
      {replaying && <p className="text-xs text-zinc-500">Replay: the cameras that saw them aren’t kept.</p>}
    </div>
  )
}

function LiveMap({ info }: { info: MapInfo }) {
  const { frame, connected } = useLiveMap()
  const [selected, setSelected] = useState<number | null>(null)
  const [offset, setOffset] = useState(0) // seconds before `history.time`; 0 is live
  const [history, setHistory] = useState<{ time: number; samples: HistorySample[] } | null>(null)
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [playing, setPlaying] = useState(false)
  const mpp = info.metres_per_px ?? 1
  const replaying = offset < 0 && !!history

  useEffect(() => {
    if (!playing) return
    const timer = setInterval(() => setOffset((o) => Math.min(0, o + 1)), 250) // 4 times real speed
    return () => clearInterval(timer)
  }, [playing])

  const startReplay = async (to: number) => {
    setOffset(to)
    if (history && offset < 0) return
    try {
      setHistory(await mapApi.history(REPLAY_SECONDS / 60))
      setHistoryError(null)
    } catch (err) {
      setHistoryError(errorMessage(err))
    }
  }
  const goLive = () => {
    setOffset(0)
    setPlaying(false)
    setHistory(null)
  }
  if (playing && offset >= 0) setPlaying(false) // the replay caught up with now

  const now = replaying ? history.time + offset : (frame?.time ?? 0)
  const people: Shown[] = replaying
    ? replayAt(history.samples, now, mpp)
    : (frame?.people ?? []).map((p) => ({ ...p, live: p }))
  const cameras = frame?.cameras ?? info.cameras.filter((c) => c.calibration && c.enabled).map((c) => ({ id: c.id, name: c.name, ...c.calibration! }))
  const chosen = people.find((p) => p.id === selected)
  const unplaced = info.cameras.filter((c) => c.enabled && !c.calibration)

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_280px]">
      <div className="min-w-0 space-y-3">
        <MapCanvas
          key={`${info.width}x${info.height}`}
          map={info}
          className="h-[min(70vh,720px)] border border-zinc-800"
          onTap={(_, key) => setSelected(key?.startsWith('person-') ? Number(key.slice(7)) : null)}
        >
          {(k) => (
            <>
              {cameras.map((c) => (
                <g key={c.id}>
                  <polygon points={c.field.map((p) => p.join(',')).join(' ')} fill="#38bdf8" fillOpacity={0.08} stroke="#38bdf8" strokeOpacity={0.35} strokeWidth={k} />
                  <circle cx={c.at[0]} cy={c.at[1]} r={11 * k} fill="#0c4a6e" stroke="#38bdf8" strokeWidth={1.5 * k} />
                  <Camera x={c.at[0] - 6.5 * k} y={c.at[1] - 6.5 * k} width={13 * k} height={13 * k} color="#e0f2fe" />
                  <text x={c.at[0]} y={c.at[1] + 24 * k} textAnchor="middle" fontSize={11 * k} fill="#bae6fd" stroke="#09090b" strokeWidth={3 * k} paintOrder="stroke">
                    {c.name}
                  </text>
                </g>
              ))}
              {people.map((p) => {
                const points = [...p.trail.map(([x, y, t]) => [x, y, t]), [p.x, p.y, now]]
                return (
                  <g key={`trail-${p.id}`}>
                    {points.slice(1).map(([x, y, t], i) => (
                      <line
                        key={i}
                        x1={points[i][0]}
                        y1={points[i][1]}
                        x2={x}
                        y2={y}
                        stroke={STATUS_COLOR[p.status]}
                        strokeOpacity={0.1 + 0.6 * Math.max(0, 1 - (now - t) / TRAIL_SECONDS)}
                        strokeWidth={3 * k}
                        strokeLinecap="round"
                      />
                    ))}
                  </g>
                )
              })}
              {people.map((p) => (
                <g key={p.id} data-key={`person-${p.id}`} className="cursor-pointer">
                  <circle cx={p.x} cy={p.y} r={18 * k} fill="transparent" />
                  {p.id === selected && <circle cx={p.x} cy={p.y} r={13 * k} fill="none" stroke="white" strokeWidth={2 * k} />}
                  <circle cx={p.x} cy={p.y} r={8 * k} fill={STATUS_COLOR[p.status]} stroke="#09090b" strokeWidth={2 * k} />
                  <text x={p.x} y={p.y - 15 * k} textAnchor="middle" fontSize={12 * k} fontWeight={600} fill="white" stroke="#09090b" strokeWidth={3 * k} paintOrder="stroke">
                    {p.label}
                    {p.speed !== null && p.speed >= STILL && <tspan fontWeight={400} fill="#d4d4d8">{` ${p.speed.toFixed(1)} m/s`}</tspan>}
                  </text>
                </g>
              ))}
            </>
          )}
        </MapCanvas>

        <div className="rounded-lg border border-zinc-800 bg-zinc-900/60 px-4 py-3">
          <div className="mb-1.5 flex items-baseline justify-between text-xs">
            <span className="font-medium text-zinc-400">Replay last 10 minutes</span>
            <span className="font-mono tabular-nums text-zinc-200">{replaying ? `${formatDuration(-offset)} ago` : 'Live'}</span>
          </div>
          <div className="flex items-center gap-3">
            <Button
              size="sm"
              variant="ghost"
              icon={playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
              aria-label={playing ? 'Pause' : 'Play'}
              disabled={!replaying}
              onClick={() => setPlaying(!playing)}
            />
            <input type="range" min={-REPLAY_SECONDS} max={0} step={1} value={offset} aria-label="Replay position" onChange={(e) => {
              const v = Number(e.target.value)
              if (v < 0) startReplay(v)
              else goLive()
            }} />
            <Button size="sm" variant={replaying ? 'primary' : 'ghost'} disabled={!replaying} onClick={goLive}>
              Live
            </Button>
          </div>
          {historyError && <p className="mt-1 text-xs text-red-400">{historyError}</p>}
          <p className="hint">Drag back to see where people went, at 1 position a second. Positions are kept in memory, so a restart clears them.</p>
        </div>
      </div>

      <div className="space-y-4">
        <Card
          title={replaying ? 'Replay' : 'Live'}
          actions={
            <a href={href('map', 'setup')} className="inline-flex items-center gap-1 text-xs text-zinc-400 hover:text-zinc-100">
              <Settings2 className="h-3.5 w-3.5" /> Set up
            </a>
          }
        >
          <p className="mb-3 flex items-center gap-2 text-sm text-zinc-300">
            {replaying ? null : <Dot className={connected ? 'bg-emerald-500' : 'bg-zinc-600'} />}
            {!replaying && !connected
              ? 'Connecting…'
              : people.length === 0
                ? 'Nobody on the map'
                : `${people.length} ${people.length === 1 ? 'person' : 'people'} on the map`}
          </p>
          <ul className="space-y-1 text-xs text-zinc-400">
            {(['known', 'unknown', 'pending'] as PersonStatus[]).map((s) => (
              <li key={s} className="flex items-center gap-2">
                <span className="h-2.5 w-2.5 rounded-full" style={{ background: STATUS_COLOR[s] }} />
                {STATUS_TEXT[s]}
              </li>
            ))}
          </ul>
        </Card>
        <Card title="Selected">
          {chosen ? (
            <Details person={chosen} now={now} replaying={replaying} />
          ) : (
            <p className="text-sm text-zinc-500">Tap a dot to see who it is and which cameras see them.</p>
          )}
        </Card>
        {(cameras.length === 0 || unplaced.length > 0) && (
          <Card title="Cameras">
            <p className={cx('text-sm', cameras.length ? 'text-zinc-400' : 'text-amber-400')}>
              {cameras.length === 0 ? 'No camera is on the map yet, so nobody can be shown. ' : `Not on the map: ${unplaced.map((c) => c.name).join(', ')}. `}
              <a href={href('map', 'setup')} className="text-blue-400 hover:text-blue-300">
                Place cameras
              </a>
            </p>
          </Card>
        )}
        <p className="px-1 text-xs text-zinc-500">
          Positions are where people’s feet are, placed with each camera’s calibration, so they are as accurate as that calibration. Only people a
          camera counts (inside its detection zones) appear.
        </p>
      </div>
    </div>
  )
}

export default function MapPage({ section }: { section: string }) {
  const [info, setInfo] = useState<MapInfo | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    mapApi.get().then(setInfo, (err) => setError(errorMessage(err)))
  }, [section])

  if (error) return <ErrorNote>{error}</ErrorNote>
  if (!info) return <p className="text-sm text-zinc-500">Loading the map…</p>
  if (section === 'setup') return <MapSetup info={info} onChange={setInfo} />
  if (!info.image) {
    return (
      <Card>
        <Empty icon={<MapIcon className="h-10 w-10" />} title="No property map yet">
          <p>A live view from above of where people are, across all cameras. Setting it up takes three steps:</p>
          <ol className="mx-auto mt-3 max-w-sm list-decimal space-y-1 pl-5 text-left">
            <li>Upload a floorplan or an aerial picture of the property, or use a blank grid.</li>
            <li>Set the scale: click two points on the map and type how far apart they are.</li>
            <li>For each camera, click 4 or more spots on the ground in its picture and the same spots on the map.</li>
          </ol>
          <a href={href('map', 'setup')} className="mt-4 inline-flex h-9 items-center rounded-md bg-blue-600 px-3.5 text-sm font-medium text-white hover:bg-blue-500">
            Set up the map
          </a>
        </Empty>
      </Card>
    )
  }
  return <LiveMap info={info} />
}
