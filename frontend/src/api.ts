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

export interface CameraStatus {
  id: string
  name: string
  source: string
  kind: SourceKind
  audio: AudioOutput
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
  schedule: ScheduleStatus
  server_time: number
}

export interface ScheduleStatus {
  enabled: boolean
  /** Whether the schedule wants Guardian armed right now (null when it is off). */
  active: boolean | null
  next_change: string | null
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
}

export interface SecurityEvent {
  id: number
  timestamp: string
  event_type: string
  description: string
  severity: Severity
  recording: string | null
  camera: string | null
  /** A picture of the moment is available from eventSnapshotUrl. */
  snapshot: boolean
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
  }
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

export const api = {
  status: () => request<Status>('/api/status'),
  arm: (armed: boolean) => request<{ armed: boolean }>('/api/arm', json('POST', { armed })),
  panic: () => request<{ ok: boolean }>('/api/panic', json('POST')),
  resetAlarm: () => request<{ ok: boolean; was_active: boolean }>('/api/alarm/reset', json('POST')),
  speak: (text: string, cameraId?: string) => request<{ ok: boolean }>('/api/speak', json('POST', { text, camera_id: cameraId })),

  cameras: () => request<{ cameras: CameraConfig[]; phone: SystemInfo['phone'] }>('/api/cameras'),
  addCamera: (body: { name: string; source: string; audio?: AudioOutput }) =>
    request<CameraConfig>('/api/cameras', json('POST', body)),
  updateCamera: (id: string, body: Partial<Pick<CameraConfig, 'name' | 'source' | 'enabled' | 'audio' | 'zones'>>) =>
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
  streamUrl: (id: string) =>
    `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/stream/${encodeURIComponent(id)}`,

  events: (filters: EventFilters, limit = 50, offset = 0) =>
    request<EventPage>(`/api/events${query({ ...filters, limit, offset })}`),
  eventSummary: (hours: number) => request<EventSummary>(`/api/events/summary?hours=${hours}`),
  eventsCsvUrl: (filters: EventFilters) => `/api/events/export.csv${query({ ...filters })}`,
  clearEvents: () => request<{ deleted: number }>('/api/events', { method: 'DELETE' }),

  recordings: (camera?: string) => request<RecordingList>(`/api/recordings${query({ camera })}`),
  recordingUrl: (file: string, download = false) =>
    `/api/recordings/${encodeURIComponent(file)}${download ? '?download=true' : ''}`,
  thumbnailUrl: (file: string) => `/api/recordings/${encodeURIComponent(file)}/thumbnail`,
  eventSnapshotUrl: (id: number) => `/api/events/${id}/snapshot.jpg`,
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

  settings: () => request<Settings>('/api/settings'),
  updateSettings: (patch: SettingsPatch) => request<Settings>('/api/settings', json('PATCH', patch)),
  aiModels: (provider: string, baseUrl: string) =>
    request<{ models: string[]; error: string | null }>(`/api/ai/models${query({ provider, base_url: baseUrl })}`),
  aiTest: (level: number, speak: boolean) => request<AITestResult>('/api/ai/test', json('POST', { level, speak })),

  system: () => request<SystemInfo>('/api/system'),
  telegramChats: (token = '') =>
    request<{ chats: { id: string; name: string; type: string }[] }>(`/api/notifications/telegram/chats?token=${encodeURIComponent(token)}`),
  testNotifications: () =>
    request<{ results: { channel: string; ok: boolean; error: string | null }[] }>('/api/notifications/test', json('POST')),
}
