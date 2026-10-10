import { Fragment, useEffect, useState } from 'react'
import { RotateCcw } from 'lucide-react'
import { api, type CameraConfig, type RoutineReport } from '../../api'
import { cx } from '../../lib/cx'
import { href } from '../../lib/route'
import { errorMessage, useToast } from '../../lib/toast'
import { usePoll } from '../../lib/usePoll'
import { Button, Card, ConfirmDialog, Empty, ErrorNote } from '../ui'

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const HOURS = Array.from({ length: 24 }, (_, h) => h)
// Hatching for cells with too little watching around them to judge
const HATCH = 'repeating-linear-gradient(135deg, rgba(9,9,11,.6) 0 2px, transparent 2px 5px)'

/** How often people are there, darkest for "usually quiet". The first level is the same line the server judges by. */
function levels(quiet: number) {
  return [
    { below: quiet, label: 'Usually quiet', className: 'bg-zinc-700' },
    { below: 0.1, label: 'Rarely', className: 'bg-blue-900' },
    { below: 0.3, label: 'Sometimes', className: 'bg-blue-700' },
    { below: 0.6, label: 'Often', className: 'bg-blue-500' },
    { below: Infinity, label: 'Usually', className: 'bg-blue-300' },
  ]
}

const pad = (h: number) => `${String(h % 24).padStart(2, '0')}:00`
const percent = (share: number) => (share === 0 ? '0%' : share < 0.01 ? 'under 1%' : `${Math.round(share * 100)}%`)

/** "people in 2 of 12 hours watched", or minutes while less than an hour was watched. */
function amounts(people: number, watched: number) {
  if (watched < 59.5) return `people in ${Math.round(people)} of ${Math.round(watched)} minutes watched`
  const hours = (m: number) => {
    const h = m / 60
    return h >= 10 ? String(Math.round(h)) : String(Math.round(h * 10) / 10)
  }
  return `people in ${hours(people)} of ${hours(watched)} hours watched`
}

function describeCell(r: RoutineReport, day: number, hour: number) {
  const watched = r.observed_minutes[day][hour]
  const head = `${DAYS[day]} ${pad(hour)}–${pad(hour + 1)}`
  if (watched < 0.5) return `${head}: not watched yet`
  const strangers = r.stranger_minutes[day][hour]
  const parts = [`${head}: ${amounts(r.people_minutes[day][hour], watched)}`]
  if (strangers >= 0.5) parts.push(`unrecognised people in ${Math.round(strangers)} ${Math.round(strangers) === 1 ? 'minute' : 'minutes'}`)
  if (!r.confident[day][hour]) parts.push('still learning this time')
  return parts.join('; ')
}

function describeNow(r: RoutineReport) {
  const when = `${DAYS[r.now.day]} ${pad(r.now.hour)}–${pad(r.now.hour + 1)}`
  if (!r.now.confident || r.now.expected == null) return `Now (${when}): still learning this time of day.`
  const usual = `Now (${when}): people are usually seen in ${percent(r.now.expected)} of the minutes watched around this time.`
  if (!r.now.unusual || r.mode === 'off') return usual
  return `${usual} While armed, an unrecognised person now would be marked as unusual.`
}

export default function RoutineSection() {
  const notify = useToast()
  const [cameras, setCameras] = useState<CameraConfig[] | null>(null)
  const [cameraId, setCameraId] = useState('')
  const [selected, setSelected] = useState<[number, number] | null>(null)
  const [confirm, setConfirm] = useState(false)
  const [resetting, setResetting] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)

  useEffect(() => {
    api.cameras().then(
      (r) => {
        setCameras(r.cameras)
        setCameraId((id) => id || r.cameras[0]?.id || '')
      },
      (err) => setLoadError(errorMessage(err)),
    )
  }, [])

  // New minutes are added once a minute
  const routine = usePoll(() => (cameraId ? api.routine(cameraId) : Promise.resolve(null)), 60000, [cameraId])
  const r = routine.data?.camera_id === cameraId ? routine.data : null
  const camera = cameras?.find((c) => c.id === cameraId)

  const reset = async () => {
    setResetting(true)
    try {
      await api.resetRoutine(cameraId)
      notify(`Routine for ${camera?.name ?? 'the camera'} reset`, 'success')
      setConfirm(false)
      await routine.refresh()
    } catch (err) {
      notify(errorMessage(err), 'error')
    }
    setResetting(false)
  }

  const scale = levels(r?.unusual_below ?? 0.025)
  const colour = (share: number) => scale.find((l) => share < l.below)!.className
  const [selDay, selHour] = selected ?? (r ? [r.now.day, r.now.hour] : [0, 0])

  return (
    <Card
      id="routine"
      title="Routine"
      actions={
        cameras &&
        cameras.length > 0 && (
          <>
            <select className="input h-8 w-auto py-0 text-xs" value={cameraId} onChange={(e) => setCameraId(e.target.value)} aria-label="Camera">
              {cameras.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
            <Button size="sm" variant="ghost" icon={<RotateCcw className="h-4 w-4" />} disabled={!r} onClick={() => setConfirm(true)}>
              Reset
            </Button>
          </>
        )
      }
    >
      {loadError && <ErrorNote>{loadError}</ErrorNote>}
      {routine.error && <ErrorNote>{routine.error.message}</ErrorNote>}
      {cameras && cameras.length === 0 && <Empty title="No cameras yet">Add a camera in Settings → Cameras.</Empty>}
      {cameraId && !r && !routine.error && <p className="text-sm text-zinc-500">Loading…</p>}

      {r && (
        <div className="space-y-4">
          <div className="space-y-1 text-sm">
            <p className="font-medium text-zinc-100">
              {r.days_watched < r.days_needed
                ? `Learning: ${r.days_watched} of ${r.days_needed} days watched`
                : `Learned from ${r.days_watched} days watched`}
            </p>
            <p className="text-zinc-400">
              Guardian counts how often people are in view of {r.name} at each hour of the week; older weeks count less.{' '}
              {r.mode === 'off' ? (
                <>
                  Marking unusual activity is turned off in{' '}
                  <a href={href('settings', 'learning')} className="text-blue-400 hover:text-blue-300">
                    Settings → Learning
                  </a>
                  .
                </>
              ) : (
                `While armed, an unrecognised person at an hour when nobody is usually there is marked as unusual${
                  r.mode === 'alert' ? ' and you get an alert straight away' : ' in the event log'
                }. Hatched hours haven’t been watched enough to judge yet.`
              )}
            </p>
          </div>

          {r.summary.length > 0 && (
            <ul className="space-y-0.5 text-sm text-zinc-300">
              {r.summary.map((line) => (
                <li key={line}>{line}</li>
              ))}
            </ul>
          )}

          <div>
            <div className="grid grid-cols-[2rem_repeat(24,minmax(0,1fr))] gap-px text-[10px] text-zinc-500 sm:grid-cols-[2.5rem_repeat(24,minmax(0,1fr))]">
              <span />
              {HOURS.map((h) => (
                <span key={h} className={cx('text-left tabular-nums', h === r.now.hour && 'font-semibold text-amber-300')}>
                  {h % 3 === 0 ? String(h).padStart(2, '0') : ''}
                </span>
              ))}
              {DAYS.map((name, day) => (
                <Fragment key={name}>
                  <span className={cx('flex items-center text-xs', day === r.now.day ? 'font-semibold text-amber-300' : 'text-zinc-400')}>{name}</span>
                  {HOURS.map((hour) => {
                    const watched = r.observed_minutes[day][hour] >= 0.5
                    const share = r.share[day][hour]
                    const isNow = day === r.now.day && hour === r.now.hour
                    const label = describeCell(r, day, hour)
                    return (
                      <button
                        key={hour}
                        type="button"
                        title={label}
                        aria-label={label}
                        onClick={() => setSelected([day, hour])}
                        className={cx(
                          'h-4 rounded-[2px] sm:h-6',
                          watched && share != null ? colour(share) : 'border border-dashed border-zinc-800 bg-zinc-900',
                          isNow && 'relative z-10 ring-2 ring-amber-400 ring-offset-1 ring-offset-zinc-900',
                          day === selDay && hour === selHour && !isNow && 'relative z-10 ring-1 ring-zinc-200',
                        )}
                        style={watched && !r.confident[day][hour] ? { backgroundImage: HATCH } : undefined}
                      />
                    )
                  })}
                </Fragment>
              ))}
            </div>
            <p className="mt-2 min-h-5 text-xs text-zinc-300">{describeCell(r, selDay, selHour)}</p>
          </div>

          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-zinc-400">
            {scale.map((l) => (
              <span key={l.label} className="flex items-center gap-1.5">
                <span className={cx('h-3 w-3 rounded-[2px]', l.className)} />
                {l.label}
              </span>
            ))}
            <span className="flex items-center gap-1.5">
              <span className="h-3 w-3 rounded-[2px] bg-zinc-600" style={{ backgroundImage: HATCH }} />
              Still learning
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-3 w-3 rounded-[2px] border border-dashed border-zinc-700 bg-zinc-900" />
              Not watched
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-3 w-3 rounded-[2px] ring-2 ring-amber-400" />
              Now
            </span>
          </div>

          <p className="text-sm text-zinc-300">{describeNow(r)}</p>
          <p className="text-xs text-zinc-500">Times are the Guardian computer’s local time. The grid adds new minutes about once a minute.</p>
        </div>
      )}

      <ConfirmDialog
        open={confirm}
        title={`Reset the routine for ${camera?.name ?? 'this camera'}?`}
        message={`Guardian forgets when people are usually in view of ${camera?.name ?? 'this camera'} and starts learning again. Nothing there is marked as unusual until it has watched for ${r?.days_needed ?? 3} days.`}
        confirmLabel="Reset"
        busy={resetting}
        onConfirm={reset}
        onCancel={() => setConfirm(false)}
      />
    </Card>
  )
}
