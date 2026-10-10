import { useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { api, type Briefing } from '../api'
import { cx } from '../lib/cx'
import { formatSeen } from '../lib/format'
import { errorMessage, useToast } from '../lib/toast'
import { usePoll } from '../lib/usePoll'
import { Badge, Button, Card } from './ui'

function Fact({ label, value, tone, title }: { label: string; value: number; tone: string; title: string }) {
  return (
    <span title={title}>
      <Badge className={value > 0 ? tone : undefined}>
        {label} <span className="tabular-nums">{value}</span>
      </Badge>
    </span>
  )
}

function sourceText(b: Briefing): string {
  if (b.source === 'llm') return 'written by the language model from the event log'
  return b.note ? 'written from the event log; the language model couldn’t be used' : 'written from the event log'
}

/** The daily briefing: a few sentences on the last 24 hours, with the main counts. */
export default function BriefingCard() {
  const notify = useToast()
  const [writing, setWriting] = useState(false)
  const [starting, setStarting] = useState(false)
  // Checked often while a briefing is being written, otherwise once a minute
  const { data, refresh } = usePoll(
    async () => {
      const b = await api.briefing()
      setWriting(b.generating)
      return b
    },
    writing ? 2000 : 60000,
  )

  if (!data || !data.enabled) return null
  const busy = writing || data.generating
  const counts = data.facts?.counts

  const write = async () => {
    setStarting(true)
    try {
      const b = await api.refreshBriefing()
      setWriting(b.generating)
      await refresh()
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setStarting(false)
    }
  }

  return (
    <Card
      title="Last 24 hours"
      actions={
        <Button size="sm" icon={<RefreshCw className={cx('h-3.5 w-3.5', busy && 'animate-spin')} />} disabled={busy || starting} onClick={write} title="Write a new briefing now">
          {busy ? 'Writing…' : 'Refresh'}
        </Button>
      }
    >
      {data.text ? (
        <>
          <p className={cx('text-sm leading-relaxed text-zinc-200', busy && 'opacity-60')}>{data.text}</p>
          {counts && (
            <div className="mt-3 flex flex-wrap gap-1.5">
              <Fact label="Incidents" value={counts.incidents} tone="bg-amber-500/10 text-amber-300 ring-amber-500/30" title="Incidents with unrecognised people" />
              <Fact label="Alerts" value={counts.alerts} tone="bg-orange-500/15 text-orange-300 ring-orange-500/35" title="Alerts sent to you" />
              <Fact label="Insiders" value={counts.insiders} tone="bg-emerald-500/10 text-emerald-300 ring-emerald-500/30" title="Insiders recognised" />
              <Fact
                label="Strangers"
                value={counts.unknown_people}
                tone="bg-yellow-400/10 text-yellow-200 ring-yellow-400/25"
                title="Unrecognised people detected. Someone detected twice counts twice."
              />
            </div>
          )}
          {data.generated_at && (
            <p className="mt-2 text-xs text-zinc-500" title={data.note ?? undefined}>
              {formatSeen(data.generated_at)} · {sourceText(data)}
            </p>
          )}
        </>
      ) : (
        <p className="text-sm text-zinc-500">{busy ? 'Writing the first briefing…' : 'No briefing yet.'}</p>
      )}
    </Card>
  )
}
