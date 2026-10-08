import { useEffect, useRef, useState } from 'react'
import { Camera, Download, Maximize2, VideoOff } from 'lucide-react'
import { api } from '../api'
import { cx } from '../lib/cx'
import { href } from '../lib/route'
import { useStatus } from '../lib/status'

const STALE_MS = 3000
const CAPTION_SECONDS = 12

export default function LiveVideo({ cameraName }: { cameraName: string }) {
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
      ws = new WebSocket(api.streamUrl())
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
  }, [])

  const cam = status?.camera
  const showFrame = hasFrame && !stale
  const caption =
    status?.last_message && status.last_message_time && status.server_time - status.last_message_time < CAPTION_SECONDS
      ? status.last_message
      : null

  let problem: { title: string; detail: string } | null = null
  if (cam && !cam.connected) {
    problem = {
      title: cam.kind === 'none' ? 'Camera disabled' : 'No camera signal',
      detail: cam.error ?? 'Waiting for the camera…',
    }
  } else if (!socketOpen) {
    problem = { title: 'Connecting to video…', detail: 'The dashboard is reconnecting to the server.' }
  } else if (!showFrame) {
    problem = { title: 'Waiting for video…', detail: 'The camera is connected but no frames have arrived yet.' }
  }

  return (
    <div ref={boxRef} className="relative aspect-video w-full overflow-hidden rounded-lg border border-zinc-800 bg-black">
      <img ref={imgRef} alt="Live camera" className={cx('h-full w-full object-contain', !showFrame && 'opacity-30')} />

      <div className="absolute inset-x-0 top-0 flex items-center justify-between gap-2 bg-gradient-to-b from-black/70 to-transparent px-3 py-2 text-xs">
        <div className="flex items-center gap-2 font-medium">
          <span className={cx('h-2 w-2 rounded-full', showFrame && cam?.connected ? 'bg-red-500' : 'bg-zinc-500')} />
          {cameraName}
          {showFrame && cam?.connected && <span className="text-zinc-400">Live</span>}
          {status?.recording.active && (
            <span className="rounded bg-red-600 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-white">REC</span>
          )}
        </div>
        <div className="flex items-center gap-1">
          {cam?.connected && (
            <span className="mr-1 tabular-nums text-zinc-300">
              {cam.width}×{cam.height} · {status?.pipeline.fps ?? 0} fps
            </span>
          )}
          <a
            href={api.snapshotUrl()}
            download="guardian-snapshot.jpg"
            className="rounded p-1.5 text-zinc-200 hover:bg-white/10"
            title="Save snapshot"
          >
            <Download className="h-4 w-4" />
          </a>
          <button
            type="button"
            className="rounded p-1.5 text-zinc-200 hover:bg-white/10"
            title="Full screen"
            onClick={() => boxRef.current?.requestFullscreen?.()}
          >
            <Maximize2 className="h-4 w-4" />
          </button>
        </div>
      </div>

      {problem && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-6 text-center">
          {cam && !cam.connected ? <VideoOff className="h-8 w-8 text-zinc-500" /> : <Camera className="h-8 w-8 text-zinc-500" />}
          <p className="font-medium text-zinc-200">{problem.title}</p>
          <p className="max-w-md text-xs text-zinc-400">{problem.detail}</p>
          {cam && !cam.connected && (
            <a href={href('settings', 'camera')} className="mt-1 text-xs font-medium text-blue-400 hover:text-blue-300">
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
