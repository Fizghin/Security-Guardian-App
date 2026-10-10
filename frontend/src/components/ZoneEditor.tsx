import { useEffect, useRef, useState, type PointerEvent } from 'react'
import { RefreshCw, Trash2 } from 'lucide-react'
import { api, type CameraConfig } from '../api'
import { cx } from '../lib/cx'
import { useStatus } from '../lib/status'
import { errorMessage, useToast } from '../lib/toast'
import { crossesItself, inside, shapeChanged, zoneProblem, type Zone } from '../lib/zones'
import { Button, ConfirmDialog, Modal } from './ui'

type Grab = { zone: number; corner: number; dx: number; dy: number }

const MAX_ZONES = 8
const MAX_CORNERS = 32
// How close a press must be to a corner to grab it, or to the first corner to close a zone
const REACH_MOUSE = 14
const REACH_TOUCH = 24
// Corners this close to the picture's edge go onto it. People cut off by the bottom of the
// picture stand on the very edge, so a zone meant to reach it must really reach it.
const SNAP_PX = 16

const points = (zone: Zone) => zone.map(([x, y]) => `${x},${y}`).join(' ')

/** The first "Spot n" name not in use yet */
const freeName = (names: string[]) => {
  let n = names.length + 1
  while (names.some((name) => name.trim().toLowerCase() === `spot ${n}`)) n++
  return `Spot ${n}`
}

/** Why the watch spots' names can't be saved, or null */
function namesProblem(names: string[]) {
  const tidy = names.map((name) => name.trim().toLowerCase())
  if (tidy.some((name) => !name)) return 'Give every watch spot a name.'
  if (new Set(tidy).size !== tidy.length) return 'Each watch spot needs its own name.'
  return null
}

const toPoint = (x: number, y: number, size: DOMRect) => {
  const snap = (offset: number, length: number) => (offset < SNAP_PX ? 0 : offset > length - SNAP_PX ? 1 : offset / length)
  return [Math.round(snap(x, size.width) * 10000) / 10000, Math.round(snap(y, size.height) * 10000) / 10000]
}

/**
 * Draws a camera's detection zones, or (mode "spots") its watch spots: named areas such as a gate that
 * should keep looking the same. Both are outlines with the same rules.
 */
export default function ZoneEditor({
  camera,
  mode = 'zones',
  onClose,
  onSaved,
}: {
  camera: CameraConfig
  mode?: 'zones' | 'spots'
  onClose: () => void
  onSaved: () => void
}) {
  const notify = useToast()
  const { status } = useStatus()
  const area = useRef<HTMLDivElement>(null)
  const spots = mode === 'spots'
  const noun = spots ? 'spot' : 'zone'
  const saved = spots ? camera.watch_spots.map((s) => s.polygon) : camera.zones
  const savedNames = camera.watch_spots.map((s) => s.name)
  const [zones, setZones] = useState<Zone[]>(saved)
  const [names, setNames] = useState<string[]>(savedNames)
  const [drawing, setDrawing] = useState<Zone | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  // The corner being moved, and where it sits relative to the pointer
  const [dragging, setDragging] = useState<Grab | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [picture, setPicture] = useState(() => api.rawSnapshotUrl(camera.id))
  const [pictureFailed, setPictureFailed] = useState(false)
  const [pictureAspect, setPictureAspect] = useState<number | null>(null)
  const [room, setRoom] = useState({ width: 0, height: 0 })
  const [confirmClose, setConfirmClose] = useState(false)
  const [saving, setSaving] = useState(false)
  const changed = JSON.stringify(zones) !== JSON.stringify(saved) || (spots && JSON.stringify(names) !== JSON.stringify(savedNames))

  const live = status?.cameras.find((c) => c.id === camera.id)
  const liveAspect = live?.width && live.height ? live.width / live.height : null
  const aspect = pictureAspect ?? liveAspect ?? camera.zones_aspect ?? 16 / 9
  const drawnOnOtherShape = !spots && camera.zones.length > 0 && shapeChanged(camera.zones_aspect, pictureAspect ?? liveAspect)
  // The whole picture fits in the room the dialog leaves, whatever its shape
  const height = Math.min(room.height, room.width / aspect)
  const width = height * aspect

  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setRoom({ width: entry.contentRect.width, height: entry.contentRect.height }))
    observer.observe(area.current!)
    return () => observer.disconnect()
  }, [])

  const requestClose = () => {
    if (confirmClose) return
    if (drawing || changed) setConfirmClose(true)
    else onClose()
  }

  const cancelZone = () => {
    setDrawing(null)
    setNote(null)
  }

  const finish = (zone: Zone) => {
    const problem = zoneProblem(zone)
    if (problem) {
      setNote(`${problem.replace('zone', noun)} Add corners or cancel the ${noun}.`)
      return
    }
    setZones((z) => [...z, zone])
    if (spots) setNames((n) => [...n, freeName(n)])
    setSelected(zones.length)
    cancelZone()
  }

  const remove = (keep: (i: number) => boolean) => {
    setZones((z) => z.filter((_, i) => keep(i)))
    setNames((n) => n.filter((_, i) => keep(i)))
    setSelected(null)
  }

  const nearestCorner = (x: number, y: number, size: DOMRect, reach: number) => {
    let best: Grab | null = null
    let bestDistance = reach
    for (const [zi, zone] of zones.entries()) {
      for (const [pi, [px, py]] of zone.entries()) {
        const [dx, dy] = [px * size.width - x, py * size.height - y]
        const distance = Math.hypot(dx, dy)
        if (distance < bestDistance) {
          best = { zone: zi, corner: pi, dx, dy }
          bestDistance = distance
        }
      }
    }
    return best
  }

  const onPicture = (e: PointerEvent<HTMLDivElement>) => {
    if (dragging || !e.isPrimary) return
    const size = e.currentTarget.getBoundingClientRect()
    const [x, y] = [e.clientX - size.left, e.clientY - size.top]
    const reach = e.pointerType === 'mouse' ? REACH_MOUSE : REACH_TOUCH
    setNote(null)
    if (drawing) {
      const [fx, fy] = drawing[0]
      if (drawing.length >= 3 && Math.hypot(x - fx * size.width, y - fy * size.height) < reach) return finish(drawing)
      if (drawing.length >= MAX_CORNERS) return
      const at = toPoint(x, y, size)
      const [lx, ly] = drawing[drawing.length - 1]
      if (at[0] === lx && at[1] === ly) return
      if (crossesItself([...drawing, at], false)) setNote('A corner there would make the outline cross or overlap itself.')
      else setDrawing([...drawing, at])
      return
    }
    // A press near a corner moves it, even when it just misses the dot
    const grab = nearestCorner(x, y, size, reach)
    if (grab) {
      e.currentTarget.setPointerCapture(e.pointerId)
      setSelected(grab.zone)
      setDragging(grab)
      return
    }
    for (let i = zones.length - 1; i >= 0; i--) {
      if (inside([x / size.width, y / size.height], zones[i])) {
        setSelected(i)
        return
      }
    }
    if (zones.length >= MAX_ZONES) {
      notify(`At most ${MAX_ZONES} ${spots ? 'watch spots' : 'zones'} per camera`, 'error')
      return
    }
    setSelected(null)
    setDrawing([toPoint(x, y, size)])
  }

  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    if (!dragging) return
    const size = e.currentTarget.getBoundingClientRect()
    const at = toPoint(e.clientX - size.left + dragging.dx, e.clientY - size.top + dragging.dy, size)
    const zone = zones[dragging.zone].map((p, i) => (i === dragging.corner ? at : p))
    // A corner that would spoil the zone stays where it was
    const problem = zoneProblem(zone)
    setNote(problem && problem.replace('zone', noun))
    if (!problem) setZones(zones.map((z, i) => (i === dragging.zone ? zone : z)))
  }

  const save = async () => {
    const problem = spots && namesProblem(names)
    if (problem) {
      setNote(problem)
      return
    }
    setSaving(true)
    try {
      if (spots) {
        await api.updateCamera(camera.id, { watch_spots: zones.map((polygon, i) => ({ name: names[i].trim(), polygon })) })
        notify(zones.length ? 'Watch spots saved' : 'No watch spots', 'success')
      } else {
        // Without a picture the server assumes the zones fit the camera's live picture
        await api.updateCamera(camera.id, { zones, ...(pictureAspect && { zones_aspect: pictureAspect }) })
        notify(zones.length ? 'Zones saved' : 'The whole picture is watched again', 'success')
      }
      onSaved()
      onClose()
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setSaving(false)
    }
  }

  const full = !!drawing && drawing.length >= MAX_CORNERS
  const hint =
    note ??
    (!drawing
      ? spots
        ? 'Outline something that should keep looking the same, such as a gate, door or window. Click to outline it; drag corners to adjust.'
        : 'Only people standing inside a zone count. Click to outline an area; drag corners to adjust.'
      : full
        ? `${MAX_CORNERS} corners is the most a ${noun} can have. Click the first corner or Finish ${noun} to close it.`
        : `Click to add corners (${drawing.length} so far). Click the first corner or Finish ${noun} to close it.`)

  return (
    <>
      <Modal
        open
        wide
        onClose={requestClose}
        onEscape={() => (drawing && !confirmClose ? cancelZone() : requestClose())}
        bodyClassName="flex flex-col"
        title={`${spots ? 'Watch spots' : 'Detection zones'} · ${camera.name}`}
        footer={
          // Every button is always there, so the picture never moves while you work
          <>
            <Button
              variant="ghost"
              className="mr-auto"
              icon={<RefreshCw className="h-4 w-4" />}
              aria-label="Refresh picture"
              title="Refresh picture"
              onClick={() => {
                setPictureFailed(false)
                setPicture(api.rawSnapshotUrl(camera.id))
              }}
            >
              <span className="hidden sm:inline">Refresh picture</span>
            </Button>
            <Button
              variant="ghost"
              icon={<Trash2 className="h-4 w-4" />}
              disabled={selected === null || !!drawing}
              onClick={() => remove((i) => i !== selected)}
            >
              Delete {noun}
            </Button>
            <Button
              variant="ghost"
              disabled={!zones.length || !!drawing}
              title={spots ? 'Remove every watch spot' : 'Remove every zone, so the whole picture is watched'}
              onClick={() => remove(() => false)}
            >
              Clear all
            </Button>
            <Button variant="ghost" disabled={!drawing} onClick={cancelZone}>
              Cancel {noun}
            </Button>
            <Button disabled={!drawing || drawing.length < 3} onClick={() => drawing && finish(drawing)}>
              Finish {noun}
            </Button>
            <Button variant="primary" disabled={!changed || !!drawing} loading={saving} onClick={save}>
              Save
            </Button>
          </>
        }
      >
        <div ref={area} className="flex min-h-24 items-center justify-center overflow-hidden" style={{ flexBasis: room.width / aspect }}>
          <div
            className={cx('relative touch-none select-none rounded bg-zinc-950', drawing ? 'cursor-crosshair' : 'cursor-pointer')}
            style={{ width, height }}
            onPointerDown={onPicture}
            onPointerMove={onMove}
            onPointerUp={() => setDragging(null)}
            onPointerCancel={() => setDragging(null)}
          >
            {!pictureFailed && (
              <img
                src={picture}
                alt={`${camera.name} now`}
                draggable={false}
                className="block h-full w-full rounded"
                onLoad={(e) => setPictureAspect(e.currentTarget.naturalWidth / e.currentTarget.naturalHeight)}
                onError={() => setPictureFailed(true)}
              />
            )}
            {pictureFailed && (
              <p className="absolute inset-x-0 top-1/2 -translate-y-1/2 px-6 text-center text-sm text-zinc-500">
                No picture from this camera right now. You can still draw; the frame is the whole picture.
              </p>
            )}
            <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="absolute inset-0 h-full w-full">
              {zones.map((zone, i) => (
                <polygon
                  key={i}
                  points={points(zone)}
                  fillRule="evenodd"
                  vectorEffect="non-scaling-stroke"
                  className={cx(
                    'stroke-2',
                    i === selected ? 'fill-blue-500/35 stroke-blue-400' : spots ? 'fill-violet-400/20 stroke-violet-300' : 'fill-cyan-400/20 stroke-cyan-300',
                  )}
                />
              ))}
              {drawing && <polyline points={points(drawing)} fillRule="evenodd" vectorEffect="non-scaling-stroke" className="fill-cyan-400/10 stroke-cyan-300 stroke-2" />}
            </svg>
            {zones.map((zone, zi) =>
              zone.map(([x, y], pi) => (
                <span
                  key={`${zi}-${pi}`}
                  className={cx('absolute flex h-7 w-7 -translate-x-1/2 -translate-y-1/2 items-center justify-center', drawing ? 'pointer-events-none' : 'cursor-move')}
                  style={{ left: `${x * 100}%`, top: `${y * 100}%` }}
                >
                  <span className={cx('h-3.5 w-3.5 rounded-full border-2 border-white', zi === selected ? 'bg-blue-500' : spots ? 'bg-violet-500' : 'bg-cyan-500')} />
                </span>
              )),
            )}
            {spots &&
              zones.map((zone, i) => (
                <span
                  key={`name-${i}`}
                  // Above the spot, or just inside it when it reaches the top of the picture
                  className={cx(
                    'pointer-events-none absolute max-w-[40%] truncate rounded bg-black/70 px-1.5 py-0.5 text-[11px] text-violet-100',
                    Math.min(...zone.map(([, y]) => y)) > 0.08 && '-translate-y-full',
                  )}
                  style={{ left: `${Math.min(...zone.map(([x]) => x)) * 100}%`, top: `${Math.min(...zone.map(([, y]) => y)) * 100}%` }}
                >
                  {names[i]}
                </span>
              ))}
            {drawing?.map(([x, y], i) => (
              <span
                key={i}
                className={cx('pointer-events-none absolute -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white bg-cyan-500', i === 0 && drawing.length >= 3 ? 'h-5 w-5' : 'h-3 w-3')}
                style={{ left: `${x * 100}%`, top: `${y * 100}%` }}
              />
            ))}
          </div>
        </div>
        {/* Room for the longest message, so changing messages never move the picture */}
        <p className={cx('mt-2 min-h-8 shrink-0 text-xs sm:min-h-4', note || full ? 'text-amber-400' : 'text-zinc-400')}>{hint}</p>
        {spots && (
          <div className="mt-2 flex shrink-0 items-center gap-2">
            <label htmlFor="spot-name" className="shrink-0 text-xs text-zinc-400">
              Name
            </label>
            <input
              id="spot-name"
              className="input h-8 py-1 text-sm"
              maxLength={40}
              disabled={selected === null || !!drawing}
              placeholder={zones.length ? 'Select a spot to name it' : 'Outline a spot first'}
              value={selected !== null && !drawing ? (names[selected] ?? '') : ''}
              onChange={(e) => {
                const name = e.target.value
                setNote(null)
                setNames((n) => n.map((old, i) => (i === selected ? name : old)))
              }}
            />
          </div>
        )}
        <p className="mt-1 min-h-8 shrink-0 text-xs text-zinc-500 sm:min-h-4">
          {spots
            ? zones.length === 0
              ? 'No watch spots yet.'
              : `${zones.length} watch spot${zones.length === 1 ? '' : 's'}. Each is compared with how it usually looks; you're told when one looks different for 5 s or more with nobody in it.`
            : zones.length === 0
              ? 'No zones: the whole picture is watched.'
              : `${zones.length} zone${zones.length === 1 ? '' : 's'}`}
          {changed ? (
            ' · not saved yet'
          ) : drawnOnOtherShape ? (
            <span className="text-amber-400"> · The picture changed shape since these zones were drawn. Redraw them.</span>
          ) : null}
        </p>
      </Modal>
      <ConfirmDialog
        open={confirmClose}
        title="Discard your changes?"
        message={`The ${spots ? 'watch spots' : 'zones'} you changed are not saved.`}
        confirmLabel="Discard"
        onConfirm={onClose}
        onCancel={() => setConfirmClose(false)}
      />
    </>
  )
}
