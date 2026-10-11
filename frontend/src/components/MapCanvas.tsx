import { useEffect, useId, useRef, useState, type PointerEvent, type ReactNode } from 'react'
import { Maximize, Minus, Plus } from 'lucide-react'
import { cx } from '../lib/cx'
import type { MapInfo, Point } from '../lib/mapApi'

type View = { x: number; y: number; w: number; h: number }
type Size = { width: number; height: number; left: number; top: number }
type Gesture =
  | { kind: 'pan' | 'drag'; key: string | null; sx: number; sy: number; view: View; moved: boolean }
  | { kind: 'pinch'; dist: number; anchor: Point; view: View }

const MOVE_PX = 6 // a press that moves less than this is a tap
const MAX_ZOOM = 25

/** Map pixels per screen pixel, with the viewBox fitted inside the box (xMidYMid meet). */
const scaleOf = (v: View, s: Size) => (s.width && s.height ? Math.max(v.w / s.width, v.h / s.height) : v.w / 800)

function toMap(v: View, s: Size, clientX: number, clientY: number): Point {
  const k = scaleOf(v, s)
  return [v.x + (clientX - s.left - (s.width - v.w / k) / 2) * k, v.y + (clientY - s.top - (s.height - v.h / k) / 2) * k]
}

/** A metre length that reads well (1, 2, 5, 10…) for a scale bar about `target` metres long. */
function niceMetres(target: number) {
  const base = 10 ** Math.floor(Math.log10(target))
  return (
    [1, 2, 5, 10]
      .map((f) => f * base)
      .filter((m) => m <= target)
      .pop() ?? base
  )
}

/**
 * The floorplan (or a blank metre grid) with pan and zoom: drag or use the wheel on a computer,
 * one finger to pan and two to zoom on a phone. `children` draw on top in map pixels; they get
 * the number of map pixels per screen pixel, to keep dots and labels the same size at any zoom.
 * Elements with a data-key attribute are reported to onTap, and dragged with onDrag.
 */
export default function MapCanvas({
  map,
  children,
  onTap,
  onDrag,
  onDragEnd,
  className,
  crosshair,
}: {
  map: Pick<MapInfo, 'width' | 'height' | 'image' | 'picture' | 'metres_per_px'>
  children?: (k: number) => ReactNode
  onTap?: (at: Point, key: string | null) => void
  onDrag?: (key: string, at: Point) => void
  onDragEnd?: (key: string) => void
  className?: string
  crosshair?: boolean
}) {
  const box = useRef<HTMLDivElement>(null)
  const pointers = useRef(new Map<number, Point>())
  const gesture = useRef<Gesture | null>(null)
  const uid = useId().replace(/[^a-zA-Z0-9]/g, '')
  const pad = Math.max(map.width, map.height) * 0.04
  const home: View = { x: -pad, y: -pad, w: map.width + 2 * pad, h: map.height + 2 * pad }
  const [view, setView] = useState<View>(home)
  const [size, setSize] = useState<Size>({ width: 0, height: 0, left: 0, top: 0 })
  const k = scaleOf(view, size)

  // Limits: from the whole map with room around it to MAX_ZOOM times closer, centre over the map
  const limit = (v: View): View => {
    const w = Math.min(Math.max(v.w, home.w / MAX_ZOOM), home.w * 2)
    const h = (v.h * w) / v.w
    const cx = Math.min(Math.max(v.x + v.w / 2, 0), map.width)
    const cy = Math.min(Math.max(v.y + v.h / 2, 0), map.height)
    return { x: cx - w / 2, y: cy - h / 2, w, h }
  }
  // Zoom by `factor` keeping the spot under (clientX, clientY) where it is
  const zoomAt = (v: View, s: Size, clientX: number, clientY: number, factor: number): View => {
    const [mx, my] = toMap(v, s, clientX, clientY)
    const w = Math.min(Math.max(v.w * factor, home.w / MAX_ZOOM), home.w * 2)
    const a = w / v.w
    return limit({ x: mx - (mx - v.x) * a, y: my - (my - v.y) * a, w, h: v.h * a })
  }
  const zoomRef = useRef(zoomAt)
  useEffect(() => {
    zoomRef.current = zoomAt
  })

  const measure = (): Size => {
    const r = box.current!.getBoundingClientRect()
    return { width: r.width, height: r.height, left: r.left, top: r.top }
  }

  useEffect(() => {
    const el = box.current!
    const observer = new ResizeObserver(() => setSize(measure()))
    observer.observe(el)
    // The wheel zooms the map rather than scrolling the page, which needs a listener that isn't passive
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      const s = measure()
      setView((v) => zoomRef.current(v, s, e.clientX, e.clientY, Math.exp(e.deltaY * 0.0015)))
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      observer.disconnect()
      el.removeEventListener('wheel', onWheel)
    }
  }, [])

  const startPinch = (s: Size) => {
    const [a, b] = [...pointers.current.values()]
    const mid: Point = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
    gesture.current = { kind: 'pinch', dist: Math.hypot(a[0] - b[0], a[1] - b[1]) || 1, anchor: toMap(view, s, ...mid), view }
  }

  const onDown = (e: PointerEvent<HTMLDivElement>) => {
    if ((e.target as Element).closest('button')) return
    e.currentTarget.setPointerCapture(e.pointerId)
    pointers.current.set(e.pointerId, [e.clientX, e.clientY])
    if (pointers.current.size === 2) return startPinch(measure())
    if (pointers.current.size > 2) return
    const key = (e.target as Element).closest('[data-key]')?.getAttribute('data-key') ?? null
    gesture.current = { kind: key && onDrag ? 'drag' : 'pan', key, sx: e.clientX, sy: e.clientY, view, moved: false }
  }

  const onMove = (e: PointerEvent<HTMLDivElement>) => {
    if (!pointers.current.has(e.pointerId)) return
    pointers.current.set(e.pointerId, [e.clientX, e.clientY])
    const g = gesture.current
    if (!g) return
    const s = measure()
    if (g.kind === 'pinch') {
      const [a, b] = [...pointers.current.values()]
      const mid: Point = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]
      const w = Math.min(Math.max((g.view.w * g.dist) / (Math.hypot(a[0] - b[0], a[1] - b[1]) || 1), home.w / MAX_ZOOM), home.w * 2)
      const v = { x: 0, y: 0, w, h: (g.view.h * w) / g.view.w }
      const [px, py] = toMap(v, s, ...mid)
      setView(limit({ ...v, x: g.anchor[0] - px, y: g.anchor[1] - py }))
      return
    }
    if (!g.moved && Math.hypot(e.clientX - g.sx, e.clientY - g.sy) < MOVE_PX) return
    g.moved = true
    if (g.kind === 'drag' && g.key) {
      onDrag?.(g.key, toMap(view, s, e.clientX, e.clientY))
    } else {
      const f = scaleOf(g.view, s)
      setView(limit({ ...g.view, x: g.view.x - (e.clientX - g.sx) * f, y: g.view.y - (e.clientY - g.sy) * f }))
    }
  }

  const onUp = (e: PointerEvent<HTMLDivElement>) => {
    if (!pointers.current.delete(e.pointerId)) return
    const g = gesture.current
    if (g?.kind === 'pinch') {
      // The finger left on the screen carries on panning from here
      const rest = [...pointers.current.values()][0]
      gesture.current = rest ? { kind: 'pan', key: null, sx: rest[0], sy: rest[1], view, moved: true } : null
      return
    }
    gesture.current = null
    if (!g || e.type === 'pointercancel') return
    if (g.kind === 'drag' && g.moved && g.key) onDragEnd?.(g.key)
    else if (!g.moved) onTap?.(toMap(view, measure(), e.clientX, e.clientY), g.key)
  }

  const zoomButton = (factor: number) => () => {
    const s = measure()
    setView((v) => zoomAt(v, s, s.left + s.width / 2, s.top + s.height / 2, factor))
  }

  const metre = map.metres_per_px ? 1 / map.metres_per_px : null // map pixels per metre
  const bar = metre ? niceMetres((110 * k) / metre) : null
  const grid = map.image === 'grid'

  return (
    <div
      ref={box}
      className={cx('relative touch-none select-none overflow-hidden rounded-md bg-zinc-950', crosshair ? 'cursor-crosshair' : 'cursor-grab', className)}
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerCancel={onUp}
    >
      <svg className="absolute inset-0 h-full w-full" viewBox={`${view.x} ${view.y} ${view.w} ${view.h}`}>
        {grid && metre && (
          <defs>
            <pattern id={`${uid}m`} width={metre} height={metre} patternUnits="userSpaceOnUse">
              <path d={`M ${metre} 0 L 0 0 0 ${metre}`} fill="none" stroke="#27272a" strokeWidth={k} />
            </pattern>
            <pattern id={`${uid}f`} width={metre * 5} height={metre * 5} patternUnits="userSpaceOnUse">
              <path d={`M ${metre * 5} 0 L 0 0 0 ${metre * 5}`} fill="none" stroke="#3f3f46" strokeWidth={k} />
            </pattern>
          </defs>
        )}
        <rect width={map.width} height={map.height} fill="#18181b" />
        {grid && metre && metre / k >= 6 && <rect width={map.width} height={map.height} fill={`url(#${uid}m)`} />}
        {grid && metre && <rect width={map.width} height={map.height} fill={`url(#${uid}f)`} />}
        {map.picture && <image href={map.picture} width={map.width} height={map.height} preserveAspectRatio="none" />}
        <rect width={map.width} height={map.height} fill="none" stroke="#52525b" strokeWidth={k} />
        {children?.(k)}
      </svg>
      <div className="absolute right-2 top-2 flex flex-col overflow-hidden rounded-md border border-zinc-700 bg-zinc-900/90">
        <button type="button" className="p-1.5 text-zinc-300 hover:bg-zinc-800" onClick={zoomButton(1 / 1.5)} aria-label="Zoom in" title="Zoom in">
          <Plus className="h-4 w-4" />
        </button>
        <button type="button" className="p-1.5 text-zinc-300 hover:bg-zinc-800" onClick={zoomButton(1.5)} aria-label="Zoom out" title="Zoom out">
          <Minus className="h-4 w-4" />
        </button>
        <button
          type="button"
          className="p-1.5 text-zinc-300 hover:bg-zinc-800"
          onClick={() => setView(home)}
          aria-label="Show the whole map"
          title="Show the whole map"
        >
          <Maximize className="h-4 w-4" />
        </button>
      </div>
      {bar && metre && (
        <div className="pointer-events-none absolute bottom-2 left-2 rounded bg-zinc-950/80 px-1.5 py-1 text-[10px] text-zinc-300">
          <div className="h-1 border-x border-b border-zinc-300" style={{ width: (bar * metre) / k }} />
          {bar} m
        </div>
      )}
    </div>
  )
}
