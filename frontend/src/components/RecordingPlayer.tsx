import { useEffect, useState } from 'react'
import { Download, FileText, Lock, LockOpen, Trash2 } from 'lucide-react'
import { api, type IncidentReport, type Recording } from '../api'
import { eventLabel, formatBytes, formatDateTime, formatDuration, reasonLabel, SEVERITY_COLOR } from '../lib/format'
import { errorMessage, useToast } from '../lib/toast'
import IntegrityBadge from './IntegrityBadge'
import { Button, ConfirmDialog, Modal } from './ui'

/** What happened during the clip, and whether the file is still exactly as it was saved. */
function IncidentPanel({ file }: { file: string }) {
  const [report, setReport] = useState<IncidentReport | null>(null)
  useEffect(() => {
    let live = true
    api.incidentReport(file).then((r) => live && setReport(r)).catch(() => {})
    return () => {
      live = false
    }
  }, [file])
  if (!report) return null
  const shown = report.timeline.filter((e) => !['RECORDING', 'NOTIFICATION'].includes(e.event_type)).slice(0, 12)
  return (
    <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div>
        <div className="mb-2 flex items-center gap-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">Summary</h3>
          <IntegrityBadge result={report.integrity} />
        </div>
        <p className="text-sm leading-relaxed text-zinc-300">{report.summary}</p>
        {report.integrity.sha256 && (
          <p className="mt-2 break-all font-mono text-[11px] text-zinc-500" title="SHA-256 recorded when the clip was sealed">
            SHA-256 {report.integrity.sha256}
          </p>
        )}
      </div>
      {shown.length > 0 && (
        <div>
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">Timeline</h3>
          <ol className="relative space-y-2 border-l border-zinc-800 pl-4">
            {shown.map((e) => (
              <li key={e.id} className="relative text-sm">
                <span className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full ring-2 ring-zinc-900" style={{ background: SEVERITY_COLOR[e.severity] }} />
                <span className="font-mono text-xs tabular-nums text-zinc-500">{e.local_time}</span>{' '}
                <span className="font-medium text-zinc-200">{eventLabel(e.event_type)}</span>
                <span className="block truncate text-xs text-zinc-500" title={e.description}>
                  {e.description}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </div>
  )
}

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
  const [isProtected, setIsProtected] = useState(!!recording?.protected)
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

  const toggleProtect = async () => {
    setBusy(true)
    try {
      const { protected: kept } = await api.protectRecording(file, !isProtected)
      setIsProtected(kept)
      notify(kept ? 'Clip protected: it is kept forever' : 'Protection removed', 'success')
      onDeleted?.()
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
                <Button
                  variant="ghost"
                  icon={<Trash2 className="h-4 w-4" />}
                  disabled={isProtected}
                  title={isProtected ? 'Protected clips cannot be deleted' : undefined}
                  onClick={() => setConfirm(true)}
                >
                  Delete
                </Button>
              )}
              {recording && !missing && (
                <Button
                  variant={isProtected ? 'secondary' : 'ghost'}
                  icon={isProtected ? <Lock className="h-4 w-4 text-emerald-400" /> : <LockOpen className="h-4 w-4" />}
                  loading={busy && !confirm}
                  onClick={toggleProtect}
                  title={isProtected ? 'Kept forever. Click to let retention delete it again.' : 'Keep this clip forever: retention skips it and it cannot be deleted'}
                >
                  {isProtected ? 'Protected' : 'Keep forever'}
                </Button>
              )}
              {!missing && (
                <a href={api.incidentReportUrl(file)} target="_blank" rel="noreferrer">
                  <Button icon={<FileText className="h-4 w-4" />}>Incident report</Button>
                </a>
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
            className="aspect-video w-full rounded-xl bg-black"
            onError={loadFailed}
          />
        ) : (
          <div className="force-dark flex aspect-video w-full flex-col items-center justify-center rounded-xl bg-black p-6 text-center text-sm text-zinc-400">
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
        {!missing && <IncidentPanel file={file} />}
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
