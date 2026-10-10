import { useState } from 'react'
import { Film, Lock, Play } from 'lucide-react'
import { api, type Recording } from '../api'
import RecordingPlayer from '../components/RecordingPlayer'
import { Badge, Card, Empty, ErrorNote } from '../components/ui'
import { formatBytes, formatDateTime, formatDuration, LEVELS, reasonLabel } from '../lib/format'
import { href } from '../lib/route'
import { usePoll } from '../lib/usePoll'

function Thumb({ rec }: { rec: Recording }) {
  const [broken, setBroken] = useState(false)
  if (!rec.thumbnail || broken) {
    return (
      <div className="flex h-full w-full items-center justify-center text-zinc-600">
        <Film className="h-8 w-8" />
      </div>
    )
  }
  return <img src={api.thumbnailUrl(rec.file)} alt="" loading="lazy" className="h-full w-full object-cover" onError={() => setBroken(true)} />
}

export default function RecordingsPage() {
  const [camera, setCamera] = useState('')
  const { data, error, refresh } = usePoll(() => api.recordings(camera), 5000, [camera])
  const settings = usePoll(api.settings, 60000)
  const [open, setOpen] = useState<Recording | null>(null)

  const s = settings.data
  const items = data?.items ?? []

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-zinc-400">
        <span>
          {data ? `${items.length} ${items.length === 1 ? 'clip' : 'clips'} · ${formatBytes(data.usage_bytes)} on disk` : 'Loading…'}
          {s && (
            <>
              {' · '}
              {s.recording.retention_days > 0 ? `deleted after ${s.recording.retention_days} days` : 'kept forever'}
            </>
          )}
        </span>
        <div className="flex items-center gap-3">
          {s && s.cameras.length > 1 && (
            <select className="input h-8 w-auto py-1 text-xs" value={camera} onChange={(e) => setCamera(e.target.value)} aria-label="Camera">
              <option value="">All cameras</option>
              {s.cameras.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          )}
          <a href={href('evidence')} className="text-xs font-medium text-blue-400 hover:text-blue-300">
            Evidence vault
          </a>
          <a href={href('settings', 'recording')} className="text-xs font-medium text-blue-400 hover:text-blue-300">
            Recording settings
          </a>
        </div>
      </div>

      {error && <ErrorNote>{error.message}</ErrorNote>}

      {data?.active.map((a) => (
        <div key={a.file} className="flex items-center gap-2 rounded-md border border-red-900/60 bg-red-950/30 px-3 py-2 text-sm text-red-200">
          <span className="h-2 w-2 animate-pulse rounded-full bg-red-500" />
          Recording on {a.camera}{a.stopping ? ', finishing the clip' : ''}. It appears here when it is saved.
        </div>
      ))}

      {data && items.length === 0 ? (
        <Card>
          <Empty icon={<Film className="h-8 w-8" />} title="No recordings yet">
            Clips are saved automatically when an incident reaches level {s?.escalation.record_at_level ?? 2}, or when the
            panic alarm is raised. Each clip includes the {s?.recording.preroll_seconds ?? 5} seconds before it started.
          </Empty>
        </Card>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
          {items.map((rec) => (
            <button
              key={rec.file}
              type="button"
              onClick={() => setOpen(rec)}
              className="group overflow-hidden rounded-2xl border border-zinc-800 bg-zinc-900/60 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:border-zinc-600 hover:shadow-lg"
            >
              <div className="force-dark relative aspect-video bg-black">
                <Thumb rec={rec} />
                {rec.protected && (
                  <span className="absolute right-1.5 top-1.5 flex items-center gap-1 rounded bg-black/75 px-1.5 py-0.5 text-[10px] font-medium text-amber-300" title="Kept forever">
                    <Lock className="h-3 w-3" /> Kept
                  </span>
                )}
                {rec.sealed && (
                  <span className="absolute left-1.5 top-1.5 flex items-center gap-1 rounded bg-black/75 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300" title="Sealed in the evidence vault">
                    <Lock className="h-3 w-3" /> Sealed
                  </span>
                )}
                <span className="absolute bottom-1.5 right-1.5 rounded bg-black/80 px-1.5 py-0.5 font-mono text-[11px] tabular-nums text-zinc-100">
                  {formatDuration(rec.duration)}
                </span>
                <span className="absolute inset-0 flex items-center justify-center opacity-0 transition-opacity group-hover:opacity-100">
                  <span className="rounded-full bg-black/70 p-3">
                    <Play className="h-5 w-5 text-white" />
                  </span>
                </span>
              </div>
              <div className="flex items-start justify-between gap-2 p-3">
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-zinc-100">{formatDateTime(rec.started)}</div>
                  <div className="truncate text-xs text-zinc-500">
                    {rec.camera ? `${rec.camera} · ` : ''}
                    {formatBytes(rec.size)}
                  </div>
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1">
                  <Badge>{reasonLabel(rec.reason)}</Badge>
                  {rec.max_level ? <Badge className={LEVELS[rec.max_level].soft}>Level {rec.max_level}</Badge> : null}
                </div>
              </div>
            </button>
          ))}
        </div>
      )}

      <RecordingPlayer key={open?.file ?? ''} file={open?.file ?? null} recording={open ?? undefined} onClose={() => setOpen(null)} onDeleted={refresh} />
    </div>
  )
}
