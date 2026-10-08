import { useRef, useState, type PointerEvent } from 'react'
import { RefreshCw, Trash2 } from 'lucide-react'
import { api, type CameraConfig } from '../api'
import { cx } from '../lib/cx'
import { errorMessage, useToast } from '../lib/toast'
import { Button, Modal } from './ui'

type Point = number[]
type Zone = Point[]

const MAX_ZONES = 8
const MAX_CORNERS = 32
const CLOSE_PX = 14 // clicking this close to the first corner finishes the zone
// Corners this close to the picture's edge go onto it. People cut off by the bottom of the
// picture stand on the very edge, so a zone meant to reach it must really reach it.
const SNAP_PX = 16

const points = (zone: Zone) => zone.map(([x, y]) => `${x},${y}`).join(' ')

export default function ZoneEditor({ camera, onClose, onSaved }: { camera: CameraConfig; onClose: () => void; onSaved: () => void }) {
  const notify = useToast()
  const box = useRef<HTMLDivElement>(null)
  const [zones, setZones] = useState<Zone[]>(camera.zones)
  const [drawing, setDrawing] = useState<Zone | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const [dragging, setDragging] = useState<[number, number] | null>(null)
  const [picture, setPicture] = useState(() => api.rawSnapshotUrl(camera.id))
  const [pictureFailed, setPictureFailed] = useState(false)
  const [saving, setSaving] = useState(false)
  const changed = JSON.stringify(zones) !== JSON.stringify(camera.zones)

  const toPoint = (e: PointerEvent): { at: Point; px: [number, number]; size: DOMRect } => {
    const size = box.current!.getBoundingClientRect()
    const snap = (offset: number, length: number) => (offset < SNAP_PX ? 0 : offset > length - SNAP_PX ? 1 : offset / length)
    const x = snap(e.clientX - size.left, size.width)
    const y = snap(e.clientY - size.top, size.height)
    return { at: [Math.round(x * 10000) / 10000, Math.round(y * 10000) / 10000], px: [e.clientX - size.left, e.clientY - size.top], size }
  }

  const finish = (zone: Zone) => {
    if (zone.length >= 3) {
      setZones((z) => [...z, zone])
      setSelected(zones.length)
    }
    setDrawing(null)
  }

  const onPicture = (e: PointerEvent) => {
    if (dragging) return
    const { at, px, size } = toPoint(e)
    if (drawing) {
      const [fx, fy] = drawing[0]
      if (drawing.length >= 3 && Math.hypot(px[0] - fx * size.width, px[1] - fy * size.height) < CLOSE_PX) finish(drawing)
      else if (drawing.length < MAX_CORNERS) setDrawing([...drawing, at])
      return
    }
    if (zones.length >= MAX_ZONES) {
      notify(`At most ${MAX_ZONES} zones per camera`, 'error')
      return
    }
    setSelected(null)
    setDrawing([at])
  }

  const onMove = (e: PointerEvent) => {
    if (!dragging) return
    const { at } = toPoint(e)
    const [zi, pi] = dragging
    setZones((z) => z.map((zone, i) => (i === zi ? zone.map((p, j) => (j === pi ? at : p)) : zone)))
  }

  const save = async () => {
    setSaving(true)
    try {
      await api.updateCamera(camera.id, { zones })
      notify(zones.length ? 'Zones saved' : 'The whole picture is watched again', 'success')
      onSaved()
      onClose()
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open
      wide
      onClose={onClose}
      title={`Detection zones · ${camera.name}`}
      footer={
        <>
          {drawing ? (
            <>
              <Button variant="ghost" onClick={() => setDrawing(null)}>
                Cancel zone
              </Button>
              <Button variant="primary" disabled={drawing.length < 3} onClick={() => finish(drawing)}>
                Finish zone
              </Button>
            </>
          ) : (
            <>
              {selected !== null && (
                <Button
                  variant="ghost"
                  icon={<Trash2 className="h-4 w-4" />}
                  onClick={() => {
                    setZones((z) => z.filter((_, i) => i !== selected))
                    setSelected(null)
                  }}
                >
                  Delete zone
                </Button>
              )}
              <Button variant="ghost" disabled={!zones.length} onClick={() => setZones([])}>
                Clear all (whole picture)
              </Button>
              <Button variant="primary" className="ml-auto" disabled={!changed} loading={saving} onClick={save}>
                Save
              </Button>
            </>
          )}
        </>
      }
    >
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <p className="text-sm text-zinc-400">
          Only people standing inside a zone count. With no zones, the whole picture counts.
        </p>
        <Button
          size="sm"
          variant="ghost"
          icon={<RefreshCw className="h-4 w-4" />}
          onClick={() => {
            setPictureFailed(false)
            setPicture(api.rawSnapshotUrl(camera.id))
          }}
        >
          Refresh picture
        </Button>
      </div>
      <div
        ref={box}
        className={cx('relative touch-none select-none rounded bg-zinc-950', drawing ? 'cursor-crosshair' : 'cursor-pointer', pictureFailed && 'aspect-video')}
        onPointerDown={onPicture}
        onPointerMove={onMove}
        onPointerUp={() => setDragging(null)}
        onPointerCancel={() => setDragging(null)}
      >
        {!pictureFailed && (
          <img src={picture} alt={`${camera.name} now`} draggable={false} className="block w-full rounded" onError={() => setPictureFailed(true)} />
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
              vectorEffect="non-scaling-stroke"
              className={cx('stroke-2', i === selected ? 'fill-blue-500/35 stroke-blue-400' : 'fill-cyan-400/20 stroke-cyan-300')}
              onPointerDown={(e) => {
                if (drawing) return
                e.stopPropagation()
                setSelected(i)
              }}
            />
          ))}
          {drawing && <polyline points={points(drawing)} vectorEffect="non-scaling-stroke" className="fill-cyan-400/10 stroke-cyan-300 stroke-2" />}
        </svg>
        {zones.map((zone, zi) =>
          zone.map(([x, y], pi) => (
            <span
              key={`${zi}-${pi}`}
              className={cx('absolute h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 cursor-move rounded-full border-2 border-white', zi === selected ? 'bg-blue-500' : 'bg-cyan-500')}
              style={{ left: `${x * 100}%`, top: `${y * 100}%` }}
              onPointerDown={(e) => {
                if (drawing) return
                e.stopPropagation()
                box.current?.setPointerCapture(e.pointerId)
                setSelected(zi)
                setDragging([zi, pi])
              }}
            />
          )),
        )}
        {drawing?.map(([x, y], i) => (
          <span
            key={i}
            className={cx('pointer-events-none absolute -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white bg-cyan-500', i === 0 && drawing.length >= 3 ? 'h-5 w-5' : 'h-3 w-3')}
            style={{ left: `${x * 100}%`, top: `${y * 100}%` }}
          />
        ))}
      </div>
      {/* Instructions sit below the picture so changing them never moves it under the pointer */}
      <p className="mt-2 text-xs text-zinc-400">
        {drawing
          ? `Click to add corners (${drawing.length} so far). Click the first corner or Finish zone to close it.`
          : 'Click the picture to outline an area; drag corners to adjust.'}
      </p>
      <p className="mt-1 text-xs text-zinc-500">
        {zones.length === 0 ? 'No zones: the whole picture is watched.' : `${zones.length} zone${zones.length === 1 ? '' : 's'}`}
        {changed && ' · not saved yet'}
      </p>
    </Modal>
  )
}
