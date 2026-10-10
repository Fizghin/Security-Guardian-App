import { useEffect, useState } from 'react'
import { api, type CameraStatus } from '../api'
import { usePoll } from '../lib/usePoll'
import { Segmented } from './ui'

const REFRESH_MS = 60000
// The colours the server paints with (OpenCV's turbo map), from the quietest place shown to the busiest
const LEGEND = 'linear-gradient(to right, #4666dd, #1ad4d0, #8bff4b, #f5c53a, #e84b0c, #7a0403)'

const hourLabel = (h: number) => `${String(h).padStart(2, '0')}:00`

/** Covers the live picture with where people stood over the last day or week. */
export default function HeatmapOverlay({ camera }: { camera: CameraStatus }) {
  const [hours, setHours] = useState<24 | 168>(24)
  const [version, setVersion] = useState(() => Date.now())
  const { data: summary } = usePoll(() => api.heatmapSummary(camera.id, hours), REFRESH_MS, [camera.id, hours])

  useEffect(() => {
    const timer = setInterval(() => setVersion(Date.now()), REFRESH_MS)
    return () => clearInterval(timer)
  }, [])

  const busiest = summary?.busiest_hours.slice(0, 2).map((b) => `${hourLabel(b.hour)}–${hourLabel((b.hour + 1) % 24)}`)

  return (
    <div className="absolute inset-0 bg-black" onClick={(e) => e.stopPropagation()}>
      <img src={api.heatmapUrl(camera.id, hours, version)} alt={`Activity heatmap of ${camera.name}`} className="h-full w-full object-contain" />
      {summary?.empty && (
        <div className="absolute inset-0 flex items-center justify-center p-4">
          <p className="rounded-md bg-black/75 px-3 py-2 text-center text-sm text-zinc-200">
            No activity yet
            <span className="block text-xs text-zinc-400">People who stand in view will show here.</span>
          </p>
        </div>
      )}
      {/* At the top: people's feet, and so the heat, are mostly in the lower part of the picture */}
      <div className="absolute inset-x-0 top-9 flex items-start justify-between gap-2 px-2 sm:top-11 sm:px-3">
        <div className="min-w-0 rounded-md bg-black/70 px-2 py-1.5 text-[11px] text-zinc-300">
          <div className="text-xs font-medium text-zinc-100">Where people spent time</div>
          <div className="mt-1 flex items-center gap-1.5">
            Less
            <span className="h-1.5 w-14 rounded-full sm:w-24" style={{ background: LEGEND }} />
            More
          </div>
          {busiest && busiest.length > 0 && <div className="mt-0.5 hidden text-zinc-400 sm:block">Busiest: {busiest.join(', ')}</div>}
        </div>
        <div className="shrink-0 rounded-md bg-black/50">
          <Segmented
            value={hours}
            options={[
              { value: 24, label: '24 h' },
              { value: 168, label: '7 days' },
            ]}
            onChange={setHours}
          />
        </div>
      </div>
    </div>
  )
}
