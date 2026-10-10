import { useRef, useState, type ReactNode } from 'react'
import { ArrowLeft, Grid3x3, Ruler, Trash2, Upload } from 'lucide-react'
import { cx } from '../lib/cx'
import { mapApi, type MapCamera, type MapInfo, type Point } from '../lib/mapApi'
import { href } from '../lib/route'
import { errorMessage, useToast } from '../lib/toast'
import MapCalibration from './MapCalibration'
import MapCanvas from './MapCanvas'
import { Button, Card, ConfirmDialog, Field } from './ui'

const metres = (m: number) => (m < 0.1 ? 'under 0.1 m' : `${m.toFixed(1)} m`)

function cameraState(c: MapCamera): { text: string; warn?: boolean } {
  if (c.problem) return { text: c.problem, warn: true }
  if (!c.calibration) return { text: 'Not on the map' }
  const accuracy = c.calibration.exact ? 'add a fifth pair to check its accuracy' : `typical error ${metres(c.calibration.error)}`
  return { text: `On the map · ${c.points.length} pairs · ${accuracy}${c.enabled ? '' : ' · camera turned off'}` }
}

function Step({ n, title, children, done }: { n: number; title: string; children: ReactNode; done?: boolean }) {
  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          <span className={cx('flex h-5 w-5 items-center justify-center rounded-full text-[11px]', done ? 'bg-emerald-600 text-white' : 'bg-zinc-700 text-zinc-200')}>{n}</span>
          {title}
        </span>
      }
    >
      {children}
    </Card>
  )
}

/** Setting up the property map: floorplan, scale and each camera's place on it. */
export default function MapSetup({ info, onChange }: { info: MapInfo; onChange: (info: MapInfo) => void }) {
  const notify = useToast()
  const file = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [grid, setGrid] = useState({ width: 30, height: 20 })
  const [pending, setPending] = useState<{ title: string; message: string; label: string; success: string; run: () => Promise<MapInfo> } | null>(null)
  const [scaling, setScaling] = useState<Point[] | null>(null) // the points clicked while setting the scale
  const [distance, setDistance] = useState('')
  const [calibrating, setCalibrating] = useState<string | null>(null)
  const placed = info.cameras.filter((c) => c.points.length)
  const camera = info.cameras.find((c) => c.id === calibrating)

  const run = async (fn: () => Promise<MapInfo>, success: string) => {
    setBusy(true)
    try {
      onChange(await fn())
      notify(success, 'success')
      return true
    } catch (err) {
      notify(errorMessage(err), 'error')
      return false
    } finally {
      setBusy(false)
      setPending(null)
    }
  }

  // A new floorplan takes the cameras off the map: their points were on the old one
  const replace = (run2: () => Promise<MapInfo>, success: string) => {
    const go = () => run(run2, success)
    if (!placed.length) return go()
    setPending({
      title: 'Replace the map?',
      message: `${placed.map((c) => c.name).join(', ')} ${placed.length === 1 ? 'is' : 'are'} placed on the current map. Replacing it takes them off, and you place them again on the new one.`,
      label: 'Replace',
      success,
      run: run2,
    })
  }

  if (camera) {
    return <MapCalibration info={info} camera={camera} onClose={() => setCalibrating(null)} onSaved={onChange} />
  }

  const describe =
    info.image === ''
      ? 'No map yet.'
      : info.image === 'grid'
        ? `Blank grid, ${Math.round(info.width * (info.metres_per_px ?? 0))} × ${Math.round(info.height * (info.metres_per_px ?? 0))} m.`
        : `Floorplan picture, ${info.width} × ${info.height} pixels.`
  const scaleText = info.metres_per_px
    ? `1 m is ${(1 / info.metres_per_px).toFixed(1)} pixels on the map${info.scale_line.length ? `, measured on a line of ${info.scale_line[4]} m` : info.image === 'grid' ? '; each grid square is 1 m' : ''}.`
    : 'Not set yet.'
  const line = scaling ?? (info.scale_line.length ? [info.scale_line.slice(0, 2) as Point, info.scale_line.slice(2, 4) as Point] : [])

  const saveScale = async () => {
    const m = Number(distance)
    if (!scaling || scaling.length < 2 || !(m > 0)) return
    if (await run(() => mapApi.setScale([...scaling[0], ...scaling[1]] as [number, number, number, number], m), 'Scale saved')) setScaling(null)
  }

  return (
    <div className="space-y-4">
      <a href={href('map')} className="inline-flex items-center gap-1.5 text-sm text-zinc-400 hover:text-zinc-100">
        <ArrowLeft className="h-4 w-4" /> Back to the live map
      </a>

      <Step n={1} title="Floorplan" done={!!info.image}>
        <p className="mb-4 text-sm text-zinc-400">{describe}</p>
        <div className="grid gap-5 md:grid-cols-2">
          <Field label="A picture of the property from above" hint="A plan, a drawing or an aerial photo, as PNG or JPEG up to 10 MB. It stays on this computer.">
            <input
              ref={file}
              type="file"
              accept="image/png,image/jpeg"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0]
                e.target.value = ''
                if (f) replace(() => mapApi.uploadPicture(f), 'Floorplan uploaded')
              }}
            />
            <Button icon={<Upload className="h-4 w-4" />} loading={busy} onClick={() => file.current?.click()}>
              Upload a picture
            </Button>
          </Field>
          <Field label="Or a blank grid" hint="Squares of 1 m. Useful when you have no plan; draw nothing, just place the cameras.">
            <div className="flex flex-wrap items-center gap-2">
              <input className="input w-20" type="number" min={5} max={400} value={grid.width} onChange={(e) => setGrid({ ...grid, width: Number(e.target.value) })} aria-label="Width in metres" />
              <span className="text-zinc-500">×</span>
              <input className="input w-20" type="number" min={5} max={400} value={grid.height} onChange={(e) => setGrid({ ...grid, height: Number(e.target.value) })} aria-label="Height in metres" />
              <span className="text-xs text-zinc-500">m</span>
              <Button
                icon={<Grid3x3 className="h-4 w-4" />}
                disabled={busy || !(grid.width >= 5 && grid.width <= 400 && grid.height >= 5 && grid.height <= 400)}
                onClick={() => replace(() => mapApi.useGrid(grid.width, grid.height), 'Using a blank grid')}
              >
                Use a grid
              </Button>
            </div>
          </Field>
        </div>
        {info.image && (
          <div className="mt-4 border-t border-zinc-800 pt-4">
            <Button
              variant="ghost"
              icon={<Trash2 className="h-4 w-4" />}
              onClick={() =>
                setPending({ title: 'Remove the map?', message: 'The floorplan and every camera’s place on it are deleted.', label: 'Remove', success: 'Map removed', run: mapApi.remove })
              }
            >
              Remove the map
            </Button>
          </div>
        )}
      </Step>

      {info.image && (
        <Step n={2} title="Scale" done={!!info.metres_per_px}>
          <p className="mb-3 text-sm text-zinc-400">
            {scaling === null
              ? scaleText
              : scaling.length < 2
                ? `Click two points on the map whose distance you know, such as the two ends of a wall (${scaling.length} of 2).`
                : 'How far apart are these two points in real life?'}
          </p>
          <div className="mb-3 flex flex-wrap items-end gap-2">
            {scaling === null ? (
              <Button icon={<Ruler className="h-4 w-4" />} onClick={() => setScaling([])}>
                {info.metres_per_px ? 'Set the scale again' : 'Set the scale'}
              </Button>
            ) : (
              <>
                {scaling.length === 2 && (
                  <label className="flex items-center gap-2 text-sm text-zinc-300">
                    Distance
                    <input className="input w-24" type="number" min={0.1} step={0.1} autoFocus value={distance} onChange={(e) => setDistance(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && saveScale()} />
                    m
                  </label>
                )}
                <Button variant="primary" disabled={scaling.length < 2 || !(Number(distance) > 0)} loading={busy} onClick={saveScale}>
                  Save scale
                </Button>
                <Button variant="ghost" onClick={() => setScaling(null)}>
                  Cancel
                </Button>
              </>
            )}
          </div>
          <MapCanvas
            key={`${info.width}x${info.height}`}
            map={info}
            className="h-[min(55vh,480px)]"
            crosshair={scaling !== null}
            onTap={(at) => {
              if (scaling === null) return
              const p: Point = [Math.min(Math.max(at[0], 0), info.width), Math.min(Math.max(at[1], 0), info.height)]
              setScaling(scaling.length >= 2 ? [p] : [...scaling, p])
            }}
          >
            {(k) => (
              <>
                {info.cameras.map(
                  (c) =>
                    c.calibration && (
                      <polygon key={c.id} points={c.calibration.field.map((p) => p.join(',')).join(' ')} fill="#38bdf8" fillOpacity={0.1} stroke="#38bdf8" strokeOpacity={0.4} strokeWidth={k} />
                    ),
                )}
                {line.length === 2 && <line x1={line[0][0]} y1={line[0][1]} x2={line[1][0]} y2={line[1][1]} stroke="#fbbf24" strokeWidth={2.5 * k} strokeDasharray={`${6 * k} ${4 * k}`} />}
                {line.map(([x, y], i) => (
                  <circle key={i} cx={x} cy={y} r={6 * k} fill="#fbbf24" stroke="#09090b" strokeWidth={1.5 * k} />
                ))}
              </>
            )}
          </MapCanvas>
        </Step>
      )}

      {info.image && (
        <Step n={3} title="Cameras" done={placed.length > 0}>
          <p className="mb-3 text-sm text-zinc-400">
            For each camera, mark 4 or more spots on the ground in its picture and the same spots on the map. Guardian then works out where people
            stand from where their feet are in the picture.
          </p>
          {!info.metres_per_px && <p className="mb-3 text-sm text-amber-400">Set the scale first, so positions and speeds come out in metres.</p>}
          <ul className="divide-y divide-zinc-800">
            {info.cameras.map((c) => {
              const state = cameraState(c)
              return (
                <li key={c.id} className="flex flex-wrap items-center justify-between gap-3 py-2.5">
                  <div className="min-w-0">
                    <p className="text-sm text-zinc-100">{c.name}</p>
                    <p className={cx('text-xs', state.warn ? 'text-amber-400' : 'text-zinc-500')}>{state.text}</p>
                  </div>
                  <Button size="sm" disabled={!info.metres_per_px} onClick={() => setCalibrating(c.id)}>
                    {c.points.length ? 'Adjust' : 'Place on the map'}
                  </Button>
                </li>
              )
            })}
          </ul>
        </Step>
      )}

      <ConfirmDialog
        open={!!pending}
        title={pending?.title ?? ''}
        message={pending?.message}
        confirmLabel={pending?.label ?? ''}
        busy={busy}
        onConfirm={() => pending && run(pending.run, pending.success)}
        onCancel={() => setPending(null)}
      />
    </div>
  )
}
