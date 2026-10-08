import { Film } from 'lucide-react'
import type { SecurityEvent } from '../api'
import { eventLabel, formatDateTime, formatShortDate, formatTime, isToday, SEVERITY_STYLE } from '../lib/format'
import { Badge } from './ui'

export default function EventRow({
  event,
  onOpenClip,
  compact,
}: {
  event: SecurityEvent
  onOpenClip?: (file: string) => void
  compact?: boolean
}) {
  const time = (
    <time
      className="shrink-0 whitespace-nowrap font-mono text-xs tabular-nums text-zinc-500"
      dateTime={event.timestamp}
      title={formatDateTime(event.timestamp)}
    >
      {isToday(event.timestamp) ? formatTime(event.timestamp) : formatShortDate(event.timestamp)}
    </time>
  )
  const clip = event.recording && onOpenClip && (
    <button
      type="button"
      onClick={() => onOpenClip(event.recording!)}
      className="flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs text-blue-400 hover:bg-zinc-800 hover:text-blue-300"
      title="Play clip"
    >
      <Film className="h-3.5 w-3.5" />
      Clip
    </button>
  )

  if (compact) {
    return (
      <li className="px-4 py-2.5">
        <div className="flex items-center gap-2">
          {time}
          <Badge className={SEVERITY_STYLE[event.severity]}>{eventLabel(event.event_type)}</Badge>
          {event.camera && <span className="truncate text-[11px] text-zinc-500">{event.camera}</span>}
          <span className="ml-auto">{clip}</span>
        </div>
        <p className="mt-1 line-clamp-2 text-zinc-300">{event.description}</p>
      </li>
    )
  }

  return (
    <li className="flex flex-col gap-1 px-4 py-2 sm:flex-row sm:items-baseline sm:gap-3">
      <div className="flex shrink-0 items-baseline gap-3 sm:w-80">
        <span className="sm:w-[6.5rem]">{time}</span>
        <Badge className={SEVERITY_STYLE[event.severity]}>{eventLabel(event.event_type)}</Badge>
        {event.camera && <span className="truncate text-xs text-zinc-500">{event.camera}</span>}
      </div>
      <p className="min-w-0 flex-1 text-zinc-300">{event.description}</p>
      {clip}
    </li>
  )
}
