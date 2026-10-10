import { useEffect, useState } from 'react'
import { ArrowLeft, Download, Loader2, Printer, RefreshCw } from 'lucide-react'
import { Badge, Button, Card, ErrorNote } from '../components/ui'
import { evidenceApi, TONE_CLASS, verifyMessage, type EvidenceItem, type IncidentReport, type IncidentSummary } from '../evidenceApi'
import { cx } from '../lib/cx'
import { eventLabel, formatBytes, formatDateTime, formatDay, formatTime, LEVELS, SEVERITY_STYLE } from '../lib/format'
import { href } from '../lib/route'
import { errorMessage, useToast } from '../lib/toast'

function lengthText(seconds: number) {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return m ? `${m} min ${s} s` : `${s} s`
}

function summaryNote(summary: IncidentSummary) {
  if (summary.pending) return 'Writing a summary with the local language model. Until it is ready, this one is written from a template.'
  if (summary.source === 'llm')
    return 'Written by the local language model from the timeline below, and checked against it: it mentions no numbers, times or names that are not there.'
  return `Written from a template${summary.reason ? ` (${summary.reason.replace(/\.$/, '')})` : ''}.`
}

function Verdict({ item }: { item: EvidenceItem }) {
  const { text, tone } = verifyMessage(item)
  return <span className={TONE_CLASS[tone]}>{text}</span>
}

function Hash({ item }: { item: EvidenceItem }) {
  if (!('sha256' in item) || !item.sha256) return <span className="text-zinc-500">–</span>
  return (
    <span className="break-all font-mono text-[11px] leading-snug text-zinc-300">
      {item.sha256}
      {item.status === 'modified' && item.changed === 'file' && item.current_sha256 && (
        <span className="tone-bad block text-red-400">now {item.current_sha256}</span>
      )}
    </span>
  )
}

function sealedText(item: EvidenceItem) {
  if (!('sealed_at' in item) || !item.sealed_at) return '–'
  return `${formatDateTime(item.sealed_at)}${item.sealed_late ? ' (late)' : ''}`
}

function EvidenceTable({ items }: { items: EvidenceItem[] }) {
  if (!items.length) return <p className="text-sm text-zinc-400">No clips or pictures were kept for this incident.</p>
  return (
    <>
      <table className="hidden w-full table-fixed text-left text-sm md:table print:table">
        <thead className="text-xs text-zinc-500">
          <tr className="border-b border-zinc-800">
            <th className="w-[24%] py-2 pr-3 font-medium">File</th>
            <th className="w-[8%] py-2 pr-3 font-medium">Size</th>
            <th className="w-[30%] py-2 pr-3 font-medium">SHA-256 (sealed)</th>
            <th className="w-[14%] py-2 pr-3 font-medium">Sealed</th>
            <th className="py-2 font-medium">Verification</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-zinc-800 align-top">
          {items.map((item) => (
            <tr key={`${item.kind}:${item.name}`}>
              <td className="break-all py-2 pr-3 text-zinc-200">
                {item.name}
                <span className="block text-xs text-zinc-500">{item.kind === 'clip' ? 'Clip' : 'Picture'}</span>
              </td>
              <td className="py-2 pr-3 tabular-nums text-zinc-400">{item.size ? formatBytes(item.size) : '–'}</td>
              <td className="py-2 pr-3">
                <Hash item={item} />
              </td>
              <td className="py-2 pr-3 text-xs text-zinc-400">{sealedText(item)}</td>
              <td className="py-2 text-xs">
                <Verdict item={item} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <ul className="divide-y divide-zinc-800 md:hidden print:hidden">
        {items.map((item) => (
          <li key={`${item.kind}:${item.name}`} className="space-y-1 py-2.5 text-sm">
            <div className="flex items-baseline justify-between gap-2">
              <span className="min-w-0 break-all text-zinc-200">{item.name}</span>
              <span className="shrink-0 text-xs tabular-nums text-zinc-500">{item.size ? formatBytes(item.size) : ''}</span>
            </div>
            <Hash item={item} />
            <div className="text-xs text-zinc-500">Sealed {sealedText(item)}</div>
            <div className="text-xs">
              <Verdict item={item} />
            </div>
          </li>
        ))}
      </ul>
    </>
  )
}

export default function IncidentPage({ id }: { id: string }) {
  const notify = useToast()
  const [report, setReport] = useState<IncidentReport | null>(null)
  const [summary, setSummary] = useState<IncidentSummary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [regenerating, setRegenerating] = useState(false)

  useEffect(() => {
    let cancelled = false
    evidenceApi.report(id).then(
      (r) => {
        if (cancelled) return
        setReport(r)
        setSummary(r.summary)
      },
      (err) => !cancelled && setError(errorMessage(err)),
    )
    return () => {
      cancelled = true
    }
  }, [id])

  // While the language model writes the summary, check back every few seconds.
  useEffect(() => {
    if (!summary?.pending) return
    const timer = setTimeout(() => {
      evidenceApi.summary(id).then(setSummary, () => setSummary((s) => s && { ...s }))
    }, 2500)
    return () => clearTimeout(timer)
  }, [summary, id])

  const regenerate = async () => {
    setRegenerating(true)
    try {
      setSummary(await evidenceApi.regenerateSummary(id))
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setRegenerating(false)
    }
  }

  if (error) return <ErrorNote>{error}</ErrorNote>
  if (!report || !summary) return <p className="text-sm text-zinc-500">Loading the report…</p>

  const inc = report.incident
  const level = LEVELS[inc.peak_level] ?? LEVELS[0]
  const ending =
    inc.status === 'ended' ? `${formatTime(inc.end!)}` : inc.status === 'ongoing' ? 'still going on' : 'no end recorded'
  const people = inc.people
  const list = (names: string[]) => (names.length ? names.join(', ') : 'none')

  return (
    <article className="report mx-auto max-w-5xl space-y-4">
      <div className="flex flex-wrap items-center gap-2 print:hidden">
        <a href={href('events')} className="mr-auto inline-flex items-center gap-1 text-sm text-blue-400 hover:text-blue-300">
          <ArrowLeft className="h-4 w-4" />
          Events
        </a>
        <Button icon={<Printer className="h-4 w-4" />} onClick={() => window.print()}>
          Print or save as PDF
        </Button>
        {/* download: a failed download must not replace the dashboard with an error page */}
        <a href={evidenceApi.packageUrl(inc.id)} download>
          <Button variant="primary" icon={<Download className="h-4 w-4" />}>
            Download evidence package
          </Button>
        </a>
      </div>

      <header className="space-y-2">
        <p className="hidden text-xs print:block">Guardian incident report · {formatDateTime(new Date().toISOString())}</p>
        <h2 className="text-xl font-semibold text-zinc-50">Incident at {inc.camera ?? 'a camera'}</h2>
        <p className="text-sm text-zinc-400">
          {formatDay(inc.start)} · {formatTime(inc.start)} to {ending} · {lengthText(inc.duration_seconds)}
        </p>
        <div className="flex flex-wrap gap-1.5">
          {inc.peak_level > 0 && (
            <Badge className={level.soft}>
              Peak level {inc.peak_level} · {inc.peak_label}
            </Badge>
          )}
          {inc.end_reason && <Badge>{inc.end_reason}</Badge>}
          {inc.test && <Badge>Test</Badge>}
          {inc.panic && <Badge className="bg-red-600/20 text-red-300 ring-red-500/40">Panic button</Badge>}
        </div>
        <p className="text-sm text-zinc-400">
          Unrecognised people when first detected: {people.unknown || 'none'} · Insiders recognised: {list(people.insiders)} · Remembered
          visitors: {list(people.visitors)}
        </p>
      </header>

      <Card
        title="Summary"
        actions={
          inc.status === 'ended' && (
            <Button size="sm" variant="ghost" className="print:hidden" icon={<RefreshCw className="h-3.5 w-3.5" />} loading={regenerating || summary.pending} onClick={regenerate}>
              Write again
            </Button>
          )
        }
      >
        <p className="text-sm leading-relaxed text-zinc-200">{summary.text}</p>
        <p className="print-muted mt-2 flex items-start gap-1.5 text-xs text-zinc-500">
          {summary.pending && <Loader2 className="mt-0.5 h-3 w-3 shrink-0 animate-spin print:hidden" />}
          {summaryNote(summary)}
        </p>
      </Card>

      <Card title="Timeline" bodyClassName="p-0">
        <ol className="divide-y divide-zinc-800">
          {report.timeline.map((t) => (
            <li key={t.event_id} className="flex flex-col gap-1 px-4 py-2 text-sm sm:flex-row sm:items-start sm:gap-3">
              <div className="flex shrink-0 items-center gap-2 sm:w-52">
                <time className="w-[5.75rem] whitespace-nowrap font-mono text-xs tabular-nums text-zinc-500" dateTime={t.time}>
                  {formatTime(t.time)}
                </time>
                <Badge className={SEVERITY_STYLE[t.severity]}>{eventLabel(t.type)}</Badge>
              </div>
              <span className="min-w-0 flex-1 break-words text-zinc-300">{t.text}</span>
            </li>
          ))}
        </ol>
      </Card>

      <Card title={`Keyframes (${report.keyframes.length})`}>
        {report.keyframes.length ? (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 print:grid-cols-4">
            {report.keyframes.map((k) => (
              <figure key={k.event_id}>
                <img src={evidenceApi.pictureUrl(k)} alt={k.text} loading="eager" className="aspect-video w-full rounded border border-zinc-800 bg-black object-contain" />
                <figcaption className="mt-1 text-xs text-zinc-400">
                  <span className="font-mono tabular-nums">{formatTime(k.time)}</span> · {eventLabel(k.type)}
                </figcaption>
              </figure>
            ))}
          </div>
        ) : (
          <p className="text-sm text-zinc-400">No pictures were kept for this incident.</p>
        )}
      </Card>

      <Card title="Evidence" className="print-break">
        <EvidenceTable items={report.evidence} />
      </Card>

      <footer className={cx('space-y-2 rounded-lg border border-zinc-800 p-4 text-xs leading-relaxed text-zinc-400')}>
        <h3 className="text-sm font-semibold text-zinc-200">How to verify</h3>
        <p>
          Guardian fingerprinted (SHA-256) each file when it saved it and signed the fingerprint into its evidence ledger. The
          verification column shows the result when this page was opened. To check the files yourself, download the evidence
          package: its VERIFY.txt has a short Python script that checks every file and signature against the public key.
        </p>
        <p>
          Public key fingerprint: <span className="break-all font-mono text-zinc-300">{report.vault.fingerprint}</span>
        </p>
        <p>
          This shows the files have not changed since Guardian saved them, as long as the private key stayed private. Someone with
          full access to the Guardian computer could change files and sign them again. Times are shown in this browser's time zone.
        </p>
      </footer>
    </article>
  )
}
