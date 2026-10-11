import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Megaphone, Mic } from 'lucide-react'
import { api, type CameraStatus, type ConversationEntry } from '../api'
import { cx } from '../lib/cx'
import { errorMessage, useToast } from '../lib/toast'
import { Button, Card } from './ui'

const WHO: Record<ConversationEntry['who'], string> = { person: 'Person', guardian: 'Guardian', owner: 'You (typed)' }

const clock = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })

/** The Guard Bot's conversation with the person on a camera during an intrusion, with the owner's override. */
export default function Conversation({ camera }: { camera: CameraStatus }) {
  const notify = useToast()
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const list = useRef<HTMLUListElement>(null)
  const bot = camera.guard_bot
  const count = bot.entries.length

  useEffect(() => {
    if (list.current) list.current.scrollTop = list.current.scrollHeight
  }, [count])

  const say = async (e: FormEvent) => {
    e.preventDefault()
    if (!text.trim()) return
    setBusy(true)
    try {
      await api.speak(text.trim(), camera.id)
      setText('')
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card
      title={`Conversation · ${camera.name}`}
      actions={
        bot.listening ? (
          <span className="flex items-center gap-1.5 text-xs text-emerald-400">
            <Mic className="h-3.5 w-3.5" />
            {bot.thinking ? 'Writing a reply…' : 'Listening'}
          </span>
        ) : (
          <span className="text-xs text-zinc-500">Not listening</span>
        )
      }
    >
      {bot.note && <p className="mb-3 text-sm text-amber-400">{bot.note}</p>}
      {count === 0 ? (
        <p className="text-sm text-zinc-500">Nothing said yet.</p>
      ) : (
        <ul ref={list} className="max-h-72 space-y-2 overflow-y-auto pr-1" aria-live="polite">
          {bot.entries.map((entry, i) => (
            <li key={`${entry.time}-${i}`} className={cx('flex', entry.who === 'person' ? 'justify-start' : 'justify-end')}>
              <div
                className={cx(
                  'max-w-[85%] rounded-lg px-3 py-2 text-sm',
                  entry.who === 'person' ? 'bg-zinc-800 text-zinc-100' : entry.who === 'owner' ? 'bg-emerald-500/15 text-emerald-100' : 'bg-blue-500/15 text-blue-100',
                )}
              >
                <div className="mb-0.5 text-[11px] text-zinc-400">
                  {WHO[entry.who]} · {clock.format(new Date(entry.time * 1000))}
                </div>
                {entry.text}
              </div>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={say} className="mt-3 flex gap-2">
        <input
          className="input"
          maxLength={300}
          placeholder="Say instead…"
          aria-label={`Say something through ${camera.name} instead`}
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <Button type="submit" icon={<Megaphone className="h-4 w-4" />} loading={busy} disabled={!text.trim() || camera.audio.talking}>
          Say
        </Button>
      </form>
      <p className="hint">Guardian answers in one short sentence, checked against what is true right now. Typing here speaks your words instead.</p>
    </Card>
  )
}
