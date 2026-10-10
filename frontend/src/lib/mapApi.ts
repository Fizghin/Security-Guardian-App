// The property map's part of the backend: floorplan, scale, camera calibration and the live map.
import { json, request, socketUrl } from '../api'

export type Point = [number, number]
/** [picture x, picture y, map x, map y]: picture in 0..1, map in floorplan pixels */
export type Pair = [number, number, number, number]
export type PersonStatus = 'known' | 'unknown' | 'pending'

export interface Calibration {
  /** Root mean square distance, in metres, between where the fit puts the pairs and where they were marked */
  error: number
  errors: number[]
  /** Pairs (0-based) left out because they don't fit the others */
  outliers: number[]
  /** Only 4 pairs were used: they always fit exactly, so the error says nothing */
  exact: boolean
  /** The ground area the camera sees, on the map */
  field: Point[]
  /** The nearest ground in view, where the camera's icon goes */
  at: Point
}

export interface MapCamera {
  id: string
  name: string
  enabled: boolean
  points: Pair[]
  calibration: Calibration | null
  problem: string | null
}

export interface MapInfo {
  /** "" before setup, "grid" for a blank grid, or the floorplan's file name */
  image: string
  width: number
  height: number
  /** null until the scale is set */
  metres_per_px: number | null
  /** [x1, y1, x2, y2, metres], or empty */
  scale_line: number[]
  picture: string | null
  cameras: MapCamera[]
}

export interface MapPerson {
  id: number
  x: number
  y: number
  /** m/s */
  speed: number
  label: string
  status: PersonStatus
  cameras: string[]
  /** What each camera makes of them */
  views: { camera: string; status: PersonStatus; label: string }[]
  /** The camera that recognised their face, if any */
  identified_on: string | null
  /** Server time they appeared on the map */
  since: number
  /** [x, y, server time], oldest first */
  trail: [number, number, number][]
}

export interface MapFrame {
  type: 'map'
  time: number
  people: MapPerson[]
  cameras: { id: string; name: string; field: Point[]; at: Point }[]
}

/** [time, id, x, y, status, label] */
export type HistorySample = [number, number, number, number, PersonStatus, string]

const camera = (id: string) => `/api/map/cameras/${encodeURIComponent(id)}`

export const mapApi = {
  get: () => request<MapInfo>('/api/map'),
  uploadPicture: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<MapInfo>('/api/map/picture', { method: 'POST', body: form })
  },
  useGrid: (widthM: number, heightM: number) => request<MapInfo>('/api/map/grid', json('POST', { width_m: widthM, height_m: heightM })),
  remove: () => request<MapInfo>('/api/map', { method: 'DELETE' }),
  setScale: (line: [number, number, number, number], metres: number) => request<MapInfo>('/api/map/scale', json('POST', { line, metres })),
  check: (cameraId: string, points: Pair[]) => request<Calibration>(`${camera(cameraId)}/check`, json('POST', { points })),
  saveCamera: (cameraId: string, points: Pair[]) => request<MapInfo>(camera(cameraId), json('PUT', { points })),
  history: (minutes: number) => request<{ time: number; step: number; samples: HistorySample[] }>(`/api/map/history?minutes=${minutes}`),
  liveUrl: () => socketUrl('/ws/map'),
}

export const STATUS_COLOR: Record<PersonStatus, string> = {
  known: '#22c55e',
  unknown: '#ef4444',
  pending: '#f59e0b',
}

export const STATUS_TEXT: Record<PersonStatus, string> = {
  known: 'Insider',
  unknown: 'Not recognised',
  pending: 'Being identified',
}
