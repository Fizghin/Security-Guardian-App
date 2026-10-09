import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Trash2, UserPlus, UserSearch } from 'lucide-react'
import { api, type Visitor, type VisitorDetail } from '../api'
import { Badge, Button, Card, ConfirmDialog, Empty, ErrorNote, Field, Modal, Toggle } from '../components/ui'
import { cx } from '../lib/cx'
import { formatDateTime, formatSeen } from '../lib/format'
import { href } from '../lib/route'
import { errorMessage, useToast } from '../lib/toast'
import { usePoll } from '../lib/usePoll'

const visitsText = (n: number) => `${n} ${n === 1 ? 'visit' : 'visits'}`
const REPEAT_STYLE = 'bg-amber-500/10 text-amber-300 ring-amber-500/30'

function Face({ visitor, n = 0 }: { visitor: Visitor; n?: number }) {
  if (!visitor.faces[n]) {
    return (
      <div className="flex h-full w-full items-center justify-center text-zinc-600">
        <UserSearch className="h-8 w-8" />
      </div>
    )
  }
  return <img src={api.visitorPhotoUrl(visitor, n)} alt="" loading="lazy" className="h-full w-full object-cover" />
}

function VisitorCard({ visitor, onOpen }: { visitor: Visitor; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="overflow-hidden rounded-lg border border-zinc-800 bg-zinc-900/60 text-left transition-colors hover:border-zinc-600"
    >
      <div className="aspect-square bg-black">
        <Face visitor={visitor} />
      </div>
      <div className="space-y-1 p-3">
        <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
          <span className="min-w-0 truncate text-sm font-medium text-zinc-100">{visitor.name}</span>
          <Badge className={visitor.visits > 1 ? REPEAT_STYLE : undefined}>{visitsText(visitor.visits)}</Badge>
        </div>
        <p className="text-xs text-zinc-400">Last seen {formatSeen(visitor.last_seen)}</p>
        <p className="text-xs text-zinc-500">First seen {formatSeen(visitor.first_seen)}</p>
        {visitor.cameras.length > 0 && <p className="truncate text-xs text-zinc-500">{visitor.cameras.join(', ')}</p>}
      </div>
    </button>
  )
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-zinc-500">{label}</dt>
      <dd className="mt-0.5 text-sm text-zinc-100">{children}</dd>
    </div>
  )
}

function VisitorDialog({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged: () => void }) {
  const notify = useToast()
  const [visitor, setVisitor] = useState<VisitorDetail | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [label, setLabel] = useState('')
  const [note, setNote] = useState('')
  const [insiderName, setInsiderName] = useState('')
  const [insiders, setInsiders] = useState<string[]>([])
  const [picture, setPicture] = useState<number | null>(null)
  const [busy, setBusy] = useState<'save' | 'insider' | 'forget' | null>(null)
  const [confirmForget, setConfirmForget] = useState(false)

  useEffect(() => {
    api.visitor(id).then(
      (v) => {
        setVisitor(v)
        setLabel(v.label ?? '')
        setNote(v.note)
      },
      (err) => setLoadError(errorMessage(err)),
    )
    api.insiders().then((r) => setInsiders(r.items.map((i) => i.name)), () => {})
  }, [id])

  const run = async (kind: 'save' | 'insider' | 'forget', fn: () => Promise<void>) => {
    setBusy(kind)
    try {
      await fn()
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(null)
    }
  }

  const save = (e: FormEvent) => {
    e.preventDefault()
    run('save', async () => {
      const v = await api.updateVisitor(id, { label: label.trim() || null, note: note.trim() || null })
      setVisitor(v)
      setLabel(v.label ?? '')
      setNote(v.note)
      notify('Saved', 'success')
      onChanged()
    })
  }

  const makeInsider = (e: FormEvent) => {
    e.preventDefault()
    run('insider', async () => {
      const r = await api.makeInsider(id, insiderName.trim())
      notify(`${r.name} is now an insider (${r.added} ${r.added === 1 ? 'photo' : 'photos'} added)`, 'success')
      onChanged()
      onClose()
    })
  }

  const forget = () =>
    run('forget', async () => {
      await api.forgetVisitor(id)
      notify(`${visitor?.name ?? 'Visitor'} forgotten`, 'success')
      onChanged()
      onClose()
    })

  const dirty = !!visitor && (label.trim() !== (visitor.label ?? '') || note.trim() !== visitor.note)

  return (
    <>
      <Modal
        open
        onClose={onClose}
        onEscape={() => (confirmForget ? setConfirmForget(false) : onClose())}
        wide
        title={visitor?.name ?? 'Visitor'}
        footer={
          <>
            <Button variant="ghost" className="mr-auto" icon={<Trash2 className="h-4 w-4" />} disabled={!visitor} onClick={() => setConfirmForget(true)}>
              Forget
            </Button>
            <Button onClick={onClose}>Close</Button>
          </>
        }
      >
        {loadError && <ErrorNote>{loadError}</ErrorNote>}
        {!visitor && !loadError && <p className="text-sm text-zinc-500">Loading…</p>}
        {visitor && (
          <div className="space-y-5">
            {visitor.faces.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {visitor.faces.map((file, n) => (
                  <div key={file} className="h-24 w-24 overflow-hidden rounded-md border border-zinc-800 bg-black">
                    <Face visitor={visitor} n={n} />
                  </div>
                ))}
              </div>
            )}

            <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Fact label="Visits">{visitor.visits}</Fact>
              <Fact label="First seen">{formatSeen(visitor.first_seen)}</Fact>
              <Fact label="Last seen">{formatSeen(visitor.last_seen)}</Fact>
              <Fact label="Cameras">{visitor.cameras.join(', ') || '–'}</Fact>
            </dl>

            <div className="grid gap-3 md:grid-cols-2">
              <form onSubmit={save} className="space-y-3 rounded-md border border-zinc-800 p-3">
                <Field label="Name" hint="Shown on the live picture instead of Unknown. They still count as a stranger.">
                  <input className="input" value={label} maxLength={40} placeholder={`Visitor ${visitor.id}`} onChange={(e) => setLabel(e.target.value)} />
                </Field>
                <Field label="Note">
                  <textarea className="input" rows={2} value={note} maxLength={300} placeholder="e.g. Delivers parcels on Tuesdays" onChange={(e) => setNote(e.target.value)} />
                </Field>
                <Button type="submit" disabled={!dirty} loading={busy === 'save'}>
                  Save
                </Button>
              </form>

              <form onSubmit={makeInsider} className="space-y-3 rounded-md border border-zinc-800 p-3">
                <div>
                  <p className="text-sm font-medium text-zinc-100">This is someone I know</p>
                  <p className="hint">Their face photos are added to the Insiders page, so they no longer set off alarms. They are then removed from Visitors.</p>
                </div>
                <Field label="Insider name">
                  <input className="input" list="visitor-insider-names" value={insiderName} maxLength={64} placeholder="e.g. Sam" onChange={(e) => setInsiderName(e.target.value)} />
                  <datalist id="visitor-insider-names">
                    {insiders.map((n) => (
                      <option key={n} value={n} />
                    ))}
                  </datalist>
                </Field>
                <Button type="submit" variant="primary" icon={<UserPlus className="h-4 w-4" />} disabled={!insiderName.trim() || visitor.faces.length === 0} loading={busy === 'insider'}>
                  Add as insider
                </Button>
              </form>
            </div>

            <div>
              <h3 className="mb-2 text-xs font-medium text-zinc-400">Sightings</h3>
              {visitor.sightings.length === 0 ? (
                <p className="text-sm text-zinc-500">No sightings kept.</p>
              ) : (
                <ol className="divide-y divide-zinc-800 rounded-md border border-zinc-800">
                  {visitor.sightings.map((s) => (
                    <li key={s.id} className="px-3 py-2">
                      <div className="flex items-center gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                            <time className="font-mono text-xs tabular-nums text-zinc-300" dateTime={s.started} title={formatDateTime(s.started)}>
                              {formatSeen(s.started)}
                            </time>
                            {s.new_visit && <Badge className={REPEAT_STYLE}>New visit</Badge>}
                            {s.camera && <span className="text-xs text-zinc-500">{s.camera}</span>}
                          </div>
                          {s.event && <p className="mt-0.5 line-clamp-2 text-xs text-zinc-400">{s.event.description}</p>}
                        </div>
                        {s.event?.snapshot && (
                          <button
                            type="button"
                            onClick={() => setPicture(picture === s.id ? null : s.id)}
                            className={cx('h-12 w-20 shrink-0 overflow-hidden rounded border bg-black hover:border-zinc-500', picture === s.id ? 'border-blue-500' : 'border-zinc-800')}
                            title={picture === s.id ? 'Hide picture' : 'Show picture'}
                          >
                            <img src={api.eventSnapshotUrl(s.event)} alt="" loading="lazy" className="h-full w-full object-cover" />
                          </button>
                        )}
                      </div>
                      {picture === s.id && s.event && <img src={api.eventSnapshotUrl(s.event)} alt={s.event.description} className="mt-2 w-full rounded" />}
                    </li>
                  ))}
                </ol>
              )}
            </div>
          </div>
        )}
      </Modal>
      <ConfirmDialog
        open={confirmForget}
        title={`Forget ${visitor?.name ?? 'this visitor'}?`}
        message="Their face photos and sightings are deleted. If they come back, they are remembered as a new visitor. Event pictures in the log stay."
        confirmLabel="Forget"
        busy={busy === 'forget'}
        onConfirm={forget}
        onCancel={() => setConfirmForget(false)}
      />
    </>
  )
}

export default function VisitorsPage() {
  const notify = useToast()
  const [repeatOnly, setRepeatOnly] = useState(false)
  const { data, error, refresh } = usePoll(() => api.visitors(repeatOnly), 10000, [repeatOnly])
  const [open, setOpen] = useState<number | null>(null)
  const [confirmAll, setConfirmAll] = useState(false)
  const [busy, setBusy] = useState(false)
  const items = data?.items ?? []

  const forgetAll = async () => {
    setBusy(true)
    try {
      const { deleted } = await api.forgetAllVisitors()
      notify(`Forgot ${deleted} ${deleted === 1 ? 'visitor' : 'visitors'}`, 'success')
      setConfirmAll(false)
      refresh()
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(false)
    }
  }

  const kept = data && (data.retention_days > 0 ? `forgotten ${data.retention_days} days after they were last seen` : 'kept until you forget them')

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <span className="text-sm text-zinc-400">
          {data ? `${items.length} ${repeatOnly ? 'returning ' : ''}${items.length === 1 ? 'visitor' : 'visitors'} · ${kept}` : 'Loading…'}
        </span>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <Toggle checked={repeatOnly} onChange={setRepeatOnly} label="Only people seen more than once" />
          <a href={href('settings', 'detection')} className="text-xs font-medium text-blue-400 hover:text-blue-300">
            Visitor settings
          </a>
          {items.length > 0 && !repeatOnly && (
            <Button size="sm" variant="ghost" icon={<Trash2 className="h-4 w-4" />} onClick={() => setConfirmAll(true)}>
              Forget all
            </Button>
          )}
        </div>
      </div>

      {data && !data.enabled && (
        <div className="rounded-md border border-amber-900/60 bg-amber-950/30 px-3 py-2 text-sm text-amber-200">
          Guardian is not remembering new faces right now.{' '}
          <a href={href('settings', 'detection')} className="font-medium text-blue-400 hover:text-blue-300">
            Turn on Remember strangers’ faces
          </a>
        </div>
      )}
      {data?.enabled && data.faces.state === 'error' && <ErrorNote>{data.faces.error}</ErrorNote>}
      {error && <ErrorNote>{error.message}</ErrorNote>}

      {data && items.length === 0 ? (
        <Card>
          {repeatOnly ? (
            <Empty icon={<UserSearch className="h-8 w-8" />} title="Nobody has come back yet">
              People seen on more than one visit show here. Coming back after {data.visit_gap_minutes} minutes or more counts as a new visit.
            </Empty>
          ) : (
            <Empty icon={<UserSearch className="h-8 w-8" />} title="No visitors yet">
              While armed, Guardian remembers the faces of people it doesn’t recognise, and tells you in the event log and in alerts
              when they come back. Each person shows here with their best face photos and when they were seen. Faces stay on this
              computer.
            </Empty>
          )}
        </Card>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-6">
          {items.map((v) => (
            <VisitorCard key={v.id} visitor={v} onOpen={() => setOpen(v.id)} />
          ))}
        </div>
      )}

      {open !== null && <VisitorDialog key={open} id={open} onClose={() => setOpen(null)} onChanged={refresh} />}
      <ConfirmDialog
        open={confirmAll}
        title="Forget all visitors?"
        message="Every remembered face and sighting is deleted. Event pictures in the log stay."
        confirmLabel="Forget all"
        busy={busy}
        onConfirm={forgetAll}
        onCancel={() => setConfirmAll(false)}
      />
    </div>
  )
}
