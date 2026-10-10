import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, BellRing, Flame, Info, Map as MapIcon, Moon, RotateCcw, Siren, UserSearch } from 'lucide-react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, type CameraStatus, type Heatmap, type Insights } from '../api'
import { Button, Card, ConfirmDialog, Empty, ErrorNote, Segmented, StatTile } from '../components/ui'
import { CHART, chartTooltip } from '../lib/chart'
import { cx } from '../lib/cx'
import { formatShortDate } from '../lib/format'
import { href } from '../lib/route'
import { useStatus } from '../lib/status'
import { errorMessage, useToast } from '../lib/toast'
import { usePoll } from '../lib/usePoll'

const RANGES = [
  { value: 7, label: '7 days' },
  { value: 30, label: '30 days' },
  { value: 90, label: '90 days' },
]

// One sequential hue (blue) for every count: darker cells are quieter on the dark surface.
const BAR = CHART.bar
// Steps chosen so the ramp stays in order in both themes (light mode maps 400 and 600 to the same blue).
const STEPS = ['bg-zinc-800/70', 'bg-blue-950', 'bg-blue-800', 'bg-blue-500', 'bg-blue-300', 'bg-blue-100']

const THREAT = {
  calm: { label: 'Calm', color: '#10b981', text: 'text-emerald-300' },
  low: { label: 'Low', color: '#facc15', text: 'text-yellow-200' },
  elevated: { label: 'Elevated', color: '#f59e0b', text: 'text-amber-300' },
  high: { label: 'High', color: '#ef4444', text: 'text-red-300' },
} as const

function ThreatGauge({ threat }: { threat: Insights['threat'] }) {
  const t = THREAT[threat.level]
  const r = 70
  const arc = Math.PI * r
  const filled = (arc * threat.score) / 100
  return (
    <div className="flex flex-col items-center sm:flex-row sm:items-center sm:gap-6">
      <svg viewBox="0 0 180 104" className="w-48 shrink-0" role="img" aria-label={`Threat score ${threat.score} of 100, ${t.label}`}>
        <path d="M20 94 A70 70 0 0 1 160 94" fill="none" stroke={CHART.grid} strokeWidth="14" strokeLinecap="round" />
        <path
          d="M20 94 A70 70 0 0 1 160 94"
          fill="none"
          stroke={t.color}
          strokeWidth="14"
          strokeLinecap="round"
          strokeDasharray={`${filled} ${arc}`}
          style={{ transition: 'stroke-dasharray .6s ease' }}
        />
        <text x="90" y="80" textAnchor="middle" className="fill-zinc-50" style={{ font: '600 30px Inter, system-ui, sans-serif' }}>
          {threat.score}
        </text>
        <text x="90" y="98" textAnchor="middle" className="fill-zinc-500" style={{ font: '11px Inter, system-ui, sans-serif' }}>
          of 100
        </text>
      </svg>
      <div className="mt-2 min-w-0 sm:mt-0">
        <div className={cx('text-lg font-semibold', t.text)}>{t.label}</div>
        <p className="text-xs text-zinc-500">Threat score for the last 24 hours</p>
        {threat.reasons.length ? (
          <ul className="mt-2 space-y-1 text-sm text-zinc-300">
            {threat.reasons.map((r) => (
              <li key={r} className="flex gap-2">
                <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: t.color }} />
                {r}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-2 text-sm text-zinc-400">Nothing out of the ordinary.</p>
        )}
      </div>
    </div>
  )
}

function WeekHourGrid({ data }: { data: Insights }) {
  const max = data.grid_max
  const step = (v: number) => (v === 0 || max === 0 ? 0 : Math.min(STEPS.length - 1, 1 + Math.floor(((v - 1) / Math.max(1, max)) * (STEPS.length - 1))))
  return (
    <div className="overflow-x-auto">
      <div className="min-w-[560px]">
        <div className="grid grid-cols-[36px_repeat(24,minmax(0,1fr))] gap-[2px]">
          <span />
          {Array.from({ length: 24 }, (_, h) => (
            <span key={h} className="text-center text-[10px] tabular-nums text-zinc-500">
              {h % 3 === 0 ? String(h).padStart(2, '0') : ''}
            </span>
          ))}
          {data.grid.map((row, d) => (
            <div key={d} className="contents">
              <span className="pr-1 text-right text-[11px] leading-5 text-zinc-500">{data.weekdays[d]}</span>
              {row.map((v, h) => (
                <span
                  key={h}
                  title={`${data.weekdays[d]} ${String(h).padStart(2, '0')}:00 · ${v} incident${v === 1 ? '' : 's'}`}
                  className={cx('h-5 rounded-[3px] transition-transform hover:scale-125 hover:ring-1 hover:ring-white/60', STEPS[step(v)])}
                />
              ))}
            </div>
          ))}
        </div>
        <div className="mt-3 flex items-center justify-end gap-1.5 text-[11px] text-zinc-500">
          Fewer
          {STEPS.map((s) => (
            <span key={s} className={cx('h-3 w-3 rounded-[3px]', s)} />
          ))}
          More
        </div>
      </div>
    </div>
  )
}

/** Draws a camera's heatmap as a soft glow over its picture. */
function HeatCanvas({ heat }: { heat: Heatmap }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const canvas = ref.current
    if (!canvas || !heat.grid.length) return
    const small = document.createElement('canvas')
    small.width = heat.cols
    small.height = heat.rows
    const sctx = small.getContext('2d')
    if (!sctx) return
    const img = sctx.createImageData(heat.cols, heat.rows)
    // Square root so a few busy cells don't hide every other path.
    const scale = Math.sqrt(heat.max || 1)
    heat.grid.forEach((row, r) =>
      row.forEach((v, c) => {
        const t = v > 0 ? Math.sqrt(v) / scale : 0
        const i = (r * heat.cols + c) * 4
        // Warm ramp: amber to red, more opaque where busier
        img.data[i] = 255
        img.data[i + 1] = Math.round(200 - 170 * t)
        img.data[i + 2] = Math.round(40 - 20 * t)
        img.data[i + 3] = t ? Math.round(60 + 170 * t) : 0
      }),
    )
    sctx.putImageData(img, 0, 0)
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    canvas.width = heat.cols * 16
    canvas.height = heat.rows * 16
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    ctx.filter = 'blur(10px)'
    ctx.imageSmoothingEnabled = true
    ctx.drawImage(small, 0, 0, canvas.width, canvas.height)
  }, [heat])
  return <canvas ref={ref} className="pointer-events-none absolute inset-0 h-full w-full mix-blend-screen" />
}

function CameraHeatmap({ camera }: { camera: CameraStatus }) {
  const notify = useToast()
  const { data, refresh } = usePoll(() => api.heatmap(camera.id), 30000, [camera.id])
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [picture] = useState(() => api.rawSnapshotUrl(camera.id))
  const [noPicture, setNoPicture] = useState(false)

  const reset = async () => {
    setBusy(true)
    try {
      await api.resetHeatmap(camera.id)
      refresh()
      notify(`Heatmap for ${camera.name} cleared`, 'success')
      setConfirm(false)
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-950/40">
      <div className="force-dark relative aspect-video bg-black">
        {!noPicture && camera.connected ? (
          <img src={picture} alt="" className="h-full w-full object-cover opacity-60" onError={() => setNoPicture(true)} />
        ) : (
          <div className="absolute left-2 top-2 text-[11px] text-zinc-600">No live picture</div>
        )}
        {data && data.samples > 0 && <HeatCanvas heat={data} />}
        {data?.hotspots.map((h, i) => (
          <span
            key={i}
            className="absolute flex h-5 w-5 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-black/70 text-[10px] font-semibold text-white ring-1 ring-white/70"
            style={{ left: `${h.x * 100}%`, top: `${h.y * 100}%` }}
            title={`${Math.round(h.share * 100)}% of all footsteps`}
          >
            {i + 1}
          </span>
        ))}
      </div>
      <div className="flex items-start justify-between gap-2 p-3">
        <div className="min-w-0">
          <div className="truncate text-sm font-medium text-zinc-100">{camera.name}</div>
          <div className="text-xs text-zinc-500">
            {data?.samples
              ? `${data.samples.toLocaleString()} footsteps since ${formatShortDate(new Date((data.since ?? 0) * 1000).toISOString())}` +
                (data.hotspots[0] ? ` · busiest: ${data.hotspots[0].where}` : '')
              : 'Nobody counted yet'}
          </div>
        </div>
        {!!data?.samples && (
          <Button size="sm" variant="ghost" icon={<RotateCcw className="h-3.5 w-3.5" />} onClick={() => setConfirm(true)}>
            Reset
          </Button>
        )}
      </div>
      <ConfirmDialog
        open={confirm}
        title={`Reset the heatmap for ${camera.name}?`}
        message="Counting starts again from now. Useful after moving the camera."
        confirmLabel="Reset"
        busy={busy}
        onConfirm={reset}
        onCancel={() => setConfirm(false)}
      />
    </div>
  )
}

const FINDING_ICON = {
  info: <Info className="h-4 w-4 text-blue-300" />,
  warning: <AlertTriangle className="h-4 w-4 text-amber-300" />,
  alert: <Flame className="h-4 w-4 text-red-300" />,
}

export default function InsightsPage() {
  const [days, setDays] = useState(30)
  const { status } = useStatus()
  const { data, error } = usePoll(() => api.insights(days), 30000, [days])

  const hourData = data?.hours.map((count, h) => ({ hour: `${String(h).padStart(2, '0')}:00`, count })) ?? []
  const dayData = data?.series.map((d) => ({ ...d, label: formatShortDate(`${d.date}T12:00:00`) })) ?? []

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-zinc-400">
          Patterns in who shows up, when and where{data ? ` · times in ${data.time_zone}` : ''}.
        </p>
        <Segmented value={days} options={RANGES} onChange={setDays} />
      </div>

      {error && <ErrorNote>{error.message}</ErrorNote>}

      {data && (
        <>
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
            <Card title="Right now">
              <ThreatGauge threat={data.threat} />
            </Card>
            <Card title="What stands out" bodyClassName="p-0">
              <ul className="divide-y divide-zinc-800/80">
                {data.findings.map((f) => (
                  <li key={f.text} className="flex gap-3 px-4 py-3 text-sm text-zinc-300">
                    <span className="mt-0.5 shrink-0">{FINDING_ICON[f.kind]}</span>
                    {f.text}
                  </li>
                ))}
              </ul>
            </Card>
          </div>

          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile label="Incidents" value={data.totals.incidents} detail={`${data.last_24h.incidents} in the last 24 h`} icon={<BellRing className="h-4 w-4" />} />
            <StatTile
              label="Night incidents"
              value={data.night.last_night}
              detail={`last night · usually ${data.night.baseline.toFixed(1)}`}
              icon={<Moon className="h-4 w-4" />}
              tone={data.night.last_night > data.night.baseline * 2 + 1 ? 'warn' : 'default'}
            />
            <StatTile
              label="Owner alerts"
              value={data.totals.alerts}
              detail={`${data.totals.sirens} siren${data.totals.sirens === 1 ? '' : 's'}`}
              icon={<Siren className="h-4 w-4" />}
              tone={data.totals.alerts ? 'bad' : 'default'}
            />
            <StatTile
              label="Returning visitors"
              value={data.totals.returning}
              detail="strangers who came back"
              icon={<UserSearch className="h-4 w-4" />}
              tone={data.totals.returning ? 'warn' : 'default'}
              href={href('visitors')}
            />
          </div>

          <Card title="When incidents happen" actions={<span className="text-xs text-zinc-500">Weekday × hour of day</span>}>
            {data.totals.incidents ? (
              <WeekHourGrid data={data} />
            ) : (
              <Empty title="No incidents in this period">Incidents start when an unrecognised person appears while armed.</Empty>
            )}
          </Card>

          <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
            <Card title="Incidents by hour of day">
              <div className="h-48">
                <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 500, height: 192 }}>
                  <BarChart data={hourData} margin={{ top: 4, right: 4, bottom: 0, left: -24 }} barCategoryGap={2}>
                    <CartesianGrid vertical={false} stroke={CHART.grid} />
                    <XAxis dataKey="hour" tick={{ fill: CHART.tick, fontSize: 11 }} tickLine={false} axisLine={{ stroke: CHART.axis }} interval={5} />
                    <YAxis allowDecimals={false} tick={{ fill: CHART.tick, fontSize: 11 }} tickLine={false} axisLine={false} />
                    <Tooltip {...chartTooltip} />
                    <Bar dataKey="count" name="Incidents" fill={BAR} radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Card>
            <Card title="Incidents per day">
              <div className="h-48">
                <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 500, height: 192 }}>
                  <BarChart data={dayData} margin={{ top: 4, right: 4, bottom: 0, left: -24 }} barCategoryGap={2}>
                    <CartesianGrid vertical={false} stroke={CHART.grid} />
                    <XAxis dataKey="label" tick={{ fill: CHART.tick, fontSize: 11 }} tickLine={false} axisLine={{ stroke: CHART.axis }} minTickGap={24} />
                    <YAxis allowDecimals={false} tick={{ fill: CHART.tick, fontSize: 11 }} tickLine={false} axisLine={false} />
                    <Tooltip {...chartTooltip} />
                    <Bar dataKey="incidents" name="Incidents" fill={BAR} radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Card>
          </div>

          {data.cameras.length > 0 && (
            <Card title="By camera" bodyClassName="p-0 overflow-x-auto">
              <table className="w-full min-w-[520px] text-sm">
                <thead className="text-left text-xs text-zinc-500">
                  <tr>
                    {['Camera', 'Incidents', 'Alerts', 'Sirens', 'Loud sounds', 'Left objects', 'Insiders seen', 'Went offline'].map((h) => (
                      <th key={h} className="px-4 py-2 font-medium">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-800/80 tabular-nums">
                  {data.cameras.map((c) => {
                    const share = data.totals.incidents ? c.incidents / data.totals.incidents : 0
                    return (
                      <tr key={c.camera}>
                        <td className="px-4 py-2.5 font-medium text-zinc-100">{c.camera}</td>
                        <td className="px-4 py-2.5">
                          <div className="flex items-center gap-2">
                            <span className="w-8 text-zinc-200">{c.incidents}</span>
                            <span className="h-1.5 w-24 overflow-hidden rounded-full bg-zinc-800">
                              <span className="block h-full rounded-full" style={{ width: `${share * 100}%`, background: BAR }} />
                            </span>
                          </div>
                        </td>
                        <td className="px-4 py-2.5 text-zinc-300">{c.alerts}</td>
                        <td className="px-4 py-2.5 text-zinc-300">{c.sirens}</td>
                        <td className="px-4 py-2.5 text-zinc-300">{c.sounds}</td>
                        <td className="px-4 py-2.5 text-zinc-300">{c.unattended}</td>
                        <td className="px-4 py-2.5 text-zinc-300">{c.insiders}</td>
                        <td className="px-4 py-2.5 text-zinc-300">{c.offline}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </Card>
          )}
        </>
      )}

      <Card
        title={
          <span className="flex items-center gap-2">
            <MapIcon className="h-4 w-4 text-zinc-400" /> Where people walk
          </span>
        }
        actions={<span className="text-xs text-zinc-500">Footsteps of everyone detected, all time · numbers mark the busiest spots</span>}
      >
        {status?.cameras.length ? (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {status.cameras.map((c) => (
              <CameraHeatmap key={c.id} camera={c} />
            ))}
          </div>
        ) : (
          <Empty title="No cameras are running" />
        )}
      </Card>
    </div>
  )
}
