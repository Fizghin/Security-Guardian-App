import { useState } from 'react'
import { Check, EyeOff, Trash2, X } from 'lucide-react'
import { api, type LearnedSpot, type Learning, type SecurityEvent, type SpotSuggestion } from '../../api'
import { cx } from '../../lib/cx'
import { href } from '../../lib/route'
import { errorMessage, useToast } from '../../lib/toast'
import { Button, Card, Empty } from '../ui'
import { seen } from './format'

function Outline({ box, label, dashed }: { box: number[]; label: string; dashed?: boolean }) {
  const [x1, y1, x2, y2] = box
  return (
    <div
      className={cx('absolute rounded-sm border-2', dashed ? 'border-dashed border-amber-400' : 'border-sky-400')}
      style={{ left: `${x1 * 100}%`, top: `${y1 * 100}%`, width: `${(x2 - x1) * 100}%`, height: `${(y2 - y1) * 100}%` }}
    >
      <span className={cx('absolute left-0 top-0 px-1 text-[11px] font-semibold leading-4 text-black', dashed ? 'bg-amber-400' : 'bg-sky-400')}>
        {label}
      </span>
    </div>
  )
}

/** The camera's picture without boxes, with its spots (numbered) and suggestions (dashed) drawn on it. */
function Picture({ cameraId, spots, suggestions }: { cameraId: string; spots: LearnedSpot[]; suggestions: SpotSuggestion[] }) {
  const [url] = useState(() => api.rawSnapshotUrl(cameraId))
  const [failed, setFailed] = useState(false)
  if (failed) {
    return (
      <div className="flex aspect-video items-center justify-center rounded-md border border-zinc-800 bg-black px-4 text-center text-sm text-zinc-500">
        No picture from this camera right now
      </div>
    )
  }
  return (
    <div className="relative overflow-hidden rounded-md border border-zinc-800 bg-black">
      <img src={url} alt="" className="block w-full" onError={() => setFailed(true)} />
      {spots.map((s, i) => (
        <Outline key={s.id} box={s.box} label={String(i + 1)} />
      ))}
      {suggestions.map((s) => (
        <Outline key={s.id} box={s.box} label="?" dashed />
      ))}
    </div>
  )
}

function CameraSpots({
  cameraId,
  name,
  spots,
  suggestions,
  stillMinutes,
  onChanged,
  onOpenPicture,
}: {
  cameraId: string
  name: string
  spots: LearnedSpot[]
  suggestions: SpotSuggestion[]
  stillMinutes: number
  onChanged: () => void
  onOpenPicture: (event: SecurityEvent) => void
}) {
  const notify = useToast()
  const [busy, setBusy] = useState<string | null>(null)

  const act = async (id: string, fn: () => Promise<string>) => {
    setBusy(id)
    try {
      notify(await fn(), 'success')
      onChanged()
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-medium text-zinc-200">{name}</h3>
      <Picture cameraId={cameraId} spots={spots} suggestions={suggestions} />
      <ul className="divide-y divide-zinc-800/80 text-sm">
        {spots.map((s, i) => (
          <li key={s.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-sm bg-sky-400 text-[11px] font-semibold text-black">{i + 1}</span>
            <div className="min-w-0 flex-1">
              <div className="text-zinc-200">
                Ignored spot · taught {s.taught === 1 ? 'once' : `${s.taught} times`}
              </div>
              <div className="text-xs text-zinc-500">
                Since {seen(s.created)} · {s.last_matched ? `last ignored something ${seen(s.last_matched)}` : 'has not ignored anything yet'}
              </div>
            </div>
            <Button
              size="sm"
              variant="ghost"
              icon={<Trash2 className="h-4 w-4" />}
              loading={busy === s.id}
              onClick={() =>
                act(s.id, async () => {
                  await api.deleteSpot(s.id)
                  return `Removed. Everything in that spot on ${name} is watched again.`
                })
              }
            >
              Remove
            </Button>
          </li>
        ))}
        {suggestions.map((s) => (
          <li key={s.id} className="flex flex-wrap items-center gap-x-3 gap-y-2 py-2">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-sm bg-amber-400 text-[11px] font-semibold text-black">?</span>
            <div className="min-w-0 flex-1">
              <div className="text-zinc-200">Suggested: something that has not moved</div>
              <div className="text-xs text-zinc-500">
                Still for {stillMinutes} minutes with no face, {seen(s.created)}. Confirm only if it is an object, not a person.
                {s.event && (
                  <>
                    {' '}
                    <button type="button" className="text-blue-400 hover:text-blue-300" onClick={() => onOpenPicture(s.event!)}>
                      Show picture
                    </button>
                  </>
                )}
              </div>
            </div>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="primary"
                icon={<Check className="h-4 w-4" />}
                loading={busy === `${s.id}:yes`}
                disabled={!!busy}
                onClick={() => act(`${s.id}:yes`, async () => (await api.answerSuggestion(s.id, true)).message)}
              >
                Confirm
              </Button>
              <Button
                size="sm"
                icon={<X className="h-4 w-4" />}
                loading={busy === `${s.id}:no`}
                disabled={!!busy}
                onClick={() => act(`${s.id}:no`, async () => (await api.answerSuggestion(s.id, false)).message)}
              >
                Dismiss
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** Spots taught with "False alarm", and places Guardian suggests because something there never moves. */
export default function IgnoredSpots({
  data,
  onChanged,
  onOpenPicture,
}: {
  data: Learning
  onChanged: () => void
  onOpenPicture: (event: SecurityEvent) => void
}) {
  const cameras = new Map<string, string>()
  for (const item of [...data.suggestions, ...data.spots]) cameras.set(item.camera_id, item.camera ?? 'Removed camera')

  return (
    <Card title="Ignored spots">
      {!data.settings.learn_from_feedback && (
        <p className="mb-4 text-sm text-amber-400">
          Learning from feedback is off, so “False alarm” teaches nothing new.{' '}
          <a href={href('settings', 'learning')} className="text-blue-400 hover:text-blue-300">
            Turn it on
          </a>
        </p>
      )}
      {cameras.size === 0 ? (
        <Empty icon={<EyeOff className="h-8 w-8" />} title="Nothing is ignored">
          Mark a detection as “False alarm” and Guardian ignores that spot while nothing there moves. A person who moves or
          shows their face is always detected.
        </Empty>
      ) : (
        <div className="space-y-6">
          <p className="text-sm text-zinc-400">
            Something on a spot is ignored only while it stays still and shows no face. It is drawn as a thin grey box
            labelled “ignored” on the live picture.
          </p>
          {[...cameras].map(([id, name]) => (
            <CameraSpots
              key={id}
              cameraId={id}
              name={name}
              spots={data.spots.filter((s) => s.camera_id === id)}
              suggestions={data.suggestions.filter((s) => s.camera_id === id)}
              stillMinutes={data.rules.still_minutes}
              onChanged={onChanged}
              onOpenPicture={onOpenPicture}
            />
          ))}
        </div>
      )}
    </Card>
  )
}
