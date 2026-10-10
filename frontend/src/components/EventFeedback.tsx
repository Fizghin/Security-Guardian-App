import { useState } from 'react'
import { Check, UserX, X } from 'lucide-react'
import { api, type SecurityEvent, type Verdict } from '../api'
import { cx } from '../lib/cx'
import { errorMessage, useToast } from '../lib/toast'

const PEOPLE_EVENTS = ['DETECTION', 'ESCALATION', 'ALERT']

interface Choice {
  verdict: Verdict
  label: string
  hint: string
  icon: typeof Check
  chosen: string
}

/** Detections, escalations and alerts can be marked Correct or False alarm; a recognised insider "Not <name>". */
function choices(event: SecurityEvent): Choice[] {
  const details = event.details
  if (event.event_type === 'INSIDER' && details?.insider) {
    const name = details.insider
    return [
      {
        verdict: 'wrong_person',
        label: `Not ${name}`,
        hint: `This was not ${name}. Removes the photos of ${name} Guardian learned from this sighting`,
        icon: UserX,
        chosen: 'bg-amber-500/15 text-amber-300 ring-amber-500/40',
      },
    ]
  }
  if (!PEOPLE_EVENTS.includes(event.event_type) || !details?.people.some((p) => !p.simulated)) return []
  return [
    {
      verdict: 'real',
      label: 'Correct',
      hint: 'Someone really was there',
      icon: Check,
      chosen: 'bg-emerald-500/15 text-emerald-300 ring-emerald-500/40',
    },
    {
      verdict: 'false_alarm',
      label: 'False alarm',
      hint: 'Nobody was there. Guardian learns to ignore that spot while nothing moves in it',
      icon: X,
      chosen: 'bg-amber-500/15 text-amber-300 ring-amber-500/40',
    },
  ]
}

/** Small controls for telling Guardian whether an event was right. The chosen one stays highlighted;
 * choosing it again clears it, which undoes what it taught. */
export default function EventFeedback({ event, className }: { event: SecurityEvent; className?: string }) {
  const notify = useToast()
  const [busy, setBusy] = useState(false)
  // The answer just given, shown until the polled event catches up with it
  const [given, setGiven] = useState<{ before: Verdict | null; now: Verdict | null } | null>(null)
  const options = choices(event)
  if (options.length === 0) return null
  const current = given && given.before === event.feedback ? given.now : event.feedback

  const choose = async (verdict: Verdict) => {
    setBusy(true)
    try {
      const result = await api.eventFeedback(event.id, current === verdict ? null : verdict)
      setGiven({ before: event.feedback, now: result.feedback })
      notify(result.message, 'success')
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div role="group" aria-label="Was this right?" className={cx('flex flex-wrap items-center gap-1', className)}>
      {options.map(({ verdict, label, hint, icon: Icon, chosen }) => {
        const on = current === verdict
        return (
          <button
            key={verdict}
            type="button"
            aria-pressed={on}
            disabled={busy}
            onClick={() => choose(verdict)}
            title={on ? `${hint}. Choose again to clear it.` : `${hint}.`}
            className={cx(
              'inline-flex h-8 items-center gap-1 rounded px-2 text-xs ring-1 ring-inset transition-colors disabled:opacity-60',
              on ? chosen : 'text-zinc-500 ring-transparent hover:bg-zinc-800 hover:text-zinc-200',
            )}
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
          </button>
        )
      })}
    </div>
  )
}
