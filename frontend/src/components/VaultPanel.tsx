// The evidence vault in Settings → System: what is sealed, checking everything, and the public key.
import { useState } from 'react'
import { Download, ShieldCheck } from 'lucide-react'
import { evidenceApi, type VaultCheck } from '../evidenceApi'
import { formatDateTime } from '../lib/format'
import { errorMessage, useToast } from '../lib/toast'
import { usePoll } from '../lib/usePoll'
import { Button } from './ui'

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`
const ledgerEntries = (n: number) => `${n} ledger ${n === 1 ? 'entry' : 'entries'}`

function CheckResult({ check }: { check: VaultCheck }) {
  const problems = [
    check.broken_at && `the ledger is damaged at entry ${check.broken_at}`,
    check.modified_count && `${plural(check.modified_count, 'file')} modified after sealing (${check.modified.join(', ')})`,
    check.missing_count && `${plural(check.missing_count, 'file')} missing without a deletion record (${check.missing.join(', ')})`,
  ].filter(Boolean)
  return (
    <div className="space-y-1 text-sm">
      <p className={problems.length ? 'text-red-400' : 'text-emerald-400'}>
        {problems.length
          ? `Last check found problems: ${problems.join('; ')}.`
          : `Last check: all ${plural(check.files, 'sealed file')} intact, ledger unbroken.`}
      </p>
      <p className="text-xs text-zinc-500">
        Checked {formatDateTime(check.checked_at)} in {check.seconds}s · {ledgerEntries(check.entries)}
        {check.deleted ? ` · ${check.deleted} recorded deletions` : ''}
        {check.unverifiable ? ` · ${check.unverifiable} can't be checked past the damage` : ''}
        {check.not_sealed_count ? ` · ${plural(check.not_sealed_count, 'file')} not sealed` : ''}
      </p>
    </div>
  )
}

export default function VaultPanel() {
  const notify = useToast()
  const [starting, setStarting] = useState(false)
  const { data: v, refresh } = usePoll(evidenceApi.vault, 3000)

  const verifyAll = async () => {
    setStarting(true)
    try {
      await evidenceApi.verifyAll()
      refresh()
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setStarting(false)
    }
  }

  if (!v) return null
  return (
    <div className="space-y-3 border-t border-zinc-800 pt-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-zinc-100">Evidence vault</h3>
        <div className="flex flex-wrap gap-2">
          {/* download: a failed download must not replace the dashboard with an error page */}
          <a href={evidenceApi.publicKeyUrl} download>
            <Button size="sm" icon={<Download className="h-3.5 w-3.5" />}>
              Public key
            </Button>
          </a>
          <Button size="sm" variant="primary" icon={<ShieldCheck className="h-3.5 w-3.5" />} loading={starting || v.checking} onClick={verifyAll}>
            {v.checking ? 'Verifying…' : 'Verify all'}
          </Button>
        </div>
      </div>
      <p className="text-sm text-zinc-300">
        {plural(v.clips, 'clip')} and {plural(v.pictures, 'event picture')} sealed · {ledgerEntries(v.entries)}
        {v.pending ? ` · ${v.pending} being sealed` : ''}
      </p>
      {v.last_check ? <CheckResult check={v.last_check} /> : <p className="text-sm text-zinc-500">Not checked yet.</p>}
      <p className="text-xs text-zinc-500">
        Public key fingerprint: <span className="break-all font-mono text-zinc-400">{v.fingerprint}</span>
      </p>
      <p className="hint">
        Guardian fingerprints (SHA-256) every clip and event picture when it saves it, and signs the fingerprint into an append-only
        ledger with a key kept on this computer. Verifying proves a file hasn't changed since Guardian saved it, as long as that private
        key stayed private. It can't prove what happened before the file was saved, and someone with full access to this computer could
        change files and sign them again. Note the public key fingerprint somewhere else to compare later.
      </p>
    </div>
  )
}
