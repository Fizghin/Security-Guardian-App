import { useState } from 'react'
import { Download, Trash2 } from 'lucide-react'
import { api, type Recording } from '../api'
import { formatBytes, formatDateTime, formatDuration, reasonLabel } from '../lib/format'
import { errorMessage, useToast } from '../lib/toast'
import { Button, ConfirmDialog, Modal } from './ui'

const browserPlaysH264 = () => document.createElement('video').canPlayType('video/mp4; codecs="avc1.42E01E"') !== ''

export default function RecordingPlayer({
  file,
  recording,
  onClose,
  onDeleted,
}: {
  file: string | null
  recording?: Recording
  onClose: () => void
  onDeleted?: () => void
}) {
  const notify = useToast()
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  // Why a clip opened from the event log doesn't load: it is still being recorded, or it is no longer on disk.
  const [missing, setMissing] = useState<'recording' | 'gone' | null>(null)

  if (!file) return null

  const loadFailed = async () => {
    try {
      const list = await api.recordings()
      if (!list.items.some((r) => r.file === file)) setMissing(list.active.some((a) => a.file === file) ? 'recording' : 'gone')
    } catch {
      // server unreachable: the general message is shown
    }
    setFailed(true)
  }

  const remove = async () => {
    setBusy(true)
    try {
      await api.deleteRecording(file)
      notify('Recording deleted', 'success')
      setConfirm(false)
      onDeleted?.()
      onClose()
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(false)
    }
  }

  const encodedWithoutFfmpeg = recording?.playable === false
  const playable = !encodedWithoutFfmpeg && !failed

  return (
    <>
      <Modal
        open
        wide
        onClose={onClose}
        title={recording ? `${reasonLabel(recording.reason)}${recording.camera ? ` at ${recording.camera}` : ''} · ${formatDateTime(recording.started)}` : file}
        footer={
          (recording || onDeleted || !missing) && (
            <>
              {recording && (
                <span className="mr-auto self-center text-xs text-zinc-500">
                  {formatDuration(recording.duration)} · {formatBytes(recording.size)}
                  {recording.max_level ? ` · peak level ${recording.max_level}` : ''}
                </span>
              )}
              {onDeleted && (
                <Button variant="ghost" icon={<Trash2 className="h-4 w-4" />} onClick={() => setConfirm(true)}>
                  Delete
                </Button>
              )}
              {!missing && (
                // download: a failed download must not replace the dashboard with an error page
                <a href={api.recordingUrl(file, true)} download>
                  <Button icon={<Download className="h-4 w-4" />}>Download</Button>
                </a>
              )}
            </>
          )
        }
      >
        {playable ? (
          <video
            key={file}
            src={api.recordingUrl(file)}
            controls
            autoPlay
            className="aspect-video w-full rounded bg-black"
            onError={loadFailed}
          />
        ) : (
          <div className="flex aspect-video w-full flex-col items-center justify-center rounded bg-black p-6 text-center text-sm text-zinc-400">
            <p className="text-zinc-200">
              {missing === 'recording' ? 'This clip is still being recorded.' : missing ? "This clip isn't available." : "This clip can't be played here."}
            </p>
            <p className="mt-1 max-w-md text-xs">
              {missing === 'recording'
                ? 'It can be played here once it is saved, shortly after the incident ends.'
                : missing
                  ? 'It is still being saved, or it was deleted. Try again in a moment.'
                  : encodedWithoutFfmpeg
                    ? 'It was saved without FFmpeg (MPEG-4 Part 2). Download it and open it in a video player, or install FFmpeg so future clips are saved as H.264.'
                    : !browserPlaysH264()
                      ? 'This browser has no H.264 video support. Download the clip, or open the dashboard in Chrome, Edge, Safari or Firefox.'
                      : 'The file could not be loaded. Download it and open it in a video player.'}
            </p>
          </div>
        )}
      </Modal>
      <ConfirmDialog
        open={confirm}
        title="Delete recording?"
        message="The video file is removed from disk. This can't be undone."
        confirmLabel="Delete"
        busy={busy}
        onConfirm={remove}
        onCancel={() => setConfirm(false)}
      />
    </>
  )
}
