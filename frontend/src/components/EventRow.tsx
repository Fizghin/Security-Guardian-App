import { Film } from 'lucide-react'
import { api, type SecurityEvent } from '../api'
import { eventLabel, formatDateTime, formatShortDate, formatTime, isToday, SEVERITY_STYLE } from '../lib/format'
import { cx } from '../lib/cx'
import { useStatus } from '../lib/status'
import EventFeedback from './EventFeedback'
import { Badge, Button, Modal } from './ui'

/** A detection at an hour when the camera usually sees nobody; the server words it so. */
const isUnusual = (event: SecurityEvent) => event.event_type === 'DETECTION' && event.description.includes('. Unusual: ')

const UNUSUAL = 'bg-fuchsia-500/10 text-fuchsia-300 ring-fuchsia-500/30'

/** A clip that is still being recorded can only be played once it is saved. */
function useStillRecording(file: string | null) {
  const { status } = useStatus()
  return !!file && !!status?.cameras.some((c) => c.recording.active && c.recording.file === file)
}

export default function EventRow({
  event,
  onOpenClip,
  onOpenPicture,
  compact,
}: {
  event: SecurityEvent
  onOpenClip?: (file: string) => void
  onOpenPicture?: (event: SecurityEvent) => void
  compact?: boolean
}) {
  const stillRecording = useStillRecording(event.recording)
  const unusual = isUnusual(event) && <Badge className={UNUSUAL}>Unusual</Badge>
  const time = (
    <time
      className="shrink-0 whitespace-nowrap font-mono text-xs tabular-nums text-zinc-500"
      dateTime={event.timestamp}
      title={formatDateTime(event.timestamp)}
    >
      {isToday(event.timestamp) ? formatTime(event.timestamp) : formatShortDate(event.timestamp)}
    </time>
  )
  const clip =
    event.recording &&
    onOpenClip &&
    (stillRecording ? (
      <span className="flex shrink-0 items-center gap-1.5 px-1.5 py-0.5 text-xs text-red-400" title="Still recording. The clip can be played once it is saved.">
        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-red-500" />
        {compact ? 'Rec' : 'Recording'}
      </span>
    ) : (
      <button
        type="button"
        onClick={() => onOpenClip(event.recording!)}
        className="flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-xs text-blue-400 hover:bg-zinc-800 hover:text-blue-300"
        title="Play clip"
      >
        <Film className="h-3.5 w-3.5" />
        Clip
      </button>
    ))
  const picture = event.snapshot && onOpenPicture && (
    <button
      type="button"
      onClick={() => onOpenPicture(event)}
      className={cx('shrink-0 overflow-hidden rounded border border-zinc-800 bg-black hover:border-zinc-500', compact ? 'h-9 w-16' : 'h-10 w-[4.5rem]')}
      title="Show picture"
    >
      <img src={api.eventSnapshotUrl(event)} alt="" loading="lazy" className="h-full w-full object-cover" />
    </button>
  )

  if (compact) {
    return (
      <li className="flex gap-3 px-4 py-2.5">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            {time}
            <Badge className={SEVERITY_STYLE[event.severity]}>{eventLabel(event.event_type)}</Badge>
            {unusual}
            {event.camera && <span className="truncate text-[11px] text-zinc-500">{event.camera}</span>}
          </div>
          <div className="mt-1 flex items-start gap-2">
            <p className="line-clamp-2 min-w-0 flex-1 text-zinc-300">{event.description}</p>
            {clip}
          </div>
          <EventFeedback event={event} className="-ml-2 mt-1" />
        </div>
        {picture}
      </li>
    )
  }

  return (
    <li className="flex flex-col gap-1 px-4 py-2 sm:flex-row sm:items-center sm:gap-3">
      <div className="flex shrink-0 items-baseline gap-3 sm:w-80">
        <span className="sm:w-[6.5rem]">{time}</span>
        <Badge className={SEVERITY_STYLE[event.severity]}>{eventLabel(event.event_type)}</Badge>
        {unusual}
        {event.camera && <span className="truncate text-xs text-zinc-500">{event.camera}</span>}
      </div>
      <p className="min-w-0 flex-1 text-zinc-300">{event.description}</p>
      <div className="flex items-center gap-2 empty:hidden">
        <EventFeedback event={event} className="-ml-2 sm:ml-0" />
        {clip}
        {picture}
      </div>
    </li>
  )
}

/**
 * The larger view of an event's picture. Pages keep the event being viewed in their own state, so
 * the view stays open when newer events push its row out of the polled list.
 */
export function EventPicture({
  event,
  onClose,
  onOpenClip,
}: {
  event: SecurityEvent | null
  onClose: () => void
  onOpenClip?: (file: string) => void
}) {
  const stillRecording = useStillRecording(event?.recording ?? null)
  if (!event?.snapshot) return null
  const clip = event.recording
  return (
    <Modal
      open
      onClose={onClose}
      wide
      title={`${eventLabel(event.event_type)}${event.camera ? ` · ${event.camera}` : ''} · ${formatDateTime(event.timestamp)}`}
      footer={
        clip && onOpenClip ? (
          stillRecording ? (
            <span className="self-center text-xs text-zinc-400">Still recording. The clip can be played once it is saved.</span>
          ) : (
            <Button
              icon={<Film className="h-4 w-4" />}
              onClick={() => {
                onClose()
                onOpenClip(clip)
              }}
            >
              Play clip
            </Button>
          )
        ) : undefined
      }
    >
      <img src={api.eventSnapshotUrl(event)} alt={event.description} className="mx-auto max-h-[70vh] w-auto rounded" />
      <p className="mt-3 text-sm text-zinc-300">{event.description}</p>
      <EventFeedback event={event} className="-ml-2 mt-2" />
    </Modal>
  )
}
