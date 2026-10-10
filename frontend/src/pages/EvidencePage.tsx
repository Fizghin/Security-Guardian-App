import { useState } from 'react'
import { CheckCircle2, Download, FileKey2, Fingerprint, Link2, Lock, ShieldAlert, ShieldCheck, Trash2 } from 'lucide-react'
import { api, type EvidenceAudit, type IntegrityStatus } from '../api'
import IntegrityBadge from '../components/IntegrityBadge'
import { Badge, Button, Card, Empty, ErrorNote, StatTile } from '../components/ui'
import { cx } from '../lib/cx'
import { INTEGRITY } from '../lib/integrity'
import { formatDateTime } from '../lib/format'
import { errorMessage, useToast } from '../lib/toast'
import { usePoll } from '../lib/usePoll'

const short = (hash: string) => `${hash.slice(0, 10)}…${hash.slice(-6)}`

export default function EvidencePage() {
  const notify = useToast()
  const { data, error, refresh } = usePoll(() => api.evidence(), 10000)
  const [audit, setAudit] = useState<EvidenceAudit | null>(null)
  const [busy, setBusy] = useState<'audit' | 'seal' | null>(null)

  const runAudit = async () => {
    setBusy('audit')
    try {
      const result = await api.evidenceAudit()
      setAudit(result)
      notify(result.ok ? 'Every sealed clip is intact' : 'Problems found, see below', result.ok ? 'success' : 'error')
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(null)
    }
  }

  const seal = async () => {
    setBusy('seal')
    try {
      const { sealed } = await api.sealUnsealed()
      notify(sealed ? `Sealed ${sealed} clip${sealed === 1 ? '' : 's'}` : 'Every clip is already sealed', 'success')
      refresh()
      if (audit) runAudit()
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(null)
    }
  }

  const chainOk = data?.chain.ok ?? true

  return (
    <div className="space-y-4">
      {error && <ErrorNote>{error.message}</ErrorNote>}

      <section
        className={cx(
          'relative overflow-hidden rounded-xl border p-5',
          chainOk ? 'border-emerald-900/60 bg-gradient-to-br from-emerald-950/50 via-zinc-900/60 to-zinc-900/60' : 'border-red-900 bg-gradient-to-br from-red-950/70 to-zinc-900/60',
        )}
      >
        <div className="flex flex-wrap items-start gap-4">
          <div className={cx('rounded-xl p-3', chainOk ? 'bg-emerald-500/15 text-emerald-300' : 'bg-red-500/20 text-red-300')}>
            {chainOk ? <ShieldCheck className="h-7 w-7" /> : <ShieldAlert className="h-7 w-7" />}
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-semibold text-zinc-50">{chainOk ? 'Evidence chain intact' : 'Evidence chain broken'}</h2>
            <p className="mt-1 max-w-2xl text-sm text-zinc-400">
              Every saved clip is fingerprinted with SHA-256 and written to an append-only ledger. Each entry is linked to the one before it
              and signed with a key that never leaves this computer, so editing, trimming or deleting a clip, or rewriting the ledger, shows
              up here.
            </p>
            {!chainOk &&
              data?.chain.problems.map((p) => (
                <p key={`${p.seq}${p.problem}`} className="mt-1 text-sm text-red-300">
                  Entry #{p.seq}: {p.problem}
                </p>
              ))}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" icon={<CheckCircle2 className="h-4 w-4" />} loading={busy === 'audit'} onClick={runAudit}>
              Verify all clips
            </Button>
            <Button icon={<Lock className="h-4 w-4" />} loading={busy === 'seal'} onClick={seal}>
              Seal unsealed clips
            </Button>
          </div>
        </div>
      </section>

      {data && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatTile label="Sealed clips" value={data.sealed} detail="still on disk" icon={<Lock className="h-4 w-4" />} tone="good" />
          <StatTile label="Ledger entries" value={data.entries} detail="seals and deletions" icon={<Link2 className="h-4 w-4" />} />
          <StatTile label="Key fingerprint" value={<span className="font-mono text-sm">{data.fingerprint}</span>} detail="Ed25519 signing key" icon={<Fingerprint className="h-4 w-4" />} />
          <StatTile label="Chain head" value={<span className="font-mono text-sm">{short(data.head)}</span>} detail="hash of the latest entry" icon={<FileKey2 className="h-4 w-4" />} />
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <a href={api.ledgerUrl} download>
          <Button icon={<Download className="h-4 w-4" />}>Download ledger</Button>
        </a>
        <a href={api.publicKeyUrl} download>
          <Button icon={<FileKey2 className="h-4 w-4" />}>Download public key</Button>
        </a>
        <span className="self-center text-xs text-zinc-500">Hand both to anyone who needs to check the signatures independently.</span>
      </div>

      {audit && (
        <Card
          title={`Verification · ${formatDateTime(audit.checked_at)}`}
          actions={
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(audit.counts).map(([status, n]) => (
                <Badge key={status} className={INTEGRITY[status as IntegrityStatus].style}>
                  {n} {INTEGRITY[status as IntegrityStatus].label.toLowerCase()}
                </Badge>
              ))}
              {audit.unsealed.length > 0 && <Badge className={INTEGRITY.unsealed.style}>{audit.unsealed.length} not sealed</Badge>}
            </div>
          }
          bodyClassName="p-0"
        >
          {audit.clips.length === 0 && audit.unsealed.length === 0 ? (
            <Empty title="No clips to check yet" />
          ) : (
            <ul className="divide-y divide-zinc-800/80">
              {[...audit.clips.filter((c) => c.status !== 'verified'), ...audit.clips.filter((c) => c.status === 'verified')].map((c) => (
                <li key={c.file} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 text-sm">
                  <IntegrityBadge result={c} />
                  <span className="min-w-0 truncate font-mono text-xs text-zinc-300">{c.file}</span>
                  <span className="ml-auto text-xs text-zinc-500">{c.detail}</span>
                </li>
              ))}
              {audit.unsealed.map((f) => (
                <li key={f} className="flex flex-wrap items-center gap-3 px-4 py-2.5 text-sm">
                  <IntegrityBadge result={{ status: 'unsealed' }} />
                  <span className="truncate font-mono text-xs text-zinc-300">{f}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}

      <Card title="Ledger" actions={<span className="text-xs text-zinc-500">Newest first</span>} bodyClassName="p-0">
        {data && data.recent.length === 0 ? (
          <Empty icon={<Lock className="h-8 w-8" />} title="Nothing sealed yet">
            Clips are sealed automatically the moment they are saved.
          </Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead className="text-left text-xs text-zinc-500">
                <tr>
                  <th className="px-4 py-2 font-medium">#</th>
                  <th className="px-4 py-2 font-medium">When</th>
                  <th className="px-4 py-2 font-medium">Action</th>
                  <th className="px-4 py-2 font-medium">Clip</th>
                  <th className="px-4 py-2 font-medium">SHA-256 / reason</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800/80">
                {data?.recent.map((e) => (
                  <tr key={e.seq}>
                    <td className="px-4 py-2 tabular-nums text-zinc-500">{e.seq}</td>
                    <td className="whitespace-nowrap px-4 py-2 text-zinc-300">{formatDateTime(e.time)}</td>
                    <td className="px-4 py-2">
                      {e.action === 'sealed' ? (
                        <Badge className="bg-emerald-500/10 text-emerald-300 ring-emerald-500/30">
                          <Lock className="h-3 w-3" /> Sealed{e.backfilled ? ' later' : ''}
                        </Badge>
                      ) : (
                        <Badge>
                          <Trash2 className="h-3 w-3" /> Deleted
                        </Badge>
                      )}
                    </td>
                    <td className="max-w-[260px] truncate px-4 py-2 font-mono text-xs text-zinc-300">{e.file}</td>
                    <td className="px-4 py-2 font-mono text-xs text-zinc-500">{e.sha256 ? short(e.sha256) : e.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  )
}
