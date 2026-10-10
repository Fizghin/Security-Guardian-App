import { useEffect, useRef, useState } from 'react'
import { Battery, BatteryCharging, Camera, Download, Maximize2, Mic, Smartphone, VideoOff } from 'lucide-react'
import { api, type CameraStatus } from '../api'
import { cx } from '../lib/cx'
import { LEVELS } from '../lib/format'
import { href } from '../lib/route'
import { useStatus } from '../lib/status'

const STALE_MS = 3000
const CAPTION_SECONDS = 12

export default function LiveVideo({
  camera,
  compact = false,
  selected = false,
  onSelect,
}: {
  camera: CameraStatus
  compact?: boolean
  selected?: boolean
  onSelect?: () => void
}) {
  const { status } = useStatus()
  const imgRef = useRef<HTMLImageElement>(null)
  const boxRef = useRef<HTMLDivElement>(null)
  const lastFrame = useRef(0)
  const [socketOpen, setSocketOpen] = useState(false)
  const [hasFrame, setHasFrame] = useState(false)
  const [stale, setStale] = useState(false)

  useEffect(() => {
    let ws: WebSocket | null = null
    let retry: ReturnType<typeof setTimeout> | undefined
    let delay = 1000
    let url: string | null = null
    let closed = false

    const connect = () => {
      ws = new WebSocket(api.streamUrl(camera.id))
      ws.binaryType = 'blob'
      ws.onopen = () => {
        delay = 1000
        setSocketOpen(true)
      }
      ws.onmessage = (e) => {
        if (!(e.data instanceof Blob) || !imgRef.current) return
        const next = URL.createObjectURL(e.data)
        imgRef.current.src = next
        if (url) URL.revokeObjectURL(url)
        url = next
        lastFrame.current = Date.now()
        setHasFrame(true)
      }
      ws.onclose = () => {
        setSocketOpen(false)
        if (!closed) {
          retry = setTimeout(connect, delay)
          delay = Math.min(delay * 2, 10000)
        }
      }
      ws.onerror = () => ws?.close()
    }
    connect()

    const staleTimer = setInterval(() => setStale(Date.now() - lastFrame.current > STALE_MS), 1000)
    return () => {
      closed = true
      clearTimeout(retry)
      clearInterval(staleTimer)
      ws?.close()
      if (url) URL.revokeObjectURL(url)
    }
  }, [camera.id])

  const showFrame = hasFrame && !stale && camera.connected
  const level = camera.threat_level
  const caption =
    !compact && camera.last_message && camera.last_message_time && status && status.server_time - camera.last_message_time < CAPTION_SECONDS
      ? camera.last_message
      : null
  const phone = camera.kind === 'phone' ? camera.phone : undefined

  let problem: { title: string; detail: string } | null = null
  if (!camera.connected) {
    problem = {
      title: camera.kind === 'none' ? 'Camera disabled' : camera.kind === 'phone' ? 'Phone not streaming' : 'No camera signal',
      detail: camera.error ?? 'Waiting for the camera…',
    }
  } else if (!socketOpen) {
    problem = { title: 'Connecting to video…', detail: 'The dashboard is reconnecting to the server.' }
  } else if (!showFrame) {
    problem = { title: 'Waiting for video…', detail: 'The camera is connected but no frames have arrived yet.' }
  }

  return (
    <div
      ref={boxRef}
      onClick={onSelect}
      className={cx(
        'force-dark group relative aspect-video w-full overflow-hidden rounded-2xl border bg-black shadow-lg shadow-black/20 transition-[border-color,box-shadow]',
        level >= 3 ? 'border-red-600 shadow-red-900/40 ring-2 ring-red-600/40' : selected ? 'border-blue-500 ring-2 ring-blue-500/30' : 'border-zinc-800 hover:border-zinc-700',
        onSelect && 'cursor-pointer',
      )}
    >
      <img
        ref={imgRef}
        alt={`${camera.name} live`}
        className={cx('h-full w-full object-contain', !hasFrame && 'invisible', hasFrame && !showFrame && 'opacity-30')}
      />

      <div className={cx('absolute inset-x-0 top-0 flex items-center justify-between gap-2 bg-gradient-to-b from-black/70 to-transparent', compact ? 'px-2 py-1.5 text-[11px]' : 'px-3 py-2 text-xs')}>
        <div className="flex min-w-0 items-center gap-2 font-medium">
          <span className={cx('h-2 w-2 shrink-0 rounded-full', showFrame ? 'bg-red-500' : 'bg-zinc-500')} />
          {phone && <Smartphone className="h-3.5 w-3.5 shrink-0 text-zinc-300" />}
          <span className="truncate">{camera.name}</span>
          {level > 0 && (
            <span className={cx('shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold text-white', LEVELS[level].bg)}>
              {compact ? `L${level}` : `Level ${level}`}
            </span>
          )}
          {camera.recording.active && (
            <span className="shrink-0 rounded bg-red-600 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-white">REC</span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {camera.audio.mic && (
            <span className="mr-1 flex items-center gap-1 text-zinc-300" title={`Microphone live${camera.audio.level_db != null ? ` · ${Math.round(camera.audio.level_db)} dB` : ''}`}>
              <Mic className="h-3.5 w-3.5" />
              <span className="h-1 w-4 overflow-hidden rounded-full bg-white/20">
                <span className="block h-full rounded-full bg-emerald-400" style={{ width: `${Math.max(0, Math.min(100, (((camera.audio.level_db ?? -60) + 60) / 60) * 100))}%` }} />
              </span>
            </span>
          )}
          {phone?.battery != null && (
            <span className={cx('mr-1 flex items-center gap-0.5 tabular-nums', phone.battery <= 20 && !phone.charging ? 'text-amber-400' : 'text-zinc-300')}>
              {phone.charging ? <BatteryCharging className="h-3.5 w-3.5" /> : <Battery className="h-3.5 w-3.5" />}
              {phone.battery}%
            </span>
          )}
          {!compact && camera.connected && (
            <span className="mr-1 hidden tabular-nums text-zinc-300 sm:inline">
              {camera.width}×{camera.height} · {camera.pipeline.fps} fps
            </span>
          )}
          {!compact && (
            <>
              <a href={api.snapshotUrl(camera.id)} download={`${camera.id}-snapshot.jpg`} className="rounded p-1.5 text-zinc-200 hover:bg-white/10" title="Save snapshot">
                <Download className="h-4 w-4" />
              </a>
              <button type="button" className="rounded p-1.5 text-zinc-200 hover:bg-white/10" title="Full screen" onClick={() => boxRef.current?.requestFullscreen?.()}>
                <Maximize2 className="h-4 w-4" />
              </button>
            </>
          )}
        </div>
      </div>

      {problem && (
        <div className={cx('absolute inset-0 flex flex-col items-center justify-center gap-1.5 text-center', compact ? 'p-3' : 'p-6')}>
          {!camera.connected ? <VideoOff className={cx('text-zinc-500', compact ? 'h-5 w-5' : 'h-8 w-8')} /> : <Camera className={cx('text-zinc-500', compact ? 'h-5 w-5' : 'h-8 w-8')} />}
          <p className={cx('font-medium text-zinc-200', compact && 'text-xs')}>{problem.title}</p>
          {!compact && <p className="max-w-md text-xs text-zinc-400">{problem.detail}</p>}
          {!compact && !camera.connected && (
            <a href={href('settings', 'cameras')} className="mt-1 text-xs font-medium text-blue-400 hover:text-blue-300">
              Camera settings
            </a>
          )}
        </div>
      )}

      {caption && (
        <div className="absolute inset-x-0 bottom-10 flex justify-center px-4">
          <p className="max-w-2xl rounded-md bg-black/75 px-3 py-2 text-center text-sm text-zinc-100">{caption}</p>
        </div>
      )}
    </div>
  )
}
