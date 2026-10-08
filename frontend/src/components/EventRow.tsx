import { useState } from 'react'
import { Film } from 'lucide-react'
import { api, type SecurityEvent } from '../api'
import { eventLabel, formatDateTime, formatShortDate, formatTime, isToday, SEVERITY_STYLE } from '../lib/format'
import { cx } from '../lib/cx'
import { Badge, Button, Modal } from './ui'

export default function EventRow({
  event,
  onOpenClip,
  compact,
}: {
  event: SecurityEvent
  onOpenClip?: (file: string) => void
  compact?: boolean
}) {
  const [viewing, setViewing] = useState(false)
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
  const picture = event.snapshot && (
    <button
      type="button"
      onClick={() => setViewing(true)}
      className={cx('shrink-0 overflow-hidden rounded border border-zinc-800 bg-black hover:border-zinc-500', compact ? 'h-9 w-16' : 'h-10 w-[4.5rem]')}
      title="Show picture"
    >
      <img src={api.eventSnapshotUrl(event.id)} alt="" loading="lazy" className="h-full w-full object-cover" />
    </button>
  )
  const lightbox = event.snapshot && (
    <Modal
      open={viewing}
      onClose={() => setViewing(false)}
      wide
      title={`${eventLabel(event.event_type)}${event.camera ? ` · ${event.camera}` : ''} · ${formatDateTime(event.timestamp)}`}
      footer={
        event.recording && onOpenClip ? (
          <Button
            icon={<Film className="h-4 w-4" />}
            onClick={() => {
              setViewing(false)
              onOpenClip(event.recording!)
            }}
          >
            Play clip
          </Button>
        ) : undefined
      }
    >
      <img src={api.eventSnapshotUrl(event.id)} alt={event.description} className="mx-auto max-h-[70vh] w-auto rounded" />
      <p className="mt-3 text-sm text-zinc-300">{event.description}</p>
    </Modal>
  )

  if (compact) {
    return (
      <li className="flex gap-3 px-4 py-2.5">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            {time}
            <Badge className={SEVERITY_STYLE[event.severity]}>{eventLabel(event.event_type)}</Badge>
            {event.camera && <span className="truncate text-[11px] text-zinc-500">{event.camera}</span>}
            <span className="ml-auto">{clip}</span>
          </div>
          <p className="mt-1 line-clamp-2 text-zinc-300">{event.description}</p>
        </div>
        {picture}
        {lightbox}
      </li>
    )
  }

  return (
    <li className="flex flex-col gap-1 px-4 py-2 sm:flex-row sm:items-center sm:gap-3">
      <div className="flex shrink-0 items-baseline gap-3 sm:w-80">
        <span className="sm:w-[6.5rem]">{time}</span>
        <Badge className={SEVERITY_STYLE[event.severity]}>{eventLabel(event.event_type)}</Badge>
        {event.camera && <span className="truncate text-xs text-zinc-500">{event.camera}</span>}
      </div>
      <p className="min-w-0 flex-1 text-zinc-300">{event.description}</p>
      <div className="flex items-center gap-2 empty:hidden">
        {clip}
        {picture}
      </div>
      {lightbox}
    </li>
  )
}
