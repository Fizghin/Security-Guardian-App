import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import { Headphones, Mic } from 'lucide-react'
import { api, type CameraStatus, type TalkPlayer } from '../api'
import { micError, micProblem, PcmPlayer, peakDb, startMic } from '../lib/audio'
import { cx } from '../lib/cx'
import { Button } from './ui'

export function LevelMeter({ db, label }: { db: number | null; label: string }) {
  const pct = db == null ? 0 : Math.max(0, Math.min(100, ((db + 60) / 60) * 100))
  return (
    <div className="flex items-center gap-2" role="meter" aria-label={label} aria-valuemin={-60} aria-valuemax={0} aria-valuenow={Math.round(db ?? -60)}>
      <div className="h-1.5 w-28 overflow-hidden rounded-full bg-zinc-800">
        <div className={cx('h-full rounded-full transition-[width] duration-100', pct > 90 ? 'bg-red-500' : pct > 70 ? 'bg-amber-500' : 'bg-emerald-500')} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-14 whitespace-nowrap font-mono text-xs tabular-nums text-zinc-400">{db == null ? '–' : `${Math.round(db)} dB`}</span>
    </div>
  )
}

/** Where the owner's voice will play, from the camera's audio setting and this computer's player. */
function talkPlan(camera: CameraStatus, player: TalkPlayer): { ok: boolean; text: string } {
  const { output, link } = camera.audio
  const phone = camera.kind === 'phone' && output !== 'server'
  const computer = output !== 'device'
  const places: string[] = []
  if (phone && link) places.push('on the phone as you speak')
  if (computer && player.mode === 'live') places.push('on this computer as you speak')
  if (computer && player.mode === 'after') places.push('on this computer when you let go')
  const missing: string[] = []
  if (phone && !link) missing.push('the phone is not connected for audio')
  if (computer && !player.mode) missing.push('this computer has no audio player')
  if (!places.length) return { ok: false, text: `Can't talk: ${missing.join(' and ')}.` }
  return { ok: true, text: `Plays ${places.join(', and ')}${missing.length ? ` (${missing.join(' and ')})` : ''}.` }
}

type TalkSession = { ws: WebSocket; ready: boolean; backlog: Int16Array[]; stopMic?: () => void }

function Talk({ camera, player }: { camera: CameraStatus; player: TalkPlayer }) {
  const [phase, setPhase] = useState<'idle' | 'starting' | 'talking'>('idle')
  const [level, setLevel] = useState<number | null>(null)
  const [where, setWhere] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const session = useRef<TalkSession | null>(null)
  const plan = talkPlan(camera, player)

  const end = (s: TalkSession) => {
    s.stopMic?.()
    if (s.ws.readyState <= WebSocket.OPEN) s.ws.close() // what was sent still arrives first
    if (session.current !== s) return
    session.current = null
    setPhase('idle')
    setLevel(null)
  }
  const release = () => {
    if (session.current) end(session.current)
  }

  const press = async () => {
    if (session.current || !plan.ok) return
    const problem = micProblem()
    if (problem) {
      setError(problem)
      return
    }
    setError(null)
    setPhase('starting')
    const s: TalkSession = { ws: new WebSocket(api.talkUrl(camera.id)), ready: false, backlog: [] }
    session.current = s
    s.ws.binaryType = 'arraybuffer'
    s.ws.onmessage = (e) => {
      if (typeof e.data !== 'string') return
      const msg = JSON.parse(e.data) as { type: string; message?: string; phone?: boolean; computer?: string | null }
      if (msg.type !== 'ready') {
        setError(msg.message ?? 'Talking is not possible right now.')
        end(s)
        return
      }
      s.ready = true
      s.backlog.forEach((chunk) => s.ws.send(chunk))
      s.backlog = []
      const places = [msg.phone && 'the phone', msg.computer === 'live' && 'this computer', msg.computer === 'after' && 'this computer when you let go']
      setWhere(places.filter(Boolean).join(' and '))
      setPhase('talking')
    }
    s.ws.onclose = () => {
      if (session.current !== s) return
      if (!s.ready) setError('Could not reach Guardian to talk.')
      end(s)
    }
    try {
      const stopMic = await startMic((pcm) => {
        setLevel(peakDb(pcm, 32768))
        if (s.ready && s.ws.readyState === WebSocket.OPEN) s.ws.send(pcm)
        else if (s.backlog.length < 20) s.backlog.push(pcm)
      })
      if (session.current === s) s.stopMic = stopMic
      else stopMic() // let go while the microphone was opening
    } catch (err) {
      if (session.current === s) {
        setError(micError(err))
        end(s)
      }
    }
  }

  useEffect(() => {
    const onHidden = () => document.visibilityState === 'hidden' && session.current && end(session.current)
    document.addEventListener('visibilitychange', onHidden)
    return () => {
      document.removeEventListener('visibilitychange', onHidden)
      if (session.current) end(session.current)
    }
  }, [])

  const onPointerDown = (e: PointerEvent<HTMLButtonElement>) => {
    if (e.button !== 0) return
    e.currentTarget.setPointerCapture(e.pointerId)
    press()
  }
  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key !== ' ') return
    e.preventDefault()
    if (!e.repeat) press()
  }
  const onKeyUp = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key !== ' ') return
    e.preventDefault()
    release()
  }

  return (
    <div className="min-w-0 space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant={phase === 'idle' ? 'secondary' : 'primary'}
          icon={<Mic className="h-4 w-4" />}
          disabled={!plan.ok && phase === 'idle'}
          aria-pressed={phase !== 'idle'}
          className="touch-none select-none"
          style={{ WebkitTouchCallout: 'none' }}
          onPointerDown={onPointerDown}
          onPointerUp={release}
          onPointerCancel={release}
          onLostPointerCapture={release}
          onKeyDown={onKeyDown}
          onKeyUp={onKeyUp}
          onBlur={release}
          onContextMenu={(e) => e.preventDefault()}
        >
          {phase === 'idle' ? 'Hold to talk' : phase === 'starting' ? 'Connecting…' : 'Talking…'}
        </Button>
        {phase !== 'idle' && <LevelMeter db={level} label="Your voice" />}
      </div>
      <p className={cx('text-xs', error ? 'text-red-400' : plan.ok ? 'text-zinc-500' : 'text-amber-400')}>
        {error ?? (phase === 'talking' && where ? `Your voice is playing on ${where}.` : camera.audio.talking && phase === 'idle' ? 'Someone else is talking through this camera.' : plan.text)}
      </p>
    </div>
  )
}

function Listen({ camera }: { camera: CameraStatus }) {
  const [on, setOn] = useState(false)
  const [level, setLevel] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const session = useRef<{ ws: WebSocket; player: PcmPlayer } | null>(null)
  const lastChunk = useRef(0)

  const stop = () => {
    const s = session.current
    session.current = null
    s?.ws.close()
    s?.player.close()
    setOn(false)
    setLevel(null)
  }

  const start = () => {
    setError(null)
    const player = new PcmPlayer()
    player.resume().catch(() => {})
    const ws = new WebSocket(api.listenUrl(camera.id))
    ws.binaryType = 'arraybuffer'
    session.current = { ws, player }
    ws.onmessage = (e) => {
      if (typeof e.data === 'string') {
        const msg = JSON.parse(e.data) as { type: string; message?: string }
        if (msg.type === 'error') {
          setError(msg.message ?? 'Listening is not possible right now.')
          stop()
        }
        return
      }
      const pcm = new Int16Array(e.data as ArrayBuffer)
      player.play(pcm)
      lastChunk.current = Date.now()
      setLevel(peakDb(pcm, 32768))
    }
    ws.onclose = () => {
      if (session.current?.ws !== ws) return
      setError('Listening stopped: the connection to Guardian was lost.')
      stop()
    }
    setOn(true)
  }

  useEffect(() => {
    if (!on) return
    const timer = setInterval(() => Date.now() - lastChunk.current > 800 && setLevel(null), 400)
    return () => clearInterval(timer)
  }, [on])

  useEffect(
    () => () => {
      session.current?.ws.close()
      session.current?.player.close()
    },
    [],
  )

  const mic = camera.audio.mic
  return (
    <div className="min-w-0 space-y-2">
      <div className="flex flex-wrap items-center gap-3">
        <Button
          variant={on ? 'primary' : 'secondary'}
          icon={<Headphones className="h-4 w-4" />}
          aria-pressed={on}
          disabled={!on && !mic}
          title={!mic ? "The phone's microphone is off" : undefined}
          onClick={on ? stop : start}
        >
          {on ? 'Stop listening' : 'Listen'}
        </Button>
        {on && <LevelMeter db={level} label="Sound at the phone" />}
      </div>
      <p className={cx('text-xs', error ? 'text-red-400' : on && !mic ? 'text-amber-400' : 'text-zinc-500')}>
        {error ??
          (on
            ? mic
              ? 'Playing the phone’s microphone. The phone shows that you are listening.'
              : 'The phone’s microphone went off.'
            : mic
              ? 'Hear what the phone hears.'
              : 'The phone’s microphone is off. Allow it on the phone’s camera page.')}
      </p>
    </div>
  )
}

export default function LiveAudio({ camera, player }: { camera: CameraStatus; player: TalkPlayer }) {
  return (
    <div className="border-t border-zinc-800 pt-4 md:col-span-2">
      <div className="label">Live audio</div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Talk camera={camera} player={player} />
        {camera.kind === 'phone' && <Listen camera={camera} />}
      </div>
    </div>
  )
}
