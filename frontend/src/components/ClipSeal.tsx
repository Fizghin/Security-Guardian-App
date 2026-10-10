// A clip's seal in the evidence vault: the badge on the Recordings page, and checking it in the player.
import { useEffect, useState } from 'react'
import { FileText, ShieldCheck } from 'lucide-react'
import type { Recording } from '../api'
import { evidenceApi, TONE_CLASS, verifyMessage, type Incident, type Verification } from '../evidenceApi'
import { formatDateTime } from '../lib/format'
import { href } from '../lib/route'
import { errorMessage } from '../lib/toast'
import { Badge, Button } from './ui'

export function SealBadge({ rec }: { rec: Recording }) {
  if (!rec.vault) return null
  return (
    <Badge>
      <ShieldCheck className="h-3 w-3 text-emerald-400" />
      <span title={`Fingerprint sealed in the evidence vault ${formatDateTime(rec.vault.sealed_at)}`}>{rec.vault.sealed_late ? 'Sealed late' : 'Sealed'}</span>
    </Badge>
  )
}

/** Verify the clip against the ledger, and link to the incident it was recorded in. */
export function ClipVerify({ file }: { file: string }) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<Verification | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [incident, setIncident] = useState<Incident | null>(null)

  useEffect(() => {
    let cancelled = false
    evidenceApi.incidents(file).then(
      (r) => !cancelled && setIncident(r.items[0] ?? null),
      () => {}, // the link is only a shortcut
    )
    return () => {
      cancelled = true
    }
  }, [file])

  const verify = async () => {
    setBusy(true)
    setError(null)
    try {
      setResult(await evidenceApi.verifyRecording(file))
    } catch (err) {
      setResult(null)
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const message = result && verifyMessage(result)
  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
      <Button size="sm" icon={<ShieldCheck className="h-3.5 w-3.5" />} loading={busy} onClick={verify}>
        Verify
      </Button>
      {message && <span className={TONE_CLASS[message.tone]}>{message.text}</span>}
      {error && <span className="text-red-400">{error}</span>}
      {!message && !error && <span className="text-xs text-zinc-500">Checks the file against the fingerprint Guardian sealed when it saved it.</span>}
      {incident && (
        <a href={href('incident', incident.id)} className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-blue-400 hover:text-blue-300">
          <FileText className="h-3.5 w-3.5" />
          Incident report
        </a>
      )}
    </div>
  )
}
