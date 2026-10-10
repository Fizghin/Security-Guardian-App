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
  /** Still things on a spot the owner taught Guardian to ignore; not counted in persons */
  ignored: number
  siren_active: boolean
  last_message: string | null
  last_message_time: number | null
  last_message_source: MessageSource | null
  recording: { active: boolean; file: string | null; started: number | null; stopping: boolean }
  /** The picture changed shape since the zones were drawn (e.g. a phone turned on its side). */
  zones_mismatch: boolean
  /** The camera's learned routine; unusual_now while an incident that started at a usually quiet time goes on. */
  routine: { learning: boolean; days_watched: number; days_needed: number; unusual_now: boolean }
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
  /** Who was in that picture (detections, escalations, alerts and recognised people) */
  details: EventDetails | null
  /** The owner's verdict on the event */
  feedback: Verdict | null
}

export type Verdict = 'real' | 'false_alarm' | 'wrong_person'

export interface EventPerson {
  /** [x1, y1, x2, y2] as fractions of the picture */
  box: number[]
  confidence: number
  status: 'known' | 'unknown' | 'pending'
  identity: string | null
  track_id: number | null
  face_visible: boolean
  match_score: number | null
  simulated?: boolean
}

export interface EventDetails {
  camera_id: string
  people: EventPerson[]
  /** On events that recognised an insider: who */
  insider?: string
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
  /** learned: added by Guardian itself from a clear sighting, not by the owner */
  photos: { file: string; usable: boolean; learned: boolean }[]
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

export interface LearnedSpot {
  id: string
  camera_id: string
  /** null when the camera has been removed */
  camera: string | null
  /** [x1, y1, x2, y2] as fractions of the picture */
  box: number[]
  /** How many times it was taught */
  taught: number
  /** Epoch seconds */
  created: number
  last_matched: number | null
  /** The events marked "False alarm" that taught it */
  events: number[]
}

export interface SpotSuggestion {
  id: string
  camera_id: string
  camera: string | null
  box: number[]
  created: number
  /** The event with the picture of the moment, while it is still in the log */
  event: SecurityEvent | null
}

export interface LearnedFaces {
  name: string
  /** All of the insider's photos, the owner's and learned ones */
  photos: number
  learned: number
  last_learned: number | null
  /** Learned photo file names, newest first */
  files: string[]
}

export interface FeedbackCounts {
  camera: string | null
  real: number
  false_alarm: number
  wrong_person: number
}

export interface Learning {
  settings: Settings['learning']
  spots: LearnedSpot[]
  suggestions: SpotSuggestion[]
  faces: LearnedFaces[]
  feedback: { days: number; cameras: FeedbackCounts[] }
  rules: { still_minutes: number; max_learned: number; learn_every_minutes: number }
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
  learning: { learn_from_feedback: boolean; improve_faces: boolean; unusual_activity: 'off' | 'log' | 'alert' }
  briefing: { enabled: boolean; time: string; send: boolean }
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

/** A camera's learned routine: 7 rows (Mon-Sun) of 24 hours, in the Guardian computer's local time. */
export interface RoutineReport {
  camera_id: string
  name: string
  mode: Settings['learning']['unusual_activity']
  days_watched: number
  days_needed: number
  observed_minutes: number[][]
  people_minutes: number[][]
  stranger_minutes: number[][]
  /** people_minutes / observed_minutes; null where the camera never watched */
  share: (number | null)[][]
  /** What a detection at that time is judged by: the hour, its neighbours and similar days */
  expected: (number | null)[][]
  /** Enough was watched around that time to judge it */
  confident: boolean[][]
  summary: string[]
  now: { day: number; hour: number; expected: number | null; confident: boolean; unusual: boolean }
  unusual_below: number
}

export type NotificationChannel = 'discord' | 'telegram' | 'ntfy' | 'webhook' | 'email'

/** What the event log says about the briefing's period. Lists are cut short; the counts cover everything. */
export interface BriefingFacts {
  counts: {
    incidents: number
    unknown_people: number
    alerts: number
    insiders: number
    offline: number
    recordings: number
    arming_changes: number
    panics: number
    tests: number
  }
  incidents: { camera: string | null; time: string; peak_level: number }[]
  alerts: { camera: string | null; time: string }[]
  insiders: { name: string; first: string; last: string; cameras: string[] }[]
  offline: { camera: string | null; time: string; back_after_seconds: number | null }[]
  arming: { time: string; armed: boolean; by: 'schedule' | 'hand' }[]
  panics: string[]
  armed_at_start: boolean
  armed_now: boolean
}

export interface Briefing {
  text: string | null
  /** llm: written by the language model and checked against the facts; template: written from the facts */
  source: 'llm' | 'template' | null
  /** Why the template was used, when the model was asked */
  note: string | null
  generated_at: string | null
  period: { start: string; end: string; hours: number } | null
  facts: BriefingFacts | null
  generating: boolean
  enabled: boolean
}

export interface HeatmapSummary {
  hours: number
  /** Points added: each tracked person adds at most two a second */
  total: number
  empty: boolean
  /** By the Guardian computer's hour of the day */
  by_hour: number[]
  busiest_hours: { hour: number; points: number; share: number }[]
}

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
  routine: (id: string) => request<RoutineReport>(`${cam(id)}/routine`),
  resetRoutine: (id: string) => request<{ ok: boolean }>(`${cam(id)}/routine`, { method: 'DELETE' }),
  heatmapUrl: (id: string, hours: number, version: number) => `${cam(id)}/heatmap.jpg?hours=${hours}&v=${version}`,
  heatmapSummary: (id: string, hours: number) => request<HeatmapSummary>(`${cam(id)}/heatmap/summary?hours=${hours}`),
  snapshotUrl: (id: string) => `${cam(id)}/snapshot.jpg`,
  rawSnapshotUrl: (id: string) => `${cam(id)}/snapshot.jpg?raw=1&t=${Date.now()}`,
  streamUrl: (id: string) => socketUrl(`/ws/stream/${encodeURIComponent(id)}`),
  talkUrl: (id: string) => socketUrl(`/ws/talk/${encodeURIComponent(id)}`),
  listenUrl: (id: string) => socketUrl(`/ws/listen/${encodeURIComponent(id)}`),

  events: (filters: EventFilters, limit = 50, offset = 0) =>
    request<EventPage>(`/api/events${query({ ...filters, limit, offset })}`),
  eventSummary: (hours: number) => request<EventSummary>(`/api/events/summary?hours=${hours}`),
  briefing: () => request<Briefing>('/api/briefing'),
  refreshBriefing: () => request<Briefing>('/api/briefing/refresh', json('POST')),
  eventsCsvUrl: (filters: EventFilters) => `/api/events/export.csv${query({ ...filters })}`,
  clearEvents: () => request<{ deleted: number }>('/api/events', { method: 'DELETE' }),
  /** null clears the verdict and undoes what it taught */
  eventFeedback: (id: number, verdict: Verdict | null) =>
    request<{ id: number; feedback: Verdict | null; message: string }>(`/api/events/${id}/feedback`, json('POST', { verdict })),

  recordings: (camera?: string) => request<RecordingList>(`/api/recordings${query({ camera })}`),
  recordingUrl: (file: string, download = false) =>
    `/api/recordings/${encodeURIComponent(file)}${download ? '?download=true' : ''}`,
  thumbnailUrl: (file: string) => `/api/recordings/${encodeURIComponent(file)}/thumbnail`,
  // The file name makes the URL unique per picture: ids start again at 1 after the log is cleared.
  eventSnapshotUrl: (event: SecurityEvent) => `/api/events/${event.id}/snapshot.jpg?v=${encodeURIComponent(event.snapshot ?? '')}`,
  deleteRecording: (file: string) => request<{ ok: boolean }>(`/api/recordings/${encodeURIComponent(file)}`, { method: 'DELETE' }),

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

  learning: () => request<Learning>('/api/learning'),
  deleteSpot: (id: string) => request<{ ok: boolean }>(`/api/learning/spots/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  answerSuggestion: (id: string, accept: boolean) =>
    request<{ spot: string | null; message: string }>(`/api/learning/suggestions/${encodeURIComponent(id)}`, json('POST', { accept })),
  forgetLearning: (cameraId: string) =>
    request<{ spots: number; suggestions: number; photos: number }>(`/api/learning${query({ camera: cameraId })}`, { method: 'DELETE' }),

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
