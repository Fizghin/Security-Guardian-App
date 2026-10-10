import { ShieldAlert, ShieldCheck, ShieldQuestion } from 'lucide-react'
import type { ClipIntegrity } from '../api'
import { INTEGRITY } from '../lib/integrity'
import { Badge } from './ui'

export default function IntegrityBadge({ result }: { result: Pick<ClipIntegrity, 'status'> }) {
  const s = INTEGRITY[result.status]
  const Icon = result.status === 'verified' ? ShieldCheck : result.status === 'unsealed' || result.status === 'removed' ? ShieldQuestion : ShieldAlert
  return (
    <Badge className={s.style}>
      <Icon className="h-3 w-3" />
      {s.label}
    </Badge>
  )
}
