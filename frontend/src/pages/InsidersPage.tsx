import { useRef, useState, type FormEvent } from 'react'
import { Camera, ImagePlus, ScanFace, Trash2, UserPlus, Users, X } from 'lucide-react'
import { api, type CheckedFace, type Insider, type LiveFace, type UploadResult } from '../api'
import { Badge, Button, Card, ConfirmDialog, Empty, ErrorNote, Field } from '../components/ui'
import { cx } from '../lib/cx'
import { href } from '../lib/route'
import { useStatus } from '../lib/status'
import { errorMessage, useToast } from '../lib/toast'
import { usePoll } from '../lib/usePoll'

function UploadResults({ results }: { results: UploadResult[] }) {
  return (
    <ul className="mt-3 space-y-1 text-xs">
      {results.map((r, i) => (
        <li key={i} className={r.ok ? (r.warning ? 'text-amber-400' : 'text-emerald-400') : 'text-red-400'}>
          {r.file}: {r.ok ? `added${r.warning ? `. ${r.warning}` : ''}` : r.error}
        </li>
      ))}
    </ul>
  )
}

function CaptureFromCamera({ names, onAdded }: { names: string[]; onAdded: () => void }) {
  const notify = useToast()
  const { status } = useStatus()
  const cameras = status?.cameras.filter((c) => c.connected) ?? []
  const [cameraId, setCameraId] = useState('')
  const [faces, setFaces] = useState<LiveFace[] | null>(null)
  const [chosen, setChosen] = useState<number | null>(null)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState<'snap' | 'add' | null>(null)
  const camera = cameras.find((c) => c.id === cameraId) ?? cameras[0]

  const snap = async () => {
    if (!camera) return
    setBusy('snap')
    setChosen(null)
    try {
      const { faces } = await api.cameraFaces(camera.id)
      setFaces(faces)
      const first = faces.find((f) => f.usable)
      if (first) {
        setChosen(first.index)
        if (first.match && !name) setName(first.match)
      }
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(null)
    }
  }

  const add = async () => {
    if (!camera || chosen === null) return
    setBusy('add')
    try {
      const saved = await api.captureInsider(camera.id, chosen, name.trim())
      notify(saved.warning ?? `Photo added for ${saved.name}`, saved.warning ? 'info' : 'success')
      setFaces(null)
      setChosen(null)
      onAdded()
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(null)
    }
  }

  return (
    <Card title="Add from a camera">
      <p className="mb-3 text-sm text-zinc-400">
        Photos taken by the camera that will see them work best. Stand 1 to 2 metres away, facing it, then take a snapshot.
      </p>
      {cameras.length === 0 ? (
        <p className="text-sm text-zinc-500">No camera is connected right now.</p>
      ) : (
        <div className="space-y-3">
          <div className="flex gap-2">
            <select className="input" value={camera?.id ?? ''} onChange={(e) => setCameraId(e.target.value)} aria-label="Camera">
              {cameras.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
            <Button icon={<Camera className="h-4 w-4" />} loading={busy === 'snap'} onClick={snap}>
              Snapshot
            </Button>
          </div>
          {faces?.length === 0 && <p className="text-sm text-amber-400">No face found. Face the camera and try again.</p>}
          {faces && faces.length > 0 && (
            <>
              <div className="flex flex-wrap gap-2">
                {faces.map((f) => (
                  <button
                    key={f.index}
                    type="button"
                    disabled={!f.usable}
                    onClick={() => setChosen(f.index)}
                    className={cx('w-24 overflow-hidden rounded-md border text-left disabled:opacity-60', chosen === f.index ? 'border-blue-500 ring-1 ring-blue-500' : 'border-zinc-800')}
                    title={f.reason ?? undefined}
                  >
                    <img src={f.thumbnail} alt="" className="aspect-square w-full object-cover" />
                    <span className={cx('block truncate px-1.5 py-1 text-[10px]', f.usable ? 'text-zinc-300' : 'text-amber-300')}>
                      {f.usable ? (f.match ? `Looks like ${f.match}` : 'New face') : f.reason}
                    </span>
                  </button>
                ))}
              </div>
              <Field label="Name">
                <input className="input" list="insider-names" value={name} maxLength={64} onChange={(e) => setName(e.target.value)} placeholder="Who is this?" />
                <datalist id="insider-names">
                  {names.map((n) => (
                    <option key={n} value={n} />
                  ))}
                </datalist>
              </Field>
              <Button variant="primary" icon={<UserPlus className="h-4 w-4" />} loading={busy === 'add'} disabled={chosen === null || !name.trim()} onClick={add}>
                Add this face
              </Button>
            </>
          )}
        </div>
      )}
    </Card>
  )
}

function CheckPhoto() {
  const notify = useToast()
  const fileRef = useRef<HTMLInputElement>(null)
  const [result, setResult] = useState<{ faces: CheckedFace[]; threshold: number } | null>(null)
  const [busy, setBusy] = useState(false)

  const check = async (file: File | undefined) => {
    if (!file) return
    setBusy(true)
    try {
      setResult(await api.checkPhoto(file))
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <Card title="Check a photo">
      <p className="mb-3 text-sm text-zinc-400">See who Guardian thinks is in a picture, and how sure it is.</p>
      <input ref={fileRef} type="file" accept="image/*" className="hidden" onChange={(e) => check(e.target.files?.[0])} />
      <Button icon={<ScanFace className="h-4 w-4" />} loading={busy} onClick={() => fileRef.current?.click()}>
        Choose photo
      </Button>
      {result?.faces.length === 0 && <p className="mt-3 text-sm text-zinc-400">No face found in that photo.</p>}
      {result && result.faces.length > 0 && (
        <ul className="mt-3 space-y-2">
          {result.faces.map((f, i) => (
            <li key={i} className="flex items-center gap-3">
              <img src={f.thumbnail} alt="" className="h-14 w-14 rounded object-cover" />
              <div className="text-sm">
                {f.match ? <span className="text-emerald-400">Recognised as {f.match}</span> : <span className="text-zinc-300">Not recognised</span>}
                <div className="text-xs text-zinc-500">
                  Match {f.score.toFixed(2)} (needs {result.threshold.toFixed(2)})
                  {f.runner_up > 0 && `, next best ${f.runner_up.toFixed(2)}`}
                  {!f.quality_ok && f.reason && <Badge className="ml-1 bg-amber-500/10 text-amber-300 ring-amber-500/30">{f.reason}</Badge>}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function InsiderCard({ insider, onChanged }: { insider: Insider; onChanged: () => void }) {
  const notify = useToast()
  const fileRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [results, setResults] = useState<UploadResult[] | null>(null)
  const [confirm, setConfirm] = useState<{ photo?: string } | null>(null)

  const addPhotos = async (files: FileList | null) => {
    if (!files?.length) return
    setBusy(true)
    try {
      const { results } = await api.addInsiderPhotos(insider.name, Array.from(files))
      setResults(results)
      onChanged()
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const remove = async () => {
    setBusy(true)
    try {
      if (confirm?.photo) {
        await api.deleteInsiderPhoto(insider.name, confirm.photo)
        notify('Photo removed', 'success')
      } else {
        await api.deleteInsider(insider.name)
        notify(`${insider.name} removed`, 'success')
      }
      setConfirm(null)
      onChanged()
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(false)
    }
  }

  const usable = insider.photos.filter((p) => p.usable).length

  return (
    <Card
      title={insider.name}
      actions={
        <>
          <input ref={fileRef} type="file" accept="image/*" multiple className="hidden" onChange={(e) => addPhotos(e.target.files)} />
          <Button size="sm" icon={<ImagePlus className="h-4 w-4" />} loading={busy} onClick={() => fileRef.current?.click()}>
            Add photos
          </Button>
          <Button size="sm" variant="ghost" icon={<Trash2 className="h-4 w-4" />} onClick={() => setConfirm({})}>
            Remove
          </Button>
        </>
      }
    >
      <p className="mb-3 text-xs text-zinc-500">
        {usable} of {insider.photos.length} {insider.photos.length === 1 ? 'photo' : 'photos'} usable for recognition
      </p>
      <div className="flex flex-wrap gap-2">
        {insider.photos.map((p) => (
          <div key={p.file} className="group relative h-20 w-20 overflow-hidden rounded-md border border-zinc-800 bg-black">
            <img src={api.insiderPhotoUrl(insider.name, p.file)} alt="" loading="lazy" className="h-full w-full object-cover" />
            {!p.usable && (
              <span className="absolute inset-x-0 bottom-0 bg-black/80 py-0.5 text-center text-[10px] text-amber-300">No face found</span>
            )}
            <button
              type="button"
              onClick={() => setConfirm({ photo: p.file })}
              className="absolute right-1 top-1 rounded bg-black/70 p-0.5 text-zinc-200 opacity-0 transition-opacity hover:text-white group-hover:opacity-100 focus:opacity-100"
              aria-label="Remove photo"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
      {results && <UploadResults results={results} />}
      <ConfirmDialog
        open={!!confirm}
        title={confirm?.photo ? 'Remove this photo?' : `Remove ${insider.name}?`}
        message={
          confirm?.photo
            ? 'The photo is deleted and no longer used for recognition.'
            : `${insider.name} will no longer be recognised and will trigger alarms like anyone else. All their photos are deleted.`
        }
        confirmLabel="Remove"
        busy={busy}
        onConfirm={remove}
        onCancel={() => setConfirm(null)}
      />
    </Card>
  )
}

export default function InsidersPage() {
  const notify = useToast()
  const { data, error, refresh } = usePoll(api.insiders, 10000)
  const [name, setName] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [busy, setBusy] = useState(false)
  const [results, setResults] = useState<UploadResult[] | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setResults(null)
    try {
      const { results } = await api.addInsiderPhotos(name.trim(), files)
      setResults(results)
      if (results.some((r) => r.ok)) {
        notify(`Saved photos for ${name.trim()}`, 'success')
        setName('')
        setFiles([])
        if (fileRef.current) fileRef.current.value = ''
      }
      refresh()
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(false)
    }
  }

  const faces = data?.faces

  return (
    <div className="grid gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
      <div className="space-y-4">
        <Card title="Add a person">
          <p className="mb-4 text-sm text-zinc-400">
            Insiders are recognised by face and never trigger alarms. Use clear, front-facing photos; two or three from
            different angles work best.
          </p>
          <form onSubmit={submit} className="space-y-4">
            <Field label="Name">
              <input className="input" value={name} maxLength={64} required onChange={(e) => setName(e.target.value)} placeholder="e.g. Sam" />
            </Field>
            <Field label="Photos" hint="JPEG, PNG or WebP, up to 15 MB each">
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                multiple
                required
                onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
                className="block w-full text-sm text-zinc-400 file:mr-3 file:rounded-md file:border-0 file:bg-zinc-800 file:px-3 file:py-2 file:text-sm file:text-zinc-100 hover:file:bg-zinc-700"
              />
            </Field>
            <Button type="submit" variant="primary" icon={<UserPlus className="h-4 w-4" />} loading={busy} disabled={!name.trim() || files.length === 0}>
              Add person
            </Button>
          </form>
          {results && <UploadResults results={results} />}
        </Card>

        <CaptureFromCamera names={data?.items.map((i) => i.name) ?? []} onAdded={refresh} />

        <Card title="Recognition">
          <div className="space-y-2 text-sm">
            {data && !data.enabled && (
              <p className="text-amber-400">
                Insider recognition is turned off, so everyone is treated as unknown.{' '}
                <a href={href('settings', 'detection')} className="text-blue-400 hover:text-blue-300">
                  Turn it on
                </a>
              </p>
            )}
            {faces?.state === 'downloading' && <p className="text-zinc-300">Downloading face models (about 40 MB)…</p>}
            {faces?.state === 'ready' && <p className="text-zinc-300">Face models loaded · {faces.enrolled} people enrolled</p>}
            {faces?.state === 'idle' && <p className="text-zinc-400">Face models load the first time they are needed.</p>}
            {faces?.state === 'error' && <ErrorNote>{faces.error}</ErrorNote>}
            <p className="text-xs text-zinc-500">
              If a recognised person turns away from the camera, they are trusted for a short grace period
              (Settings → Detection).
            </p>
          </div>
        </Card>

        <CheckPhoto />
      </div>

      <div className="space-y-4">
        {error && <ErrorNote>{error.message}</ErrorNote>}
        {data && data.items.length === 0 ? (
          <Card>
            <Empty icon={<Users className="h-8 w-8" />} title="No insiders yet">
              Add the people who live or work here so they don't set off the alarm.
            </Empty>
          </Card>
        ) : (
          data?.items.map((i) => <InsiderCard key={i.name} insider={i} onChanged={refresh} />)
        )}
      </div>
    </div>
  )
}
