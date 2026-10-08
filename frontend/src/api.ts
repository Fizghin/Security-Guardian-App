// Typed client for the Guardian backend. All URLs are relative: the dashboard is served by
// the backend in production and proxied by Vite in development.

export type Severity = 'INFO' | 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
export const SEVERITIES: Severity[] = ['INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL']

export interface CameraStatus {
  name: string
  source: string
  kind: 'none' | 'auto' | 'index' | 'url' | 'file'
  active_index: number | null
  connected: boolean
  error: string | null
  width: number
  height: number
  fps: number
}

export interface Status {
  armed: boolean
  threat_level: number
  threat_label: string
  manual_alarm: boolean
  test: boolean
  incident_started: number | null
  incident_seconds: number
  persons: number
  insiders_in_view: string[]
  last_message: string | null
  last_message_time: number | null
  last_message_source: 'llm' | 'fallback' | 'operator' | null
  camera: CameraStatus
  pipeline: { running: boolean; fps: number; motion: boolean; test_seconds_left: number; error: string | null }
  detector: { loaded: boolean; model: string; device: string | null; inference_ms: number | null; error: string | null }
  recording: { active: boolean; file: string | null; started: number | null; stopping: boolean; encoder: string }
  siren: { active: boolean; available: boolean; player: string | null; started_at: number | null }
  voice: { available: boolean | null; engine: string | null; error: string | null; speaking: boolean; queued: number }
  ai: {
    provider: string
    base_url: string
    model: string | null
    busy: boolean
    last_source: string | null
    last_error: string | null
    last_latency_ms: number | null
  }
  faces: FaceStatus
  server_time: number
}

export interface FaceStatus {
  state: 'idle' | 'downloading' | 'ready' | 'error'
  error: string | null
  enrolled: number
}

export interface SecurityEvent {
  id: number
  timestamp: string
  event_type: string
  description: string
  severity: Severity
  recording: string | null
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
}

export interface EventFilters {
  type?: string
  severity?: string
  since?: string
  search?: string
}

export interface Recording {
  file: string
  size: number
  started: string
  duration: number | null
  reason: string
  max_level: number | null
  playable: boolean
  thumbnail: boolean
}

export interface RecordingList {
  items: Recording[]
  usage_bytes: number
  recording: Status['recording']
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
}

export interface Settings {
  armed: boolean
  camera: { source: string; name: string }
  detection: {
    confidence: number
    min_person_height: number
    interval_ms: number
    face_recognition: boolean
    face_match_threshold: number
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
  }
}

export type SettingsPatch = {
  [K in keyof Settings]?: Settings[K] extends object ? Partial<Settings[K]> & { api_key?: string } : Settings[K]
}

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
  pipeline: Status['pipeline']
  recording_encoder: string
  recordings_bytes: number
  voice: Status['voice']
  siren: Status['siren']
  faces: FaceStatus
  notifications: { discord: boolean; email: boolean; email_to: string | null }
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

export const api = {
  status: () => request<Status>('/api/status'),
  arm: (armed: boolean) => request<{ armed: boolean }>('/api/arm', json('POST', { armed })),
  panic: () => request<{ ok: boolean }>('/api/panic', json('POST')),
  resetAlarm: () => request<{ ok: boolean; was_active: boolean }>('/api/alarm/reset', json('POST')),
  testIntrusion: (seconds: number) => request<{ ok: boolean; seconds: number }>('/api/test-intrusion', json('POST', { seconds })),
  speak: (text: string) => request<{ ok: boolean }>('/api/speak', json('POST', { text })),

  events: (filters: EventFilters, limit = 50, offset = 0) =>
    request<EventPage>(`/api/events${query({ ...filters, limit, offset })}`),
  eventSummary: (hours: number) => request<EventSummary>(`/api/events/summary?hours=${hours}`),
  eventsCsvUrl: (filters: EventFilters) => `/api/events/export.csv${query({ ...filters })}`,
  clearEvents: () => request<{ deleted: number }>('/api/events', { method: 'DELETE' }),

  recordings: () => request<RecordingList>('/api/recordings'),
  recordingUrl: (file: string, download = false) =>
    `/api/recordings/${encodeURIComponent(file)}${download ? '?download=true' : ''}`,
  thumbnailUrl: (file: string) => `/api/recordings/${encodeURIComponent(file)}/thumbnail`,
  deleteRecording: (file: string) => request<{ ok: boolean }>(`/api/recordings/${encodeURIComponent(file)}`, { method: 'DELETE' }),

  insiders: () => request<InsiderList>('/api/insiders'),
  addInsiderPhotos: (name: string, files: File[]) => {
    const form = new FormData()
    form.append('name', name)
    files.forEach((f) => form.append('files', f))
    return request<{ results: UploadResult[] }>('/api/insiders', { method: 'POST', body: form })
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
  scanCameras: () => request<{ cameras: { index: number; width: number; height: number; in_use?: boolean }[] }>('/api/cameras/scan'),

  system: () => request<SystemInfo>('/api/system'),
  testNotifications: () =>
    request<{ results: { channel: string; ok: boolean; error: string | null }[] }>('/api/notifications/test', json('POST')),

  snapshotUrl: () => '/api/snapshot.jpg',
  streamUrl: () => `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/stream`,
}
