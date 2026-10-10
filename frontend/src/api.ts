// Typed client for the Guardian backend. All URLs are relative: the dashboard is served by
// the backend in production and proxied by Vite in development.

export type Severity = 'INFO' | 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
export const SEVERITIES: Severity[] = ['INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL']

export type SourceKind = 'none' | 'auto' | 'index' | 'url' | 'file' | 'phone'
export type AudioOutput = 'server' | 'device' | 'both'
export type MessageSource = 'llm' | 'cached' | 'fallback' | 'operator' | 'greeting'

export interface PhoneInfo {
  battery?: number
  charging?: boolean
  camera?: string
  user_agent?: string
  address?: string
  state?: string
  online: boolean
  last_contact: number | null
}

export interface CameraAudio {
  /** Where warnings, the siren and the owner's voice play */
  output: AudioOutput
  /** The phone's audio link is connected (it can play the owner's voice) */
  link: boolean
  /** The phone's microphone is live */
  mic: boolean
  /** The microphone's latest peak level in dBFS */
  level_db: number | null
  /** Someone is talking through this camera */
  talking: boolean
  listeners: number
}

export interface CameraStatus {
  id: string
  name: string
  source: string
  kind: SourceKind
  audio: CameraAudio
  active_index: number | null
  connected: boolean
  error: string | null
  width: number
  height: number
  fps: number
  phone?: PhoneInfo
  pipeline: { fps: number; motion: boolean; test_seconds_left: number; error: string | null }
  threat_level: number
  threat_label: string
  manual_alarm: boolean
  test: boolean
  incident_started: number | null
  incident_seconds: number
  persons: number
  pending: number
  insiders_in_view: string[]
  siren_active: boolean
  last_message: string | null
  last_message_time: number | null
  last_message_source: MessageSource | null
  recording: { active: boolean; file: string | null; started: number | null; stopping: boolean }
  /** The picture changed shape since the zones were drawn (e.g. a phone turned on its side). */
  zones_mismatch: boolean
}

export interface Status {
  armed: boolean
  panic: boolean
  threat_level: number
  alarm_cameras: string[]
  cameras: CameraStatus[]
  detector: { loaded: boolean; model: string; device: string | null; inference_ms: number | null; error: string | null }
  siren: { active: boolean; available: boolean; player: string | null; started_at: number | null }
  voice: { available: boolean | null; engine: string | null; error: string | null; speaking: boolean; queued: number }
  ai: AIStatus
  faces: FaceStatus
  phone: { enabled: boolean; port: number }
  /** How this computer plays the owner's voice: as they speak, after they let go, or not at all */
  talk: TalkPlayer
  schedule: ScheduleStatus
  server_time: number
  /** The Guardian computer's time zone, which schedule times are in; utc_offset like "+01:00". */
  time_zone: { name: string; utc_offset: string }
}

export interface TalkPlayer {
  mode: 'live' | 'after' | null
  player: string | null
}

export interface ScheduleStatus {
  enabled: boolean
  /** Whether the schedule wants Guardian armed right now (null when it is off). */
  active: boolean | null
  /** When the schedule next changes the armed state, in the Guardian computer's local time. */
  next_change: string | null
  /** A scheduled disarm is waiting for an alarm to be reset or clear. */
  waiting: boolean
}

export interface ScheduleRule {
  /** 0 = Monday … 6 = Sunday */
  days: number[]
  start: string
  end: string
}

export interface AIStatus {
  provider: string
  base_url: string
  model: string | null
  busy: boolean
  loading: boolean
  last_source: string | null
  last_error: string | null
  last_latency_ms: number | null
  ready_lines: number
  rejected_replies: number
}

export interface Pairing {
  urls: string[]
  qr_svg: string | null
  port: number
  public_url: string
}

export interface FaceStatus {
  state: 'idle' | 'downloading' | 'ready' | 'error'
  error: string | null
  enrolled: number
}

export interface CameraConfig {
  id: string
  name: string
  source: string
  enabled: boolean
  audio: AudioOutput
  kind: SourceKind
  token?: string
  /** Areas where people count: polygons of [x, y] corners in 0..1. Empty = the whole picture. */
  zones: number[][][]
  /** Width / height of the picture the zones were drawn on (null when unknown or no zones). */
  zones_aspect: number | null
}

export interface SecurityEvent {
  id: number
  timestamp: string
  event_type: string
  description: string
  severity: Severity
  recording: string | null
  camera: string | null
  /** File name of the picture of the moment, shown with eventSnapshotUrl; null when there is none. */
  snapshot: string | null
}

export interface EventPage {
  total: number
  items: SecurityEvent[]
}

export interface EventSummary {
  hours: number
  bucket: 'hour' | 'day'
  total: number
  series: ({ start: string; total: number } & Record<Severity, number>)[]
  by_type: Record<string, number>
  by_severity: Record<Severity, number>
  types: string[]
  cameras: string[]
}

export interface EventFilters {
  type?: string
  severity?: string
  since?: string
  search?: string
  camera?: string
}

export interface Recording {
  file: string
  size: number
  started: string
  duration: number | null
  reason: string
  max_level: number | null
  playable: boolean
  camera_id: string | null
  camera: string | null
  thumbnail: boolean
  /** Sealed in the evidence vault */
  sealed?: boolean
}

export type IntegrityStatus = 'verified' | 'tampered' | 'unsealed' | 'missing' | 'removed'

export interface ClipIntegrity {
  file: string
  status: IntegrityStatus
  detail: string
  sealed_at?: string
  removed_at?: string
  seq?: number
  sha256?: string
  current_sha256?: string
  backfilled?: boolean
  signature_ok?: boolean
}

export interface LedgerEntry {
  seq: number
  time: string
  action: 'sealed' | 'removed'
  file: string
  sha256?: string
  size?: number
  reason?: string
  backfilled?: boolean
  hash: string
  prev: string
}

export interface ChainCheck {
  ok: boolean
  entries: number
  problems: { seq: number; problem: string }[]
  head: string
}

export interface EvidenceStatus {
  entries: number
  sealed: number
  fingerprint: string
  head: string
  chain: ChainCheck
  recent: LedgerEntry[]
}

export interface EvidenceAudit {
  ok: boolean
  chain: ChainCheck
  clips: ClipIntegrity[]
  counts: Partial<Record<IntegrityStatus, number>>
  unsealed: string[]
  checked_at: string
}

export interface IncidentReport {
  file: string
  camera: string
  reason: string
  reason_label: string
  started: string
  ended: string
  duration: number
  max_level: number
  summary: string
  stats: {
    warnings: number
    alerts: number
    siren: boolean
    returning_visitor: boolean
    insiders: string[]
    lasted: number | null
    first_seen: string | null
    escalations: { offset: number; level: number; label: string }[]
    pictures: number
    peak_level: number
  }
  timeline: (SecurityEvent & { offset: number; local_time: string })[]
  integrity: ClipIntegrity
  fingerprint: string
  generated: string
}

export interface Insights {
  days: number
  generated: string
  time_zone: string
  weekdays: string[]
  grid: number[][]
  grid_max: number
  hours: number[]
  series: { date: string; incidents: number; alerts: number; sounds: number }[]
  cameras: { camera: string; incidents: number; alerts: number; sirens: number; sounds: number; insiders: number; offline: number }[]
  totals: { incidents: number; alerts: number; sirens: number; sounds: number; insiders: number; returning: number; offline: number }
  last_24h: { incidents: number; alerts: number; sirens: number; sounds: number; night_incidents: number }
  night: { start: number; end: number; last_night: number; baseline: number; ratio: number }
  threat: { score: number; level: 'calm' | 'low' | 'elevated' | 'high'; reasons: string[] }
  findings: { kind: 'info' | 'warning' | 'alert'; text: string }[]
}

export interface Heatmap {
  cols: number
  rows: number
  grid: number[][]
  max: number
  samples: number
  strangers: number
  since: number | null
  hotspots: { x: number; y: number; share: number; where: string }[]
}

export interface RecordingList {
  items: Recording[]
  usage_bytes: number
  active: { camera: string; file: string; stopping: boolean }[]
  encoder: string
}

export interface Insider {
  name: string
  added: number
  photos: { file: string; usable: boolean }[]
}

export interface InsiderList {
  items: Insider[]
  faces: FaceStatus
  enabled: boolean
}

export interface UploadResult {
  file: string
  ok: boolean
  error: string | null
  warning?: string | null
}

export interface LiveFace {
  index: number
  thumbnail: string
  usable: boolean
  reason: string | null
  match: string | null
  score: number
}

export interface CheckedFace {
  thumbnail: string
  match: string | null
  score: number
  runner_up: number
  quality_ok: boolean
  reason: string | null
}

export interface Visitor {
  id: number
  /** The name given to them, or "Visitor 12" */
  name: string
  label: string | null
  note: string
  first_seen: string
  last_seen: string
  visits: number
  cameras: string[]
  /** Face photo file names, best first; shown with visitorPhotoUrl */
  faces: string[]
}

export interface VisitorSighting {
  id: number
  started: string
  last_seen: string | null
  camera: string | null
  /** This sighting started a new visit */
  new_visit: boolean
  /** The event with a picture of the moment, while it is still in the log */
  event: SecurityEvent | null
}

export interface VisitorDetail extends Visitor {
  /** Newest first */
  sightings: VisitorSighting[]
}

export interface VisitorList {
  items: Visitor[]
  /** Face recognition and "Remember strangers' faces" are both on */
  enabled: boolean
  retention_days: number
  visit_gap_minutes: number
  faces: FaceStatus
}

export interface Settings {
  armed: boolean
  cameras: CameraConfig[]
  detection: {
    confidence: number
    min_person_height: number
    interval_ms: number
    face_recognition: boolean
    face_match_threshold: number
    identify_seconds: number
    insider_grace_seconds: number
    sound_alerts: 'off' | 'log' | 'alert'
    sound_sensitivity: number
    remember_visitors: boolean
    visitor_retention_days: number
    visit_gap_minutes: number
    motion_trails: boolean
    activity_heatmap: boolean
  }
  escalation: {
    level2_after: number
    level3_after: number
    level4_after: number
    clear_after: number
    record_at_level: number
    alert_at_level: number
    siren_enabled: boolean
    siren_at_level: number
    siren_max_seconds: number
    offline_alert_seconds: number
  }
  recording: { preroll_seconds: number; postroll_seconds: number; max_clip_seconds: number; retention_days: number }
  ai: {
    provider: 'ollama' | 'openai'
    base_url: string
    model: string
    api_key_set: boolean
    timeout_seconds: number
    intimidation: number
    humor: number
    persistence: number
    voice_enabled: boolean
    voice_rate: number
    greet_insiders: boolean
    greet_cooldown_minutes: number
  }
  phone: { fps: number; max_width: number; quality: number }
  schedule: { enabled: boolean; rules: ScheduleRule[] }
  notifications: {
    discord_webhook_set: boolean
    telegram_token_set: boolean
    telegram_chat_id: string
    ntfy_url: string
    ntfy_token_set: boolean
    webhook_url_set: boolean
    smtp_host: string
    smtp_port: number
    smtp_user: string
    smtp_password_set: boolean
    email_to: string
    /** Channels the saved settings are complete for; the server decides, the same way it does when sending. */
    configured: NotificationChannel[]
  }
}

export type NotificationChannel = 'discord' | 'telegram' | 'ntfy' | 'webhook' | 'email'

export interface NotificationResult {
  channel: NotificationChannel
  ok: boolean
  error: string | null
  /** Set when the alert went out without part of it, e.g. a picture the server refused. */
  note: string | null
}

/** Write-only values: the dashboard can set or clear them but never reads them back. */
export interface NotificationSecrets {
  discord_webhook?: string
  telegram_token?: string
  ntfy_token?: string
  webhook_url?: string
  smtp_password?: string
}

type Section = Exclude<keyof Settings, 'armed' | 'cameras'>
export type SettingsPatch = { [K in Section]?: Partial<Settings[K]> & { api_key?: string } & NotificationSecrets }

export interface AITestResult {
  text: string
  source: 'llm' | 'fallback'
  model: string | null
  latency_ms: number
  error: string | null
  spoken?: boolean
}

export interface SystemInfo {
  cpu_percent: number
  cpu_count: number
  process_cpu_percent: number
  memory_percent: number
  memory_used: number
  memory_total: number
  process_memory: number
  disk_free: number
  disk_total: number
  uptime_seconds: number
  platform: string
  python: string
  data_dir: string
  detector: Status['detector']
  recording_encoder: string
  recordings_bytes: number
  voice: Status['voice']
  siren: Status['siren']
  talk: TalkPlayer
  faces: FaceStatus
  ai: AIStatus
  notifications: { discord: boolean; telegram: boolean; ntfy: boolean; webhook: boolean; email: boolean; any: boolean; email_to: string | null }
  phone: { enabled: boolean; port: number; addresses: string[]; public_url: string }
}

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, init)
  } catch {
    throw new ApiError('Cannot reach the Guardian server', 0)
  }
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') message = body.detail
      else if (Array.isArray(body.detail)) message = body.detail.map((d: { msg: string }) => d.msg).join('; ')
    } catch {
      // body was not JSON; keep the status line
    }
    throw new ApiError(message, res.status)
  }
  return res.json() as Promise<T>
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
})

const query = (params: Record<string, string | number | undefined>) => {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '') q.set(k, String(v))
  const s = q.toString()
  return s ? `?${s}` : ''
}

const cam = (id: string) => `/api/cameras/${encodeURIComponent(id)}`
const socketUrl = (path: string) => `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${path}`

export const api = {
  status: () => request<Status>('/api/status'),
  arm: (armed: boolean) => request<{ armed: boolean }>('/api/arm', json('POST', { armed })),
  panic: () => request<{ ok: boolean }>('/api/panic', json('POST')),
  resetAlarm: () => request<{ ok: boolean; was_active: boolean }>('/api/alarm/reset', json('POST')),
  speak: (text: string, cameraId?: string) => request<{ ok: boolean }>('/api/speak', json('POST', { text, camera_id: cameraId })),

  cameras: () => request<{ cameras: CameraConfig[]; phone: SystemInfo['phone'] }>('/api/cameras'),
  addCamera: (body: { name: string; source: string; audio?: AudioOutput }) =>
    request<CameraConfig>('/api/cameras', json('POST', body)),
  updateCamera: (id: string, body: Partial<Pick<CameraConfig, 'name' | 'source' | 'enabled' | 'audio' | 'zones' | 'zones_aspect'>>) =>
    request<CameraConfig>(cam(id), json('PATCH', body)),
  deleteCamera: (id: string) => request<{ ok: boolean }>(cam(id), { method: 'DELETE' }),
  resetPhoneLink: (id: string) => request<CameraConfig>(`${cam(id)}/reset-link`, json('POST')),
  pairing: (id: string) => request<Pairing>(`${cam(id)}/pairing`),
  testSource: (source: string) =>
    request<{ ok: boolean; error?: string; width?: number; height?: number; preview?: string }>('/api/cameras/test', json('POST', { source })),
  scanCameras: () => request<{ cameras: { index: number; width: number; height: number; in_use?: boolean }[] }>('/api/cameras/scan'),
  testIntrusion: (id: string, seconds: number) =>
    request<{ ok: boolean; seconds: number }>(`${cam(id)}/test-intrusion`, json('POST', { seconds })),
  cameraFaces: (id: string) => request<{ faces: LiveFace[] }>(`${cam(id)}/faces`),
  snapshotUrl: (id: string) => `${cam(id)}/snapshot.jpg`,
  rawSnapshotUrl: (id: string) => `${cam(id)}/snapshot.jpg?raw=1&t=${Date.now()}`,
  streamUrl: (id: string) => socketUrl(`/ws/stream/${encodeURIComponent(id)}`),
  talkUrl: (id: string) => socketUrl(`/ws/talk/${encodeURIComponent(id)}`),
  listenUrl: (id: string) => socketUrl(`/ws/listen/${encodeURIComponent(id)}`),

  events: (filters: EventFilters, limit = 50, offset = 0) =>
    request<EventPage>(`/api/events${query({ ...filters, limit, offset })}`),
  eventSummary: (hours: number) => request<EventSummary>(`/api/events/summary?hours=${hours}`),
  eventsCsvUrl: (filters: EventFilters) => `/api/events/export.csv${query({ ...filters })}`,
  clearEvents: () => request<{ deleted: number }>('/api/events', { method: 'DELETE' }),

  recordings: (camera?: string) => request<RecordingList>(`/api/recordings${query({ camera })}`),
  recordingUrl: (file: string, download = false) =>
    `/api/recordings/${encodeURIComponent(file)}${download ? '?download=true' : ''}`,
  thumbnailUrl: (file: string) => `/api/recordings/${encodeURIComponent(file)}/thumbnail`,
  // The file name makes the URL unique per picture: ids start again at 1 after the log is cleared.
  eventSnapshotUrl: (event: SecurityEvent) => `/api/events/${event.id}/snapshot.jpg?v=${encodeURIComponent(event.snapshot ?? '')}`,
  verifyRecording: (file: string) => request<ClipIntegrity>(`/api/recordings/${encodeURIComponent(file)}/verify`),
  incidentReport: (file: string) => request<IncidentReport>(`/api/recordings/${encodeURIComponent(file)}/report`),
  incidentReportUrl: (file: string) => `/api/recordings/${encodeURIComponent(file)}/report.html`,
  deleteRecording: (file: string) => request<{ ok: boolean }>(`/api/recordings/${encodeURIComponent(file)}`, { method: 'DELETE' }),

  evidence: () => request<EvidenceStatus>('/api/evidence'),
  evidenceAudit: () => request<EvidenceAudit>('/api/evidence/audit', json('POST')),
  sealUnsealed: () => request<{ sealed: number }>('/api/evidence/seal', json('POST')),
  ledgerUrl: '/api/evidence/ledger.jsonl',
  publicKeyUrl: '/api/evidence/public-key.pem',

  insights: (days: number) => request<Insights>(`/api/insights?days=${days}`),
  heatmap: (id: string) => request<Heatmap>(`${cam(id)}/heatmap`),
  resetHeatmap: (id: string) => request<{ ok: boolean }>(`${cam(id)}/heatmap`, { method: 'DELETE' }),

  insiders: () => request<InsiderList>('/api/insiders'),
  addInsiderPhotos: (name: string, files: File[]) => {
    const form = new FormData()
    form.append('name', name)
    files.forEach((f) => form.append('files', f))
    return request<{ results: UploadResult[] }>('/api/insiders', { method: 'POST', body: form })
  },
  captureInsider: (cameraId: string, index: number, name: string) =>
    request<{ name: string; file: string; warning: string | null }>('/api/insiders/capture', json('POST', { camera_id: cameraId, index, name })),
  checkPhoto: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<{ faces: CheckedFace[]; threshold: number }>('/api/insiders/check', { method: 'POST', body: form })
  },
  insiderPhotoUrl: (name: string, file: string) =>
    `/api/insiders/${encodeURIComponent(name)}/photos/${encodeURIComponent(file)}`,
  deleteInsiderPhoto: (name: string, file: string) =>
    request<{ ok: boolean }>(`/api/insiders/${encodeURIComponent(name)}/photos/${encodeURIComponent(file)}`, { method: 'DELETE' }),
  deleteInsider: (name: string) => request<{ ok: boolean }>(`/api/insiders/${encodeURIComponent(name)}`, { method: 'DELETE' }),

  visitors: (repeatOnly: boolean) => request<VisitorList>(`/api/visitors${query({ repeat: repeatOnly ? 'true' : undefined })}`),
  visitor: (id: number) => request<VisitorDetail>(`/api/visitors/${id}`),
  updateVisitor: (id: number, body: { label?: string | null; note?: string | null }) =>
    request<VisitorDetail>(`/api/visitors/${id}`, json('PATCH', body)),
  makeInsider: (id: number, name: string) =>
    request<{ name: string; added: number; skipped: number }>(`/api/visitors/${id}/make-insider`, json('POST', { name })),
  forgetVisitor: (id: number) => request<{ ok: boolean }>(`/api/visitors/${id}`, { method: 'DELETE' }),
  forgetAllVisitors: () => request<{ deleted: number }>('/api/visitors', { method: 'DELETE' }),
  // The file name makes the URL unique per photo: a visitor's best faces change as better ones are seen.
  visitorPhotoUrl: (visitor: Visitor, n: number) =>
    `/api/visitors/${visitor.id}/photos/${n}.jpg?v=${encodeURIComponent(visitor.faces[n] ?? '')}`,

  settings: () => request<Settings>('/api/settings'),
  updateSettings: (patch: SettingsPatch) => request<Settings>('/api/settings', json('PATCH', patch)),
  aiModels: (provider: string, baseUrl: string) =>
    request<{ models: string[]; error: string | null }>(`/api/ai/models${query({ provider, base_url: baseUrl })}`),
  aiTest: (level: number, speak: boolean) => request<AITestResult>('/api/ai/test', json('POST', { level, speak })),

  system: () => request<SystemInfo>('/api/system'),
  // POST, so a token typed in stays out of URLs that proxies and tunnels log
  telegramChats: (token = '') =>
    request<{ chats: { id: string; name: string; type: string }[] }>('/api/notifications/telegram/chats', json('POST', { token })),
  testNotifications: () => request<{ results: NotificationResult[] }>('/api/notifications/test', json('POST')),
}
