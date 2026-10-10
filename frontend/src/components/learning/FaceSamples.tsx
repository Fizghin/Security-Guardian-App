import { ScanFace } from 'lucide-react'
import { api, type Learning } from '../../api'
import { href } from '../../lib/route'
import { Card, Empty } from '../ui'
import { seen } from './format'

/** The photos Guardian added to each insider by itself. */
export default function FaceSamples({ data }: { data: Learning }) {
  const { faces, settings, rules } = data
  return (
    <Card
      title="Faces"
      actions={
        <a href={href('insiders')} className="text-xs font-medium text-blue-400 hover:text-blue-300">
          Insiders
        </a>
      }
    >
      {faces.length === 0 ? (
        <Empty icon={<ScanFace className="h-8 w-8" />} title="No insiders yet">
          Add the people who live here on the Insiders page. When Guardian recognises one of them clearly from a new angle
          or in new light, it keeps that look too.
        </Empty>
      ) : (
        <div className="space-y-4">
          <ul className="space-y-3">
            {faces.map((f) => (
              <li key={f.name}>
                <div className="flex items-baseline justify-between gap-3 text-sm">
                  <span className="truncate font-medium text-zinc-200">{f.name}</span>
                  <span className="shrink-0 text-xs text-zinc-500">
                    {f.learned === 0
                      ? 'Nothing learned yet'
                      : `${f.learned} learned · last ${seen(f.last_learned!)}`}
                  </span>
                </div>
                {f.files.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {f.files.map((file) => (
                      <img
                        key={file}
                        src={api.insiderPhotoUrl(f.name, file)}
                        alt={`Learned photo of ${f.name}`}
                        loading="lazy"
                        className="h-12 w-12 rounded border border-zinc-800 object-cover"
                      />
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
          <p className="text-xs text-zinc-500">
            {settings.improve_faces ? (
              <>
                Only from clear, confident recognitions, at most one photo every {rules.learn_every_minutes} minutes and{' '}
                {rules.max_learned} per person. The oldest learned photo makes way for a new one; yours are never replaced.
              </>
            ) : (
              <>
                Turned off in{' '}
                <a href={href('settings', 'learning')} className="text-blue-400 hover:text-blue-300">
                  Settings → Learning
                </a>
                . Photos learned earlier are still used.
              </>
            )}
          </p>
        </div>
      )}
    </Card>
  )
}
