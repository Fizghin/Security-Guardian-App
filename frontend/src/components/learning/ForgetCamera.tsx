import { useState } from 'react'
import { Eraser } from 'lucide-react'
import { api, type Learning } from '../../api'
import { useStatus } from '../../lib/status'
import { errorMessage, useToast } from '../../lib/toast'
import { Button, Card, ConfirmDialog } from '../ui'

const count = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

/** Forgets the spots, suggestions and learned face photos of one camera. */
export default function ForgetCamera({ data, onChanged }: { data: Learning; onChanged: () => void }) {
  const notify = useToast()
  const { status } = useStatus()
  const cameras = new Map<string, string>()
  for (const c of status?.cameras ?? []) cameras.set(c.id, c.name)
  for (const item of [...data.spots, ...data.suggestions]) if (!cameras.has(item.camera_id)) cameras.set(item.camera_id, item.camera ?? 'Removed camera')
  const [chosen, setChosen] = useState('')
  const [confirm, setConfirm] = useState(false)
  const [busy, setBusy] = useState(false)
  const cameraId = cameras.has(chosen) ? chosen : ([...cameras.keys()][0] ?? '')
  const name = cameras.get(cameraId) ?? ''

  const forget = async () => {
    setBusy(true)
    try {
      const r = await api.forgetLearning(cameraId)
      notify(
        r.spots + r.suggestions + r.photos === 0
          ? `Guardian had learned nothing on ${name}.`
          : `Forgot ${count(r.spots, 'ignored spot')}, ${count(r.suggestions, 'suggestion')} and ${count(r.photos, 'learned face photo')} from ${name}.`,
        'success',
      )
      setConfirm(false)
      onChanged()
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card title="Forget what was learned on a camera">
      <p className="mb-3 text-sm text-zinc-400">
        Removes the camera’s ignored spots and suggestions, and the face photos Guardian learned from it. Photos you added
        and your answers on events are kept.
      </p>
      {cameras.size === 0 ? (
        <p className="text-sm text-zinc-500">No cameras.</p>
      ) : (
        <div className="flex gap-2">
          <select className="input" value={cameraId} onChange={(e) => setChosen(e.target.value)} aria-label="Camera">
            {[...cameras].map(([id, cameraName]) => (
              <option key={id} value={id}>
                {cameraName}
              </option>
            ))}
          </select>
          <Button icon={<Eraser className="h-4 w-4" />} onClick={() => setConfirm(true)}>
            Forget
          </Button>
        </div>
      )}
      <ConfirmDialog
        open={confirm}
        title={`Forget what was learned on ${name}?`}
        message="Its ignored spots and suggestions are deleted, and so are the face photos Guardian learned from this camera. This can't be undone."
        confirmLabel="Forget"
        busy={busy}
        onConfirm={forget}
        onCancel={() => setConfirm(false)}
      />
    </Card>
  )
}
