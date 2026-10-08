import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Battery, BatteryCharging, Copy, FileVideo, Pencil, Plus, QrCode, ScanSearch, Smartphone, Trash2, Usb, Wifi } from 'lucide-react'
import { api, type AudioOutput, type CameraConfig, type CameraStatus, type Settings, type SourceKind } from '../api'
import { cx } from '../lib/cx'
import { useStatus } from '../lib/status'
import { errorMessage, useToast } from '../lib/toast'
import { Badge, Button, Card, ConfirmDialog, Dot, ErrorNote, Field, Modal, Slider } from './ui'

const KIND_ICON: Record<SourceKind, typeof Usb> = {
  phone: Smartphone,
  url: Wifi,
  index: Usb,
  auto: Usb,
  file: FileVideo,
  none: Usb,
}

const AUDIO_OPTIONS: { value: AudioOutput; label: string }[] = [
  { value: 'device', label: 'The phone' },
  { value: 'server', label: 'This computer' },
  { value: 'both', label: 'Both' },
]

const APP_PRESETS = [
  { id: 'ipwebcam', label: 'IP Webcam (Android app)', template: 'http://{ip}:8080/video', hint: 'In the app tap "Start server"; the address is shown on screen.' },
  { id: 'droidcam', label: 'DroidCam (Android / iPhone app)', template: 'http://{ip}:4747/video', hint: 'Open DroidCam; it shows the Wi-Fi IP.' },
  { id: 'rtsp', label: 'Network camera (RTSP)', template: 'rtsp://user:password@{ip}:554/stream1', hint: 'Check the camera manual for its RTSP path.' },
  { id: 'custom', label: 'Other address', template: '', hint: 'Any MJPEG/HTTP or RTSP stream URL.' },
] as const

function sourceSummary(c: CameraConfig) {
  if (c.kind === 'phone') return 'Phone camera (browser)'
  if (c.kind === 'index') return `Camera ${c.source} on this computer`
  if (c.kind === 'auto') return 'First camera on this computer'
  if (c.kind === 'none') return 'Disabled'
  return c.source
}

function LiveState({ live }: { live?: CameraStatus }) {
  if (!live) return <span className="text-zinc-500">Turned off</span>
  if (live.connected) {
    return (
      <span className="text-zinc-300">
        Live · {live.width}×{live.height} · {live.pipeline.fps} fps
      </span>
    )
  }
  return <span className="text-amber-400">{live.error ?? 'Connecting…'}</span>
}

function PhoneBattery({ live }: { live?: CameraStatus }) {
  const b = live?.phone?.battery
  if (b == null) return null
  const Icon = live?.phone?.charging ? BatteryCharging : Battery
  return (
    <span className={cx('inline-flex items-center gap-1 tabular-nums', b <= 20 && !live?.phone?.charging ? 'text-amber-400' : 'text-zinc-400')}>
      <Icon className="h-3.5 w-3.5" />
      {b}%
    </span>
  )
}

// ---- pairing a phone ---------------------------------------------------------------
export function PairingPanel({ camera, onReset }: { camera: CameraConfig; onReset?: () => void }) {
  const notify = useToast()
  const { status } = useStatus()
  const [pairing, setPairing] = useState<{ urls: string[]; qr_svg: string | null; port: number } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirmReset, setConfirmReset] = useState(false)
  const [busy, setBusy] = useState(false)
  const live = status?.cameras.find((c) => c.id === camera.id)

  const load = useCallback(async () => {
    try {
      setPairing(await api.pairing(camera.id))
      setError(null)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [camera.id])

  useEffect(() => {
    load()
  }, [load, camera.token])

  const copy = async (url: string) => {
    try {
      await navigator.clipboard.writeText(url)
      notify('Link copied', 'success')
    } catch {
      notify('Copy failed; select the link and copy it', 'error')
    }
  }

  const reset = async () => {
    setBusy(true)
    try {
      await api.resetPhoneLink(camera.id)
      notify('New link created; the old one no longer works', 'success')
      setConfirmReset(false)
      onReset?.()
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(false)
    }
  }

  if (error) return <ErrorNote>{error}</ErrorNote>
  if (!pairing) return <p className="text-sm text-zinc-500">Preparing the link…</p>
  if (!pairing.urls.length) {
    return <ErrorNote>This computer has no network address a phone could reach. Connect it to Wi-Fi or a network cable.</ErrorNote>
  }

  return (
    <div className="grid gap-5 sm:grid-cols-[auto_1fr]">
      <div className="space-y-2">
        {pairing.qr_svg && (
          <div className="rounded-lg bg-white p-2 [&>svg]:h-48 [&>svg]:w-48" dangerouslySetInnerHTML={{ __html: pairing.qr_svg }} />
        )}
        <div className={cx('flex items-center justify-center gap-2 text-sm', live?.connected ? 'text-emerald-400' : 'text-zinc-400')}>
          <Dot className={live?.connected ? 'bg-emerald-500' : live?.phone?.online ? 'bg-amber-500' : 'bg-zinc-600 animate-pulse'} />
          {live?.connected ? 'Phone connected' : live?.phone?.online ? 'Phone open, camera not started' : 'Waiting for the phone…'}
        </div>
      </div>
      <div className="min-w-0 space-y-3 text-sm">
        <ol className="list-decimal space-y-1.5 pl-5 text-zinc-300">
          <li>Connect the phone to the same Wi-Fi as this computer.</li>
          <li>Scan the code with the phone's camera, or type the link into its browser.</li>
          <li>
            The browser warns that the connection is not private: this computer made its own certificate. Choose{' '}
            <span className="text-zinc-100">Advanced → Proceed</span> (Android) or{' '}
            <span className="text-zinc-100">Show Details → visit this website</span> (iPhone).
          </li>
          <li>Tap <span className="text-zinc-100">Start camera</span> and allow camera access. Keep the phone plugged in.</li>
        </ol>
        <div className="space-y-1.5">
          {pairing.urls.map((url) => (
            <div key={url} className="flex items-center gap-2">
              <code className="min-w-0 flex-1 truncate rounded bg-zinc-950 px-2 py-1.5 font-mono text-xs text-zinc-300" title={url}>
                {url}
              </code>
              <Button size="sm" variant="ghost" icon={<Copy className="h-3.5 w-3.5" />} onClick={() => copy(url)} aria-label="Copy link" />
            </div>
          ))}
        </div>
        <p className="hint">
          Old phone browser can't open the camera? Use an IP camera app instead (Add camera → Phone app). Anyone with this
          link on your network can send video as this camera; create a new link if it leaks.
        </p>
        <Button size="sm" variant="ghost" onClick={() => setConfirmReset(true)}>
          Create new link
        </Button>
      </div>
      <ConfirmDialog
        open={confirmReset}
        title="Create a new pairing link?"
        message="The phone using the current link disconnects and has to scan the new code."
        confirmLabel="New link"
        busy={busy}
        onConfirm={reset}
        onCancel={() => setConfirmReset(false)}
      />
    </div>
  )
}

// ---- add / edit ------------------------------------------------------------------------
function SourceTester({ source }: { source: string }) {
  const [result, setResult] = useState<{ ok: boolean; error?: string; preview?: string; width?: number; height?: number } | null>(null)
  const [busy, setBusy] = useState(false)
  const test = async () => {
    setBusy(true)
    setResult(null)
    try {
      setResult(await api.testSource(source))
    } catch (err) {
      setResult({ ok: false, error: errorMessage(err) })
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="space-y-2">
      <Button size="sm" onClick={test} loading={busy} disabled={!source.trim()}>
        Test connection
      </Button>
      {result?.ok && (
        <div className="space-y-1">
          <img src={result.preview} alt="Test frame" className="w-full max-w-sm rounded border border-zinc-800" />
          <p className="text-xs text-emerald-400">
            Connected · {result.width}×{result.height}
          </p>
        </div>
      )}
      {result && !result.ok && <ErrorNote>{result.error}</ErrorNote>}
    </div>
  )
}

type Step = 'choose' | 'phone' | 'app' | 'webcam' | 'file' | 'pair'

function Choice({ icon, title, children, onClick }: { icon: ReactNode; title: string; children: ReactNode; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className="flex gap-3 rounded-lg border border-zinc-800 p-3 text-left transition-colors hover:border-zinc-600 hover:bg-zinc-800/40">
      <span className="mt-0.5 text-zinc-300">{icon}</span>
      <span>
        <span className="block text-sm font-medium text-zinc-100">{title}</span>
        <span className="mt-0.5 block text-xs text-zinc-400">{children}</span>
      </span>
    </button>
  )
}

function AddCameraDialog({ open, count, onClose, onAdded }: { open: boolean; count: number; onClose: () => void; onAdded: () => void }) {
  const notify = useToast()
  const [step, setStep] = useState<Step>('choose')
  const [name, setName] = useState('')
  const [audio, setAudio] = useState<AudioOutput>('device')
  const [preset, setPreset] = useState<(typeof APP_PRESETS)[number]['id']>('ipwebcam')
  const [ip, setIp] = useState('')
  const [url, setUrl] = useState('')
  const [index, setIndex] = useState<string>('')
  const [path, setPath] = useState('')
  const [found, setFound] = useState<{ index: number; width: number; height: number; in_use?: boolean }[] | null>(null)
  const [scanning, setScanning] = useState(false)
  const [busy, setBusy] = useState(false)
  const [created, setCreated] = useState<CameraConfig | null>(null)

  const presetDef = APP_PRESETS.find((p) => p.id === preset)!
  const appUrl = preset === 'ipwebcam' || preset === 'droidcam' ? presetDef.template.replace('{ip}', ip.trim()) : url

  const close = () => {
    setStep('choose')
    setName('')
    setIp('')
    setUrl('')
    setIndex('')
    setPath('')
    setFound(null)
    setCreated(null)
    onClose()
  }

  const go = (s: Step, defaultName: string) => {
    setStep(s)
    setName(defaultName)
  }

  const create = async (source: string) => {
    setBusy(true)
    try {
      const cam = await api.addCamera({ name: name.trim(), source, audio: source === 'phone' ? audio : undefined })
      onAdded()
      if (source === 'phone') {
        setCreated(cam)
        setStep('pair')
      } else {
        notify(`${cam.name} added`, 'success')
        close()
      }
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(false)
    }
  }

  const scan = async () => {
    setScanning(true)
    try {
      setFound((await api.scanCameras()).cameras)
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setScanning(false)
    }
  }

  const nameField = (
    <Field label="Name" hint="Shown on the video, in events and in what the guardian says">
      <input className="input" value={name} maxLength={40} onChange={(e) => setName(e.target.value)} />
    </Field>
  )

  const titles: Record<Step, string> = {
    choose: 'Add a camera',
    phone: 'Phone as a camera',
    app: 'Phone app or network camera',
    webcam: 'Camera on this computer',
    file: 'Video file',
    pair: `Pair ${created?.name ?? 'the phone'}`,
  }

  return (
    <Modal
      open={open}
      onClose={close}
      title={titles[step]}
      wide={step === 'pair'}
      footer={
        step === 'choose' ? undefined : step === 'pair' ? (
          <Button variant="primary" onClick={close}>
            Done
          </Button>
        ) : (
          <>
            <Button variant="ghost" onClick={() => setStep('choose')}>
              Back
            </Button>
            <Button
              variant="primary"
              loading={busy}
              disabled={
                !name.trim() ||
                (step === 'app' && !appUrl.trim()) ||
                (step === 'webcam' && index === '') ||
                (step === 'file' && !path.trim())
              }
              onClick={() => create(step === 'phone' ? 'phone' : step === 'app' ? appUrl.trim() : step === 'webcam' ? index : path.trim())}
            >
              {step === 'phone' ? 'Create pairing code' : 'Add camera'}
            </Button>
          </>
        )
      }
    >
      {step === 'choose' && (
        <div className="grid gap-2">
          <Choice icon={<Smartphone className="h-5 w-5" />} title="Phone (no app needed)" onClick={() => go('phone', `Phone ${count + 1}`)}>
            Android 5+ with Chrome or iPhone (iOS 11+) in Safari. Warnings and the siren can play on the phone.
          </Choice>
          <Choice icon={<Wifi className="h-5 w-5" />} title="Phone app or network camera" onClick={() => go('app', `Camera ${count + 1}`)}>
            For older phones: IP Webcam or DroidCam. Also IP/RTSP security cameras.
          </Choice>
          <Choice icon={<Usb className="h-5 w-5" />} title="Webcam on this computer" onClick={() => go('webcam', `Webcam ${count + 1}`)}>
            Built-in or USB camera, or a phone connected as a webcam.
          </Choice>
          <Choice icon={<FileVideo className="h-5 w-5" />} title="Video file" onClick={() => go('file', 'Test video')}>
            Loops a recording, useful for trying settings out.
          </Choice>
        </div>
      )}

      {step === 'phone' && (
        <div className="space-y-4">
          {nameField}
          <Field label="Play warnings and the siren on">
            <select className="input" value={audio} onChange={(e) => setAudio(e.target.value as AudioOutput)}>
              {AUDIO_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <p className="hint">Next you scan a code with the phone. Old phones whose browser can't open the camera can use the phone-app option instead.</p>
        </div>
      )}

      {step === 'app' && (
        <div className="space-y-4">
          {nameField}
          <Field label="App or camera" hint={presetDef.hint}>
            <select
              className="input"
              value={preset}
              onChange={(e) => {
                const p = APP_PRESETS.find((x) => x.id === e.target.value)!
                setPreset(p.id)
                if (p.id === 'rtsp' || p.id === 'custom') setUrl(p.template.replace('{ip}', ip.trim() || '192.168.1.50'))
              }}
            >
              {APP_PRESETS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </Field>
          {preset === 'ipwebcam' || preset === 'droidcam' ? (
            <Field label="Phone's IP address" hint={appUrl && ip ? `Guardian will open ${appUrl}` : 'e.g. 192.168.1.23'}>
              <input className="input font-mono" value={ip} onChange={(e) => setIp(e.target.value)} placeholder="192.168.1.23" />
            </Field>
          ) : (
            <Field label="Stream address">
              <input className="input font-mono" value={url} onChange={(e) => setUrl(e.target.value)} />
            </Field>
          )}
          <SourceTester source={appUrl} />
        </div>
      )}

      {step === 'webcam' && (
        <div className="space-y-4">
          {nameField}
          <div className="space-y-2">
            <Button size="sm" icon={<ScanSearch className="h-4 w-4" />} loading={scanning} onClick={scan}>
              Find cameras
            </Button>
            {found?.length === 0 && <p className="text-xs text-zinc-500">No cameras found. Check the cable and that no other app is using it.</p>}
            <div className="grid gap-1.5">
              {found?.map((c) => (
                <label key={c.index} className={cx('flex items-center gap-2 rounded-md border px-3 py-2 text-sm', index === String(c.index) ? 'border-blue-500' : 'border-zinc-800', c.in_use && 'opacity-60')}>
                  <input type="radio" name="cam" className="accent-blue-500" disabled={c.in_use} checked={index === String(c.index)} onChange={() => setIndex(String(c.index))} />
                  Camera {c.index}
                  {c.width > 0 && <span className="text-zinc-500">{c.width}×{c.height}</span>}
                  {c.in_use && <Badge>already added</Badge>}
                </label>
              ))}
            </div>
          </div>
        </div>
      )}

      {step === 'file' && (
        <div className="space-y-4">
          {nameField}
          <Field label="Path on this computer" hint="The video plays in a loop">
            <input className="input font-mono" value={path} onChange={(e) => setPath(e.target.value)} placeholder="C:\Videos\test.mp4" />
          </Field>
          <SourceTester source={path} />
        </div>
      )}

      {step === 'pair' && created && <PairingPanel camera={created} />}
    </Modal>
  )
}

function EditCameraDialog({ camera, onClose, onSaved }: { camera: CameraConfig | null; onClose: () => void; onSaved: () => void }) {
  const notify = useToast()
  const [name, setName] = useState(camera?.name ?? '')
  const [source, setSource] = useState(camera?.source ?? '')
  const [audio, setAudio] = useState<AudioOutput>(camera?.audio ?? 'server')
  const [busy, setBusy] = useState(false)
  if (!camera) return null
  const phone = camera.kind === 'phone'

  const save = async () => {
    setBusy(true)
    try {
      await api.updateCamera(camera.id, { name: name.trim(), ...(phone ? { audio } : { source: source.trim() }) })
      notify('Camera saved', 'success')
      onSaved()
      onClose()
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={`Edit ${camera.name}`}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={busy} disabled={!name.trim() || (!phone && !source.trim())} onClick={save}>
            Save
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field label="Name">
          <input className="input" value={name} maxLength={40} onChange={(e) => setName(e.target.value)} />
        </Field>
        {phone ? (
          <Field label="Play warnings and the siren on">
            <select className="input" value={audio} onChange={(e) => setAudio(e.target.value as AudioOutput)}>
              {AUDIO_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
        ) : (
          <>
            <Field label="Source" hint="auto, a camera number (0, 1…), a stream URL, or a video file path">
              <input className="input font-mono" value={source} onChange={(e) => setSource(e.target.value)} />
            </Field>
            <SourceTester source={source} />
          </>
        )}
      </div>
    </Modal>
  )
}

// ---- section ------------------------------------------------------------------------------
export default function CamerasSection({ phoneSettings, savePhone }: { phoneSettings: Settings['phone']; savePhone: (p: Settings['phone']) => Promise<boolean> }) {
  const notify = useToast()
  const { status, refresh } = useStatus()
  const [cams, setCams] = useState<CameraConfig[] | null>(null)
  const [phoneInfo, setPhoneInfo] = useState<{ enabled: boolean; port: number; addresses: string[] } | null>(null)
  const [adding, setAdding] = useState(false)
  const [editing, setEditing] = useState<CameraConfig | null>(null)
  const [pairing, setPairing] = useState<CameraConfig | null>(null)
  const [removing, setRemoving] = useState<CameraConfig | null>(null)
  const [busy, setBusy] = useState(false)
  const [phoneDraft, setPhoneDraft] = useState(phoneSettings)

  const load = useCallback(async () => {
    try {
      const r = await api.cameras()
      setCams(r.cameras)
      setPhoneInfo(r.phone)
      setPairing((p) => (p ? r.cameras.find((c) => c.id === p.id) ?? null : null))
    } catch (err) {
      notify(errorMessage(err), 'error')
    }
  }, [notify])

  useEffect(() => {
    load()
  }, [load])

  const changed = async () => {
    await load()
    refresh()
  }

  const toggle = async (c: CameraConfig) => {
    try {
      await api.updateCamera(c.id, { enabled: !c.enabled })
      notify(`${c.name} ${c.enabled ? 'turned off' : 'turned on'}`, 'success')
      changed()
    } catch (err) {
      notify(errorMessage(err), 'error')
    }
  }

  const remove = async () => {
    if (!removing) return
    setBusy(true)
    try {
      await api.deleteCamera(removing.id)
      notify(`${removing.name} removed`, 'success')
      setRemoving(null)
      changed()
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(false)
    }
  }

  const phoneDirty = JSON.stringify(phoneDraft) !== JSON.stringify(phoneSettings)
  const hasPhones = cams?.some((c) => c.kind === 'phone')

  return (
    <Card
      id="cameras"
      title="Cameras"
      className="scroll-mt-4"
      actions={
        <Button size="sm" variant="primary" icon={<Plus className="h-4 w-4" />} onClick={() => setAdding(true)}>
          Add camera
        </Button>
      }
    >
      <div className="divide-y divide-zinc-800 rounded-md border border-zinc-800">
        {cams?.length === 0 && <p className="p-4 text-sm text-zinc-400">No cameras yet. Add a webcam, an IP camera or an old phone.</p>}
        {cams?.map((c) => {
          const live = status?.cameras.find((s) => s.id === c.id)
          const Icon = KIND_ICON[c.kind]
          return (
            <div key={c.id} className="flex flex-wrap items-center gap-3 p-3">
              <Icon className={cx('h-5 w-5 shrink-0', c.enabled ? 'text-zinc-300' : 'text-zinc-600')} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 text-sm font-medium">
                  <Dot className={!c.enabled ? 'bg-zinc-600' : live?.connected ? 'bg-emerald-500' : 'bg-amber-500'} />
                  <span className={c.enabled ? 'text-zinc-100' : 'text-zinc-500'}>{c.name}</span>
                  <PhoneBattery live={live} />
                </div>
                <div className="mt-0.5 truncate text-xs text-zinc-500" title={c.source}>
                  {sourceSummary(c)} · {c.enabled ? <LiveState live={live} /> : 'Turned off'}
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                {c.kind === 'phone' && (
                  <Button size="sm" variant="ghost" icon={<QrCode className="h-4 w-4" />} onClick={() => setPairing(c)}>
                    Pair
                  </Button>
                )}
                <Button size="sm" variant="ghost" onClick={() => toggle(c)}>
                  {c.enabled ? 'Turn off' : 'Turn on'}
                </Button>
                <Button size="sm" variant="ghost" icon={<Pencil className="h-4 w-4" />} onClick={() => setEditing(c)} aria-label={`Edit ${c.name}`} />
                <Button size="sm" variant="ghost" icon={<Trash2 className="h-4 w-4" />} onClick={() => setRemoving(c)} aria-label={`Remove ${c.name}`} />
              </div>
            </div>
          )
        })}
      </div>

      {phoneInfo && !phoneInfo.enabled && (
        <p className="mt-3 text-xs text-amber-400">Phone cameras are turned off (PHONE_PORT=0 in backend/.env).</p>
      )}

      {hasPhones && (
        <div className="mt-5 space-y-4 border-t border-zinc-800 pt-4">
          <div className="text-sm font-medium text-zinc-200">Phone video quality</div>
          <div className="grid gap-5 sm:grid-cols-3">
            <Slider label="Frames per second" value={phoneDraft.fps} min={1} max={15} onChange={(v) => setPhoneDraft({ ...phoneDraft, fps: v })} hint="Lower saves battery and Wi-Fi" />
            <Slider label="Picture width" value={phoneDraft.max_width} min={320} max={1920} step={160} format={(v) => `${v}px`} onChange={(v) => setPhoneDraft({ ...phoneDraft, max_width: v })} />
            <Slider label="JPEG quality" value={phoneDraft.quality} min={0.3} max={0.95} step={0.05} format={(v) => `${Math.round(v * 100)}%`} onChange={(v) => setPhoneDraft({ ...phoneDraft, quality: v })} />
          </div>
          <div className="flex justify-end gap-2">
            {phoneDirty && (
              <Button variant="ghost" onClick={() => setPhoneDraft(phoneSettings)}>
                Discard
              </Button>
            )}
            <Button variant="primary" disabled={!phoneDirty} onClick={() => savePhone(phoneDraft)}>
              Save
            </Button>
          </div>
        </div>
      )}

      <AddCameraDialog open={adding} count={cams?.length ?? 0} onClose={() => setAdding(false)} onAdded={changed} />
      <EditCameraDialog key={editing?.id ?? ''} camera={editing} onClose={() => setEditing(null)} onSaved={changed} />
      <Modal open={!!pairing} onClose={() => setPairing(null)} title={`Pair ${pairing?.name ?? ''}`} wide>
        {pairing && <PairingPanel camera={pairing} onReset={load} />}
      </Modal>
      <ConfirmDialog
        open={!!removing}
        title={`Remove ${removing?.name ?? ''}?`}
        message="The camera stops recording and its pairing link stops working. Recordings and events from it are kept."
        confirmLabel="Remove"
        busy={busy}
        onConfirm={remove}
        onCancel={() => setRemoving(null)}
      />
    </Card>
  )
}
