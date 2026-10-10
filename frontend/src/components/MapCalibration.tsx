import { useEffect, useState, type PointerEvent } from 'react'
import { RefreshCw, Trash2 } from 'lucide-react'
import { api } from '../api'
import { cx } from '../lib/cx'
import { mapApi, type Calibration, type MapCamera, type MapInfo, type Pair, type Point } from '../lib/mapApi'
import { errorMessage, useToast } from '../lib/toast'
import MapCanvas from './MapCanvas'
import { Button, Card, ConfirmDialog } from './ui'

type Spot = { pic: Point | null; map: Point | null }
type Check = { points: string; cal: Calibration | null; problem: string | null }

const MAX_PAIRS = 16
const REACH_MOUSE = 14
const REACH_TOUCH = 24

const metres = (m: number) => (m < 0.1 ? 'under 0.1 m' : `${m.toFixed(1)} m`)
const clamp = (v: number, max: number) => Math.min(Math.max(v, 0), max)

/**
 * Places a camera on the map: the same spots on the ground, marked on the camera picture and on
 * the map. Pairs are numbered alike on both sides and can be dragged. From 4 pairs on, the server
 * checks the fit and the ground area the camera sees is shown on the map.
 */
export default function MapCalibration({
  info,
  camera,
  onClose,
  onSaved,
}: {
  info: MapInfo
  camera: MapCamera
  onClose: () => void
  onSaved: (info: MapInfo) => void
}) {
  const notify = useToast()
  const [spots, setSpots] = useState<Spot[]>(() => camera.points.map(([a, b, c, d]) => ({ pic: [a, b], map: [c, d] })))
  const [selected, setSelected] = useState<number | null>(null)
  const [picture, setPicture] = useState(() => api.rawSnapshotUrl(camera.id))
  const [pictureFailed, setPictureFailed] = useState(false)
  const [aspect, setAspect] = useState(16 / 9)
  const [dragging, setDragging] = useState<{ spot: number; dx: number; dy: number } | null>(null)
  const [check, setCheck] = useState<Check | null>(null)
  const [saving, setSaving] = useState(false)
  const [confirm, setConfirm] = useState<'close' | 'remove' | null>(null)

  const points = spots.filter((s) => s.pic && s.map).map((s) => [...s.pic!, ...s.map!] as Pair)
  const key = JSON.stringify(points)
  const changed = key !== JSON.stringify(camera.points)
  const waiting = spots.findIndex((s) => !s.pic || !s.map) // the pair still missing one side
  const current = check?.points === key ? check : null
  const outliers = new Set(current?.cal?.outliers ?? [])

  useEffect(() => {
    if (points.length < 4) return
    const timer = setTimeout(() => {
      mapApi.check(camera.id, JSON.parse(key)).then(
        (cal) => setCheck({ points: key, cal, problem: null }),
        (err) => setCheck({ points: key, cal: null, problem: errorMessage(err) }),
      )
    }, 300)
    return () => clearTimeout(timer)
  }, [camera.id, key, points.length])

  const update = (i: number, side: 'pic' | 'map', at: Point) => setSpots((all) => all.map((s, j) => (j === i ? { ...s, [side]: at } : s)))

  /** A click on one side: completes the waiting pair, or starts a new one. */
  const place = (side: 'pic' | 'map', at: Point) => {
    if (waiting >= 0) {
      update(waiting, side, at)
      setSelected(waiting)
    } else if (spots.length >= MAX_PAIRS) {
      notify(`At most ${MAX_PAIRS} pairs`, 'error')
    } else {
      setSpots([...spots, { pic: null, map: null, [side]: at }])
      setSelected(spots.length)
    }
  }

  const onPicture = (e: PointerEvent<HTMLDivElement>) => {
    if (!e.isPrimary || pictureFailed) return
    const r = e.currentTarget.getBoundingClientRect()
    const [x, y] = [e.clientX - r.left, e.clientY - r.top]
    const reach = e.pointerType === 'mouse' ? REACH_MOUSE : REACH_TOUCH
    let best = -1
    let bestDistance = reach
    spots.forEach((s, i) => {
      if (!s.pic) return
      const d = Math.hypot(s.pic[0] * r.width - x, s.pic[1] * r.height - y)
      if (d < bestDistance) [best, bestDistance] = [i, d]
    })
    if (best >= 0) {
      // A press near a point moves it, even when it just misses the dot
      e.currentTarget.setPointerCapture(e.pointerId)
      setSelected(best)
      setDragging({ spot: best, dx: spots[best].pic![0] * r.width - x, dy: spots[best].pic![1] * r.height - y })
      return
    }
    place('pic', [Math.round(clamp(x / r.width, 1) * 10000) / 10000, Math.round(clamp(y / r.height, 1) * 10000) / 10000])
  }

  const onPictureMove = (e: PointerEvent<HTMLDivElement>) => {
    if (!dragging) return
    const r = e.currentTarget.getBoundingClientRect()
    const x = clamp((e.clientX - r.left + dragging.dx) / r.width, 1)
    const y = clamp((e.clientY - r.top + dragging.dy) / r.height, 1)
    update(dragging.spot, 'pic', [Math.round(x * 10000) / 10000, Math.round(y * 10000) / 10000])
  }

  const onMap = (at: Point, key: string | null) => {
    if (key?.startsWith('spot-')) setSelected(Number(key.slice(5)))
    else place('map', [Math.round(clamp(at[0], info.width) * 10) / 10, Math.round(clamp(at[1], info.height) * 10) / 10])
  }

  const remove = () => {
    const i = selected ?? spots.length - 1
    setSpots(spots.filter((_, j) => j !== i))
    setSelected(null)
  }

  const save = async (pairs: Pair[]) => {
    setSaving(true)
    try {
      const saved = await mapApi.saveCamera(camera.id, pairs)
      notify(pairs.length ? `${camera.name} is on the map` : `${camera.name} is off the map`, 'success')
      onSaved(saved)
      onClose()
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setSaving(false)
      setConfirm(null)
    }
  }

  const color = (i: number) =>
    // Only the last pair can be incomplete, so pair numbers match the server's
    i === selected ? '#2563eb' : outliers.has(i) ? '#dc2626' : !spots[i].pic || !spots[i].map ? '#d97706' : '#0891b2'

  const hint =
    waiting >= 0 && spots[waiting].pic
      ? `Now click the same spot on the map (pair ${waiting + 1}).`
      : waiting >= 0
        ? `Now click the same spot on the camera picture (pair ${waiting + 1}).`
        : points.length < 4
          ? `Click a spot on the ground in the camera picture, then the same spot on the map. ${points.length} of at least 4 pairs.`
          : 'Drag points to adjust them. More pairs, spread over the ground, make the map more accurate.'

  return (
    <>
      <Card
        title={`Place ${camera.name} on the map`}
        actions={
          <Button variant="ghost" size="sm" onClick={() => (changed || waiting >= 0 ? setConfirm('close') : onClose())}>
            Close
          </Button>
        }
      >
        <p className="-mt-1 mb-3 text-sm text-zinc-400">
          Pick spots on the ground that are easy to find in both pictures, such as the corners of a path, a doormat or the foot of a post, and
          spread them over the area the camera sees. Only people standing on that ground are placed correctly.
        </p>
        <div className="grid gap-3 lg:grid-cols-2">
          <div>
            <div className="label">Camera picture</div>
            <div
              className="relative cursor-crosshair touch-none select-none overflow-hidden rounded-md bg-zinc-950"
              style={{ aspectRatio: aspect }}
              onPointerDown={onPicture}
              onPointerMove={onPictureMove}
              onPointerUp={() => setDragging(null)}
              onPointerCancel={() => setDragging(null)}
            >
              {!pictureFailed && (
                <img
                  src={picture}
                  alt={`${camera.name} now`}
                  draggable={false}
                  className="block h-full w-full"
                  onLoad={(e) => setAspect(e.currentTarget.naturalWidth / e.currentTarget.naturalHeight)}
                  onError={() => setPictureFailed(true)}
                />
              )}
              {pictureFailed && (
                <p className="absolute inset-x-0 top-1/2 -translate-y-1/2 px-6 text-center text-sm text-zinc-500">
                  No picture from this camera right now. It needs to be turned on and connected.
                </p>
              )}
              {spots.map(
                (s, i) =>
                  s.pic && (
                    <span
                      key={i}
                      className="pointer-events-none absolute flex h-6 w-6 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full border-2 border-white text-[11px] font-semibold text-white shadow"
                      style={{ left: `${s.pic[0] * 100}%`, top: `${s.pic[1] * 100}%`, background: color(i) }}
                    >
                      {i + 1}
                    </span>
                  ),
              )}
            </div>
          </div>
          <div className="flex flex-col">
            <div className="label">Map</div>
            <MapCanvas
              map={info}
              className="min-h-[280px] flex-1"
              crosshair
              onTap={onMap}
              onDrag={(k, at) => {
                const i = Number(k.slice(5))
                setSelected(i)
                update(i, 'map', [Math.round(clamp(at[0], info.width) * 10) / 10, Math.round(clamp(at[1], info.height) * 10) / 10])
              }}
            >
              {(k) => (
                <>
                  {current?.cal && (
                    <polygon points={current.cal.field.map((p) => p.join(',')).join(' ')} fill="#38bdf8" fillOpacity={0.15} stroke="#38bdf8" strokeOpacity={0.6} strokeWidth={1.5 * k} />
                  )}
                  {spots.map(
                    (s, i) =>
                      s.map && (
                        <g key={i} data-key={`spot-${i}`} className="cursor-move">
                          <circle cx={s.map[0]} cy={s.map[1]} r={16 * k} fill="transparent" />
                          <circle cx={s.map[0]} cy={s.map[1]} r={11 * k} fill={color(i)} stroke="white" strokeWidth={2 * k} />
                          <text x={s.map[0]} y={s.map[1]} dy="0.35em" textAnchor="middle" fontSize={11 * k} fontWeight={600} fill="white" className="pointer-events-none">
                            {i + 1}
                          </text>
                        </g>
                      ),
                  )}
                </>
              )}
            </MapCanvas>
          </div>
        </div>

        <p className={cx('mt-3 text-sm', waiting >= 0 ? 'text-amber-400' : 'text-zinc-300')}>{hint}</p>
        <div className="mt-2 min-h-10 text-sm">
          {points.length >= 4 && !current && <p className="text-zinc-500">Checking…</p>}
          {current?.problem && <p className="text-amber-400">{current.problem}</p>}
          {current?.cal && (
            <div className="space-y-1 text-zinc-400">
              {current.cal.exact ? (
                <p>4 pairs always fit exactly, so there is no accuracy to show yet. Add a fifth pair to check it.</p>
              ) : (
                <p>
                  Typical error: <span className="font-medium text-zinc-100">{metres(current.cal.error)}</span> between where the pairs were marked and
                  where the calibration puts them.
                </p>
              )}
              {current.cal.outliers.map((i) => (
                <p key={i} className="text-amber-400">
                  Pair {i + 1} is {metres(current.cal!.errors[i])} away from where the other pairs put it, so it is left out. Move it or remove it.
                </p>
              ))}
              <p className="text-xs text-zinc-500">The shaded area on the map is the ground this camera sees, as far as the calibration reaches.</p>
            </div>
          )}
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-zinc-800 pt-4">
          <Button
            variant="ghost"
            icon={<RefreshCw className="h-4 w-4" />}
            onClick={() => {
              setPictureFailed(false)
              setPicture(api.rawSnapshotUrl(camera.id))
            }}
          >
            Refresh picture
          </Button>
          <Button variant="ghost" icon={<Trash2 className="h-4 w-4" />} disabled={!spots.length} onClick={remove}>
            {selected !== null ? `Remove pair ${selected + 1}` : 'Remove last pair'}
          </Button>
          <Button variant="ghost" disabled={!spots.length} onClick={() => setSpots([])}>
            Clear all
          </Button>
          <div className="ml-auto flex flex-wrap gap-2">
            {camera.points.length > 0 && (
              <Button variant="ghost" onClick={() => setConfirm('remove')}>
                Take off the map
              </Button>
            )}
            <Button variant="primary" loading={saving} disabled={!changed || !current?.cal || waiting >= 0} onClick={() => save(points)}>
              Save
            </Button>
          </div>
        </div>
      </Card>
      <ConfirmDialog
        open={confirm === 'close'}
        title="Discard your changes?"
        message="The points you placed are not saved."
        confirmLabel="Discard"
        onConfirm={onClose}
        onCancel={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm === 'remove'}
        title={`Take ${camera.name} off the map?`}
        message="Its points are deleted and the people it sees no longer show on the map."
        confirmLabel="Take off the map"
        busy={saving}
        onConfirm={() => save([])}
        onCancel={() => setConfirm(null)}
      />
    </>
  )
}
