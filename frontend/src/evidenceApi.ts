// The evidence vault and incident reports (backend/evidence_api.py).
import { request } from './api'
import { formatDateTime, formatShortDate, formatTime } from './lib/format'

export type VerifyStatus = 'intact' | 'modified' | 'missing' | 'not_sealed' | 'ledger_broken'

export interface Verification {
  kind: 'clip' | 'picture'
  name: string
  status: VerifyStatus
  checked_at: string
  seq: number | null
  sealed_at: string | null
  sealed_late: boolean
  /** The fingerprint sealed in the ledger */
  sha256: string | null
  size: number | null
  /** The file's fingerprint now, when it exists */
  current_sha256: string | null
  /** What changed when modified: the file itself, or a clip's details file (camera, time, length) */
  changed: 'file' | 'details' | null
  /** The ledger entry where the chain breaks */
  broken_at: number | null
  /** The ledger's record of the file being deleted */
  deleted: { time: string; reason: string } | null
  exists: boolean
}

export interface VaultCheck {
  checked_at: string
  seconds: number
  entries: number
  broken_at: number | null
  files: number
  intact: number
  modified: string[]
  modified_count: number
  missing: string[]
  missing_count: number
  /** Sealed after the point where the ledger is damaged */
  unverifiable: number
  not_sealed: string[]
  not_sealed_count: number
  deleted: number
}

export interface VaultStatus {
  /** SHA-256 of the public key */
  fingerprint: string
  created: string | null
  entries: number
  clips: number
  pictures: number
  pending: number
  checking: boolean
  last_check: VaultCheck | null
}

export interface Incident {
  id: string
  camera: string | null
  start: string
  end: string | null
  /** unfinished: the event log has no end, e.g. Guardian stopped during it */
  status: 'ended' | 'ongoing' | 'unfinished'
  end_reason: string | null
  duration_seconds: number
  peak_level: number
  peak_label: string | null
  test: boolean
  panic: boolean
  people: { unknown: number; insiders: string[]; visitors: string[] }
  clips: string[]
  pictures: number
  warnings: number
  alerts: number
  siren: boolean
}

export interface TimelineItem {
  time: string
  type: string
  severity: string
  text: string
  event_id: number
  recording: string | null
  snapshot: string | null
}

export interface IncidentSummary {
  text: string
  source: 'llm' | 'template'
  /** Why the template was used */
  reason: string | null
  /** The language model is writing one; `text` is the template meanwhile */
  pending: boolean
  written_at: string | null
}

export type EvidenceItem =
  | (Verification & { event_id: number | null })
  | { kind: 'clip'; name: string; status: 'recording'; exists: false; size: null }

export interface IncidentReport {
  incident: Incident
  timeline: TimelineItem[]
  facts: string
  summary: IncidentSummary
  keyframes: TimelineItem[]
  evidence: EvidenceItem[]
  vault: { fingerprint: string }
}

const incident = (id: string) => `/api/incidents/${encodeURIComponent(id)}`

export const evidenceApi = {
  vault: () => request<VaultStatus>('/api/vault'),
  publicKeyUrl: '/api/vault/public-key.pem',
  verifyAll: () => request<{ started: boolean; checking: boolean; last_check: VaultCheck | null }>('/api/vault/verify', { method: 'POST' }),
  verifyRecording: (file: string) => request<Verification>(`/api/recordings/${encodeURIComponent(file)}/verify`),
  incidents: (recording?: string) => request<{ items: Incident[] }>(`/api/incidents${recording ? `?recording=${encodeURIComponent(recording)}` : ''}`),
  report: (id: string) => request<IncidentReport>(`${incident(id)}/report`),
  summary: (id: string) => request<IncidentSummary>(`${incident(id)}/summary`),
  regenerateSummary: (id: string) => request<IncidentSummary>(`${incident(id)}/summary`, { method: 'POST' }),
  packageUrl: (id: string) => `${incident(id)}/package.zip`,
  pictureUrl: (item: { event_id: number; snapshot: string | null }) =>
    `/api/events/${item.event_id}/snapshot.jpg?v=${encodeURIComponent(item.snapshot ?? '')}`,
}

export type Tone = 'good' | 'bad' | 'warn' | 'muted'

export const TONE_CLASS: Record<Tone, string> = {
  good: 'tone-good text-emerald-400',
  bad: 'tone-bad text-red-400',
  warn: 'tone-warn text-amber-400',
  muted: 'tone-muted text-zinc-400',
}

const sealedAt = (iso: string) => `${formatTime(iso)} on ${formatShortDate(iso)}`

/** A verification result in plain words. */
export function verifyMessage(v: EvidenceItem | Verification): { text: string; tone: Tone } {
  switch (v.status) {
    case 'intact':
      return {
        text: `Intact: matches the fingerprint sealed at ${v.sealed_at ? sealedAt(v.sealed_at) : '–'}${v.sealed_late ? ' (sealed late)' : ''}`,
        tone: 'good',
      }
    case 'modified':
      return {
        text: v.changed === 'details' ? 'Modified after it was sealed: its details (camera, time, length) were changed' : 'Modified after it was sealed',
        tone: 'bad',
      }
    case 'missing':
      return v.deleted
        ? { text: `Deleted ${formatDateTime(v.deleted.time)}: ${v.deleted.reason}. The ledger records the deletion.`, tone: 'muted' }
        : { text: 'Missing: the file is gone and the ledger has no record of it being deleted', tone: 'bad' }
    case 'not_sealed':
      return { text: 'Not sealed: Guardian has no fingerprint for this file', tone: 'warn' }
    case 'ledger_broken':
      return { text: `Can't be checked: the ledger is damaged at entry ${v.broken_at}`, tone: 'bad' }
    default:
      return { text: 'Still being recorded; it is sealed when it is saved', tone: 'muted' }
  }
}
