import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Download, Plus, RefreshCw, Send, Sparkles, Trash2 } from 'lucide-react'
import {
  api,
  type AITestResult,
  type AudioModel,
  type AudioModels,
  type NotificationChannel,
  type NotificationResult,
  type ScheduleRule,
  type Settings,
  type SettingsPatch,
  type SoundAction,
  type SoundGroup,
  type Status,
} from '../api'
import CamerasSection from '../components/CamerasSection'
import { Button, Card, Dot, ErrorNote, Field, Slider, Toggle } from '../components/ui'
import VaultPanel from '../components/VaultPanel'
import { cx } from '../lib/cx'
import { describeSchedule, formatBytes, formatUptime } from '../lib/format'
import { href } from '../lib/route'
import { mapApi, type MapInfo } from '../lib/mapApi'
import { useStatus } from '../lib/status'
import { errorMessage, useToast } from '../lib/toast'
import { usePoll } from '../lib/usePoll'

type Save = (patch: SettingsPatch) => Promise<boolean>

const OLLAMA_URL = 'http://localhost:11434'
const OPENAI_COMPAT_URL = 'http://localhost:1234/v1'

/** Local editable copy of one settings group; `changes` holds only fields that differ. */
function useDraft<T extends object>(value: T) {
  const [draft, setDraft] = useState(value)
  const set = <K extends keyof T>(key: K, v: T[K]) => setDraft((d) => ({ ...d, [key]: v }))
  const changes = Object.fromEntries(
    (Object.keys(draft) as (keyof T)[]).filter((k) => draft[k] !== value[k]).map((k) => [k, draft[k]]),
  ) as Partial<T>
  return { draft, set, changes, dirty: Object.keys(changes).length > 0, reset: () => setDraft(value) }
}

function Section({
  id,
  title,
  description,
  children,
  dirty,
  canSave = true,
  onSave,
  onReset,
  extraActions,
}: {
  id: string
  title: string
  description?: ReactNode
  children: ReactNode
  dirty?: boolean
  /** false while the changes can't be saved yet; Discard still shows */
  canSave?: boolean
  onSave?: () => Promise<unknown>
  onReset?: () => void
  extraActions?: ReactNode
}) {
  const [saving, setSaving] = useState(false)
  return (
    <Card id={id} title={title} className="scroll-mt-4">
      {description && <p className="-mt-1 mb-4 text-sm text-zinc-400">{description}</p>}
      <div className="space-y-4">{children}</div>
      {(onSave || extraActions) && (
        <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-zinc-800 pt-4">
          {extraActions}
          {onSave && (
            <div className="ml-auto flex gap-2">
              {dirty && (
                <Button variant="ghost" onClick={onReset}>
                  Discard
                </Button>
              )}
              <Button
                variant="primary"
                disabled={!dirty || !canSave}
                loading={saving}
                onClick={async () => {
                  setSaving(true)
                  await onSave()
                  setSaving(false)
                }}
              >
                Save
              </Button>
            </div>
          )}
        </div>
      )}
    </Card>
  )
}

function NumberInput({ value, onChange, min, max, suffix }: { value: number; onChange: (v: number) => void; min?: number; max?: number; suffix?: string }) {
  return (
    <div className="relative">
      <input type="number" className={cx('input tabular-nums', suffix && 'pr-12')} value={value} min={min} max={max} onChange={(e) => onChange(Number(e.target.value))} />
      {suffix && <span className="pointer-events-none absolute inset-y-0 right-3 flex items-center text-xs text-zinc-500">{suffix}</span>}
    </div>
  )
}

function LevelSelect({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  const names = ['', 'Level 1 · Person detected', 'Level 2 · Loitering', 'Level 3 · Intruder', 'Level 4 · Alarm']
  return (
    <select className="input" value={value} onChange={(e) => onChange(Number(e.target.value))}>
      {[1, 2, 3, 4].map((n) => (
        <option key={n} value={n}>
          {names[n]}
        </option>
      ))}
    </select>
  )
}

// ---- Language model -------------------------------------------------------------------
const modelFields = (ai: Settings['ai']) => ({
  provider: ai.provider,
  base_url: ai.base_url,
  model: ai.model,
  timeout_seconds: ai.timeout_seconds,
})

const voiceFields = (ai: Settings['ai']) => ({
  voice_enabled: ai.voice_enabled,
  voice_rate: ai.voice_rate,
  intimidation: ai.intimidation,
  humor: ai.humor,
  persistence: ai.persistence,
  greet_insiders: ai.greet_insiders,
  greet_cooldown_minutes: ai.greet_cooldown_minutes,
})

function ModelSection({ value, save }: { value: Settings['ai']; save: Save }) {
  const notify = useToast()
  const d = useDraft(modelFields(value))
  const [apiKey, setApiKey] = useState('')
  const [models, setModels] = useState<string[]>([])
  const [modelError, setModelError] = useState<string | null>(null)
  const [loadingModels, setLoadingModels] = useState(false)
  const [testLevel, setTestLevel] = useState(1)
  const [speakTest, setSpeakTest] = useState(false)
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState<AITestResult | null>(null)

  const { provider, base_url } = d.draft
  const loadModels = useCallback(async () => {
    setLoadingModels(true)
    try {
      const r = await api.aiModels(provider, base_url)
      setModels(r.models)
      setModelError(r.error)
    } catch (err) {
      setModelError(errorMessage(err))
    } finally {
      setLoadingModels(false)
    }
    // base_url is read at call time; refetching on every keystroke would spam the server
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [provider])

  useEffect(() => {
    loadModels()
  }, [loadModels])

  const changeProvider = (p: 'ollama' | 'openai') => {
    d.set('provider', p)
    if ([OLLAMA_URL, OPENAI_COMPAT_URL].includes(d.draft.base_url)) d.set('base_url', p === 'ollama' ? OLLAMA_URL : OPENAI_COMPAT_URL)
    d.set('model', '')
  }

  const runTest = async () => {
    setTesting(true)
    setResult(null)
    try {
      setResult(await api.aiTest(testLevel, speakTest))
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setTesting(false)
    }
  }

  const modelOptions = d.draft.model && !models.includes(d.draft.model) ? [...models, d.draft.model] : models
  const dirty = d.dirty || apiKey !== ''

  return (
    <Section
      id="ai"
      title="Language model"
      description="Writes the spoken warnings. It runs on this computer through Ollama or any OpenAI-compatible local server; nothing is sent to the cloud."
      dirty={dirty}
      onReset={() => {
        d.reset()
        setApiKey('')
      }}
      onSave={async () => {
        const changes = { ...d.changes, ...(apiKey ? { api_key: apiKey } : {}) }
        if (await save({ ai: changes })) setApiKey('')
      }}
      extraActions={
        <div className="flex flex-wrap items-center gap-2">
          <select className="input h-9 w-auto py-1" value={testLevel} onChange={(e) => setTestLevel(Number(e.target.value))} aria-label="Test level">
            {[1, 2, 3, 4].map((n) => (
              <option key={n} value={n}>
                Level {n}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-1.5 text-xs text-zinc-400">
            <input type="checkbox" checked={speakTest} onChange={(e) => setSpeakTest(e.target.checked)} className="accent-blue-500" />
            Speak it
          </label>
          <Button icon={<Sparkles className="h-4 w-4" />} loading={testing} onClick={runTest} title="Uses the saved settings">
            Test warning
          </Button>
        </div>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Server type">
          <select className="input" value={provider} onChange={(e) => changeProvider(e.target.value as 'ollama' | 'openai')}>
            <option value="ollama">Ollama</option>
            <option value="openai">OpenAI-compatible (LM Studio, llama.cpp, vLLM…)</option>
          </select>
        </Field>
        <Field label="Server URL">
          <input className="input font-mono" value={base_url} onChange={(e) => d.set('base_url', e.target.value)} onBlur={loadModels} />
        </Field>
        <Field label="Model" hint={loadingModels ? 'Checking the server…' : `${models.length} installed`}>
          <div className="flex gap-2">
            <select className="input" value={d.draft.model} onChange={(e) => d.set('model', e.target.value)}>
              <option value="">Automatic (first installed model)</option>
              {modelOptions.map((m) => (
                <option key={m} value={m}>
                  {m}
                  {!models.includes(m) ? ' (not found on server)' : ''}
                </option>
              ))}
            </select>
            <Button icon={<RefreshCw className="h-4 w-4" />} onClick={loadModels} loading={loadingModels} aria-label="Refresh models" title="Refresh models" />
          </div>
        </Field>
        <Field label="Time limit" hint="After this, a pre-written line is spoken instead">
          <NumberInput value={d.draft.timeout_seconds} min={5} max={180} suffix="sec" onChange={(v) => d.set('timeout_seconds', v)} />
        </Field>
        {provider === 'openai' && (
          <Field label="API key" hint="Only if your server requires one">
            <input
              type="password"
              className="input"
              value={apiKey}
              autoComplete="off"
              placeholder={value.api_key_set ? 'Saved; type to replace' : 'Not set'}
              onChange={(e) => setApiKey(e.target.value)}
            />
          </Field>
        )}
      </div>
      {modelError && (
        <ErrorNote>
          {modelError}
          {provider === 'ollama' && (
            <span className="mt-1 block text-xs text-red-300/80">
              Install Ollama from ollama.com, then run <span className="font-mono">ollama pull llama3.2:3b</span> (or{' '}
              <span className="font-mono">llama3.2:1b</span> on slower computers).
            </span>
          )}
        </ErrorNote>
      )}
      {result && (
        <div className="rounded-md border border-zinc-800 bg-zinc-950/60 p-3">
          <p className="text-sm text-zinc-100">“{result.text}”</p>
          <p className="mt-1 text-xs text-zinc-500">
            {result.source === 'llm' ? `${result.model} · ${(result.latency_ms / 1000).toFixed(1)} s` : `Pre-written fallback · ${result.error}`}
            {result.spoken === false && ' · not spoken (no speech engine)'}
          </p>
        </div>
      )}
    </Section>
  )
}

// ---- Guard Bot ---------------------------------------------------------------------------
const guardFields = (ai: Settings['ai']) => ({
  guard_bot: ai.guard_bot,
  stt_model: ai.stt_model,
  owner_instructions: ai.owner_instructions,
})

const STT_MODELS: [Settings['ai']['stt_model'], string][] = [
  ['tiny.en', 'Fastest (75 MB)'],
  ['base.en', 'More accurate (145 MB)'],
  ['small.en', 'Most accurate, slow on older computers (484 MB)'],
]

function modelText(m: AudioModel): { text: string; tone: string } {
  switch (m.state) {
    case 'missing':
      return { text: m.error ?? 'Not installed', tone: 'text-amber-400' }
    case 'downloading':
      return { text: `Downloading…${m.progress != null ? ` ${Math.round(m.progress * 100)}%` : ''}`, tone: 'text-blue-300' }
    case 'loading':
      return { text: 'Loading…', tone: 'text-blue-300' }
    case 'ready':
      return { text: 'Ready', tone: 'text-emerald-400' }
    case 'error':
      return { text: m.error ?? 'Could not be set up', tone: 'text-red-400' }
    default:
      return m.downloaded
        ? { text: 'Downloaded; loads when first needed', tone: 'text-zinc-400' }
        : { text: `Not downloaded yet (${m.size_mb} MB). It downloads when first needed, or now.`, tone: 'text-zinc-400' }
  }
}

function ModelStatus({ name, model, onDownload }: { name: string; model?: AudioModel; onDownload: () => Promise<void> }) {
  const [busy, setBusy] = useState(false)
  if (!model) return null
  const { text, tone } = modelText(model)
  const canDownload = model.installed && (model.state === 'error' || (model.state === 'idle' && !model.downloaded))
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-zinc-800 bg-zinc-950/60 p-3">
      <div className="min-w-0 flex-1">
        <div className="text-sm text-zinc-200">{name}</div>
        <div className={cx('break-words text-xs', tone)}>{text}</div>
        {model.state === 'downloading' && model.progress != null && (
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-zinc-800">
            <div className="h-full rounded-full bg-blue-500 transition-all" style={{ width: `${Math.round(model.progress * 100)}%` }} />
          </div>
        )}
      </div>
      {canDownload && (
        <Button
          size="sm"
          icon={<Download className="h-4 w-4" />}
          loading={busy}
          onClick={async () => {
            setBusy(true)
            await onDownload()
            setBusy(false)
          }}
        >
          {model.state === 'error' ? 'Try again' : 'Download model'}
        </Button>
      )}
    </div>
  )
}

function GuardBotSection({ value, save }: { value: Settings['ai']; save: Save }) {
  const notify = useToast()
  const d = useDraft(guardFields(value))
  const models = usePoll(api.audioModels, 1500)
  const download = async (what: keyof AudioModels) => {
    try {
      await api.downloadAudioModel(what)
      await models.refresh()
    } catch (err) {
      notify(errorMessage(err), 'error')
    }
  }
  return (
    <Section
      id="guard-bot"
      title="Guard Bot"
      description="During an intrusion, Guardian listens through the phone camera’s microphone, turns what the person says into text on this computer and answers them through the camera’s speaker."
      dirty={d.dirty}
      onReset={d.reset}
      onSave={() => save({ ai: d.changes })}
    >
      <Toggle
        checked={d.draft.guard_bot}
        onChange={(v) => d.set('guard_bot', v)}
        label="Answer people who speak during an intrusion"
        description="Only while an unrecognised person is on a phone camera with its microphone on. Each reply is one short sentence that uses only what is true right now and your instructions; it never says the police are coming, and says you were alerted only if you were. At most one reply every 6 seconds, and none while you talk through the camera."
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Speech-to-text model" hint="Runs on this computer’s processor. Larger models understand more but answer later.">
          <select className="input" value={d.draft.stt_model} onChange={(e) => d.set('stt_model', e.target.value as Settings['ai']['stt_model'])}>
            {STT_MODELS.map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Your instructions" hint="Facts replies may use, e.g. “Deliveries go to the side door”. Leave empty if there are none.">
          <textarea
            className="input min-h-[4.5rem]"
            maxLength={300}
            value={d.draft.owner_instructions}
            placeholder="Deliveries go to the side door"
            onChange={(e) => d.set('owner_instructions', e.target.value)}
          />
        </Field>
      </div>
      <div className="space-y-2">
        <ModelStatus name={`Speech-to-text (${models.data?.speech.model ?? value.stt_model})`} model={models.data?.speech} onDownload={() => download('speech')} />
        <ModelStatus name="Sound recognition (YAMNet, Settings → Detection)" model={models.data?.sounds} onDownload={() => download('sounds')} />
        {d.draft.stt_model !== value.stt_model && <p className="hint">Save to see the status of the model you picked.</p>}
      </div>
    </Section>
  )
}

// ---- Voice & personality ------------------------------------------------------------
function toneText(intimidation: number, humor: number) {
  let tone = intimidation >= 67 ? 'Stern and intimidating' : intimidation >= 34 ? 'Firm and authoritative' : 'Calm and polite'
  if (humor >= 67) tone += ', with sarcastic wit'
  else if (humor >= 34) tone += ', with a touch of dry humour'
  return tone
}

function VoiceSection({ value, save, voice }: { value: Settings['ai']; save: Save; voice?: { available: boolean | null; engine: string | null; error: string | null } }) {
  const d = useDraft(voiceFields(value))
  const repeat = Math.round(40 - 0.32 * d.draft.persistence)

  return (
    <Section id="voice" title="Voice and personality" dirty={d.dirty} onReset={d.reset} onSave={() => save({ ai: d.changes })}>
      <div className="text-sm">
        {voice?.available === false ? (
          <ErrorNote>
            No speech engine on the server ({voice.error?.replace(/[.!\s]+$/, "")}). On Linux install <span className="font-mono">espeak-ng</span>; Windows and macOS
            have one built in.
          </ErrorNote>
        ) : (
          <span className="text-zinc-400">Speech engine: {voice?.engine ?? 'checking…'}</span>
        )}
      </div>
      <Toggle checked={d.draft.voice_enabled} onChange={(v) => d.set('voice_enabled', v)} label="Speak warnings aloud" description="When off, warnings are still written to the log and shown on the live view." />
      <Slider label="Speaking rate" value={d.draft.voice_rate} min={80} max={300} step={5} format={(v) => `${v} wpm`} onChange={(v) => d.set('voice_rate', v)} />
      <div className="grid gap-4 sm:grid-cols-3">
        <Slider label="Intimidation" value={d.draft.intimidation} min={0} max={100} onChange={(v) => d.set('intimidation', v)} />
        <Slider label="Humour" value={d.draft.humor} min={0} max={100} onChange={(v) => d.set('humor', v)} />
        <Slider label="Persistence" value={d.draft.persistence} min={0} max={100} onChange={(v) => d.set('persistence', v)} hint={`Repeats a warning every ${repeat} s`} />
      </div>
      <p className="text-sm text-zinc-400">
        Tone: <span className="text-zinc-200">{toneText(d.draft.intimidation, d.draft.humor)}</span>
      </p>
      <div className="border-t border-zinc-800 pt-4">
        <Toggle
          checked={d.draft.greet_insiders}
          onChange={(v) => d.set('greet_insiders', v)}
          label="Greet recognised people by name"
          description="e.g. “Welcome back, Sam.” Only while armed. Needs insiders with photos and face recognition turned on."
        />
      </div>
      {d.draft.greet_insiders && (
        <Slider label="Greet the same person at most every" value={d.draft.greet_cooldown_minutes} min={1} max={720} step={1} format={(v) => (v >= 60 ? `${(v / 60).toFixed(v % 60 ? 1 : 0)} h` : `${v} min`)} onChange={(v) => d.set('greet_cooldown_minutes', v)} />
      )}
    </Section>
  )
}

// ---- Schedule ---------------------------------------------------------------------------
const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const EVERY_DAY = [0, 1, 2, 3, 4, 5, 6]
const SCHEDULE_PRESETS: [string, ScheduleRule][] = [
  ['Every night 22:00–07:00', { days: EVERY_DAY, start: '22:00', end: '07:00' }],
  ['Weekdays 08:00–18:00', { days: [0, 1, 2, 3, 4], start: '08:00', end: '18:00' }],
]

function scheduleProblem(rules: ScheduleRule[]): string | null {
  for (const rule of rules) {
    if (!rule.days.length) return 'Each period needs at least one day.'
    if (!rule.start || !rule.end) return 'Each period needs a start and an end time.'
    if (rule.start === rule.end) return 'A period needs different start and end times.'
  }
  return null
}

function ScheduleSection({ value, save, status }: { value: Settings['schedule']; save: Save; status: Status | null }) {
  const d = useDraft(value)
  const rules = d.draft.rules
  const problem = scheduleProblem(rules)
  const setRule = (i: number, patch: Partial<ScheduleRule>) => d.set('rules', rules.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  const toggleDay = (i: number, day: number) => {
    const days = rules[i].days.includes(day) ? rules[i].days.filter((x) => x !== day) : [...rules[i].days, day].sort()
    setRule(i, { days })
  }
  const now = status && !d.dirty ? describeSchedule(status.schedule, status.armed)?.long : undefined
  const zone = status ? ` (UTC${status.time_zone.utc_offset})` : ''
  return (
    <Section
      id="schedule"
      title="Schedule"
      description={`Guardian arms itself when a period starts and disarms when it ends. Arming or disarming by hand lasts until the next start or end. Times are in the Guardian computer’s time zone${zone}.`}
      dirty={d.dirty}
      canSave={!problem}
      onReset={d.reset}
      onSave={() => save({ schedule: d.changes })}
    >
      <Toggle checked={d.draft.enabled} onChange={(v) => d.set('enabled', v)} label="Arm and disarm on a schedule" description={now} />
      {rules.length === 0 ? (
        <p className="text-sm text-zinc-500">No periods yet. Add one below.</p>
      ) : (
        <ul className="space-y-2">
          {rules.map((rule, i) => (
            <li key={i} className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border border-zinc-800 p-3">
              <div className="flex gap-1" role="group" aria-label="Days">
                {DAYS.map((name, day) => (
                  <button
                    key={name}
                    type="button"
                    aria-pressed={rule.days.includes(day)}
                    onClick={() => toggleDay(i, day)}
                    className={cx('h-8 w-10 rounded text-xs font-medium', rule.days.includes(day) ? 'bg-blue-600 text-white' : 'bg-zinc-800 text-zinc-400 hover:text-zinc-200')}
                  >
                    {name}
                  </button>
                ))}
              </div>
              <div className="flex items-center gap-2 text-sm text-zinc-400">
                <input type="time" aria-label="Start" className="input w-32 sm:w-36" value={rule.start} onChange={(e) => setRule(i, { start: e.target.value })} />
                to
                <input type="time" aria-label="End" className="input w-32 sm:w-36" value={rule.end} onChange={(e) => setRule(i, { end: e.target.value })} />
                {rule.start && rule.end && rule.end < rule.start && <span className="text-xs text-zinc-500">next day</span>}
              </div>
              <Button size="sm" variant="ghost" className="ml-auto" icon={<Trash2 className="h-4 w-4" />} aria-label="Remove period" onClick={() => d.set('rules', rules.filter((_, j) => j !== i))} />
            </li>
          ))}
        </ul>
      )}
      {problem && <p className="text-sm text-amber-400">{problem}</p>}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" icon={<Plus className="h-4 w-4" />} disabled={rules.length >= 14} onClick={() => d.set('rules', [...rules, { days: EVERY_DAY, start: '22:00', end: '07:00' }])}>
          Add period
        </Button>
        {SCHEDULE_PRESETS.map(([label, rule]) => (
          <Button key={label} size="sm" variant="ghost" disabled={rules.length >= 14} onClick={() => d.set('rules', [...rules, rule])}>
            {label}
          </Button>
        ))}
      </div>
    </Section>
  )
}

// ---- Detection --------------------------------------------------------------------------
const SOUND_GROUPS: [SoundGroup, string][] = [
  ['glass', 'Glass breaking'],
  ['alarm', 'Smoke or fire alarm'],
  ['scream', 'Scream or shout'],
  ['gunshot', 'Gunshot or explosion'],
  ['banging', 'Banging or knocking'],
  ['dog', 'Dog barking'],
  ['baby', 'Baby crying'],
  ['siren', 'Siren'],
]

function DetectionSection({ value, save }: { value: Settings['detection']; save: Save }) {
  const d = useDraft(value)
  return (
    <Section id="detection" title="Detection" dirty={d.dirty} onReset={d.reset} onSave={() => save({ detection: d.changes })}>
      <div className="grid gap-5 sm:grid-cols-2">
        <Slider label="Person confidence" value={d.draft.confidence} min={0.1} max={0.95} step={0.05} format={(v) => `${Math.round(v * 100)}%`} onChange={(v) => d.set('confidence', v)} hint="Higher means fewer false alarms but may miss people in poor light" />
        <Slider label="Minimum person size" value={d.draft.min_person_height} min={0} max={90} format={(v) => `${v}% of frame`} onChange={(v) => d.set('min_person_height', v)} hint="Ignores people far away, e.g. on the street" />
        <Slider label="Check every" value={d.draft.interval_ms} min={100} max={5000} step={100} format={(v) => `${(v / 1000).toFixed(1)} s`} onChange={(v) => d.set('interval_ms', v)} hint="Shorter reacts faster; longer uses less CPU. Detection only runs when something moves." />
      </div>
      <div className="border-t border-zinc-800 pt-4">
        <Toggle checked={d.draft.face_recognition} onChange={(v) => d.set('face_recognition', v)} label="Recognise insiders by face" description="People added on the Insiders page won't trigger alarms. Downloads two small face models on first use." />
      </div>
      {d.draft.face_recognition && (
        <div className="grid gap-5 sm:grid-cols-2">
          <Slider label="Face match strictness" value={d.draft.face_match_threshold} min={0.2} max={0.8} step={0.01} format={(v) => v.toFixed(2)} onChange={(v) => d.set('face_match_threshold', v)} hint="Higher is stricter. 0.36 suits most cameras." />
          <Slider label="Time to identify someone" value={d.draft.identify_seconds} min={0} max={10} step={0.5} format={(v) => `${v.toFixed(1)} s`} onChange={(v) => d.set('identify_seconds', v)} hint="A new person counts as a stranger after this long without a matching face. Strangers whose face is clearly seen are flagged sooner." />
          <Slider label="Trust after recognition" value={d.draft.insider_grace_seconds} min={0} max={300} step={5} format={(v) => `${v} s`} onChange={(v) => d.set('insider_grace_seconds', v)} hint="Keeps trusting an insider who turns away from the camera" />
        </div>
      )}
      <div className="space-y-4 border-t border-zinc-800 pt-4">
        <Toggle
          checked={d.draft.sound_recognition}
          onChange={(v) => d.set('sound_recognition', v)}
          label="Recognise sounds"
          description="Phone cameras send their microphone to this computer (about 32 kB/s each), which tells breaking glass from a barking dog. Downloads a 4 MB sound model on first use (status under Guard Bot)."
        />
        {d.draft.sound_recognition && (
          <>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              {SOUND_GROUPS.map(([key, label]) => (
                <Field key={key} label={label}>
                  <select
                    className="input"
                    value={d.draft.sound_actions[key]}
                    onChange={(e) => d.set('sound_actions', { ...d.draft.sound_actions, [key]: e.target.value as SoundAction })}
                  >
                    <option value="off">Ignore</option>
                    <option value="log">Log with a picture</option>
                    <option value="alert">Log and alert me</option>
                  </select>
                </Field>
              ))}
            </div>
            <p className="hint">
              A sound counts when it is heard clearly for about a second, at most once a minute per kind of sound and camera. While armed, glass, alarms, screams and gunshots set to alert raise the camera
              to level 3 and record, even with nobody on camera. Smoke alarms and a crying baby set to alert reach you even while disarmed. Guardian’s own voice and siren are ignored.
            </p>
          </>
        )}
      </div>
      <div className="grid gap-5 border-t border-zinc-800 pt-4 sm:grid-cols-2">
        <Field label="Loud sounds" hint="Used when sound recognition is off or can’t run. Heard by the microphones of phone cameras. Alerts also record a clip and alert you while armed. Nothing is spoken to whoever made the sound.">
          <select className="input" value={d.draft.sound_alerts} onChange={(e) => d.set('sound_alerts', e.target.value as Settings['detection']['sound_alerts'])}>
            <option value="off">Ignore</option>
            <option value="log">Log them with a picture</option>
            <option value="alert">Log them and alert me while armed</option>
          </select>
        </Field>
        {d.draft.sound_alerts !== 'off' && (
          <Slider label="Sound sensitivity" value={d.draft.sound_sensitivity} min={1} max={10} format={(v) => `${v} of 10`} onChange={(v) => d.set('sound_sensitivity', v)} hint={`Counts sounds ${50 - 4 * d.draft.sound_sensitivity} dB or more above what that place usually sounds like. Higher catches quieter sounds.`} />
        )}
      </div>
      <div className="grid gap-5 border-t border-zinc-800 pt-4 sm:grid-cols-2">
        <Field label="Bags left unattended after" hint="A backpack, handbag or suitcase that stands still this long with nobody near it is logged, and you're alerted while armed. Bags already there when the camera starts don't count. 0 turns this off.">
          <NumberInput value={d.draft.unattended_minutes} min={0} max={60} suffix="min" onChange={(v) => d.set('unattended_minutes', v)} />
        </Field>
        <Field label="Someone may have fallen" hint="Checks the posture of people who look like they're lying down; 10 s on the floor without getting up counts. Alerts go out armed or not, insiders included. Downloads a small pose model on first use.">
          <select className="input" value={d.draft.fall_alerts} onChange={(e) => d.set('fall_alerts', e.target.value as Settings['detection']['fall_alerts'])}>
            <option value="off">Don't check</option>
            <option value="log">Log it with a picture</option>
            <option value="alert">Log it and alert me</option>
          </select>
        </Field>
        {d.draft.fall_alerts === 'alert' && (
          <div className="sm:col-span-2">
            <Toggle checked={d.draft.fall_ask} onChange={(v) => d.set('fall_ask', v)} label="Ask “Are you OK?”" description="Through the camera's speaker, and only after the alert was actually sent: “Are you OK? I've let the owner know.”" />
          </div>
        )}
      </div>
      {d.draft.face_recognition && (
        <div className="space-y-4 border-t border-zinc-800 pt-4">
          <Toggle
            checked={d.draft.remember_visitors}
            onChange={(v) => d.set('remember_visitors', v)}
            label="Remember strangers’ faces"
            description={
              <>
                While armed, keeps the faces of people it doesn’t recognise on this computer and says when they come back. See{' '}
                <a href={href('visitors')} className="text-blue-400 hover:text-blue-300">
                  Visitors
                </a>
                .
              </>
            }
          />
          {d.draft.remember_visitors && (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Forget visitors after" hint="Days since they were last seen. 0 keeps them until you forget them.">
                <NumberInput value={d.draft.visitor_retention_days} min={0} max={3650} suffix="days" onChange={(v) => d.set('visitor_retention_days', v)} />
              </Field>
              <Field label="Count a new visit after" hint="Time away before coming back counts as another visit">
                <NumberInput value={d.draft.visit_gap_minutes} min={0} max={1440} suffix="min" onChange={(v) => d.set('visit_gap_minutes', v)} />
              </Field>
            </div>
          )}
        </div>
      )}
    </Section>
  )
}

// ---- Learning --------------------------------------------------------------------------------
function LearningSection({ value, save }: { value: Settings['learning']; save: Save }) {
  const d = useDraft(value)
  return (
    <Section
      id="learning"
      title="Learning"
      description={
        <>
          Guardian learns when people are usually in view of each camera, and from your answers. See and undo what it has learned on the{' '}
          <a href={href('learning')} className="text-blue-400 hover:text-blue-300">
            Learning page
          </a>
          .
        </>
      }
      dirty={d.dirty}
      onReset={d.reset}
      onSave={() => save({ learning: d.changes })}
    >
      <Toggle
        checked={d.draft.learn_from_feedback}
        onChange={(v) => d.set('learn_from_feedback', v)}
        label="Learn from my feedback"
        description="“False alarm” on a detection teaches Guardian to ignore that spot while nothing moves there. A person who moves or shows their face is still detected. When off, your answers are kept but change nothing."
      />
      <Toggle
        checked={d.draft.improve_faces}
        onChange={(v) => d.set('improve_faces', v)}
        label="Improve insider recognition by itself"
        description="When an insider is recognised clearly, Guardian keeps a new look at their face (another angle or light): at most one every 10 minutes and 12 per person. They are marked “Learned” on the Insiders page, where you can remove them."
      />
      <Field
        label="Unusual activity"
        hint="An unrecognised person at an hour when a camera usually sees nobody, while armed. Judged only after Guardian has watched for 3 days and for 3 hours around that time. The alert comes as soon as they are detected, without waiting for the alert level, at most every 10 minutes per camera."
      >
        <select className="input" value={d.draft.unusual_activity} onChange={(e) => d.set('unusual_activity', e.target.value as Settings['learning']['unusual_activity'])}>
          <option value="off">Ignore</option>
          <option value="log">Mark it as unusual in the event log</option>
          <option value="alert">Mark it and alert me straight away</option>
        </select>
      </Field>
    </Section>
  )
}

// ---- Escalation ---------------------------------------------------------------------------
function EscalationSection({ value, save, sirenAvailable }: { value: Settings['escalation']; save: Save; sirenAvailable?: boolean }) {
  const d = useDraft(value)
  return (
    <Section
      id="escalation"
      title="Alarm escalation"
      description="How long an unrecognised person may stay before each level is reached. Each level speaks a firmer warning."
      dirty={d.dirty}
      onReset={d.reset}
      onSave={() => save({ escalation: d.changes })}
    >
      <div className="grid gap-4 sm:grid-cols-4">
        <Field label="Level 2 after">
          <NumberInput value={d.draft.level2_after} min={1} suffix="sec" onChange={(v) => d.set('level2_after', v)} />
        </Field>
        <Field label="Level 3 after">
          <NumberInput value={d.draft.level3_after} min={2} suffix="sec" onChange={(v) => d.set('level3_after', v)} />
        </Field>
        <Field label="Level 4 after">
          <NumberInput value={d.draft.level4_after} min={3} suffix="sec" onChange={(v) => d.set('level4_after', v)} />
        </Field>
        <Field label="All clear after" hint="With nobody in view">
          <NumberInput value={d.draft.clear_after} min={2} suffix="sec" onChange={(v) => d.set('clear_after', v)} />
        </Field>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Start recording at">
          <LevelSelect value={d.draft.record_at_level} onChange={(v) => d.set('record_at_level', v)} />
        </Field>
        <Field label="Alert the owner at" hint="Through the channels under Notifications">
          <LevelSelect value={d.draft.alert_at_level} onChange={(v) => d.set('alert_at_level', v)} />
        </Field>
      </div>
      <div className="border-t border-zinc-800 pt-4">
        <Toggle
          checked={d.draft.siren_enabled}
          onChange={(v) => d.set('siren_enabled', v)}
          label="Sound the siren automatically"
          description={sirenAvailable === false ? 'No audio player found on the server; the siren cannot play.' : 'The panic button always sounds the siren.'}
        />
      </div>
      {d.draft.siren_enabled && (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Siren at">
            <LevelSelect value={d.draft.siren_at_level} onChange={(v) => d.set('siren_at_level', v)} />
          </Field>
          <Field label="Siren stops after">
            <NumberInput value={d.draft.siren_max_seconds} min={5} max={600} suffix="sec" onChange={(v) => d.set('siren_max_seconds', v)} />
          </Field>
        </div>
      )}
      <div className="border-t border-zinc-800 pt-4">
        <Field label="Alert when a camera goes offline after" hint="While armed. Catches unplugged, covered or dead-battery cameras. 0 turns it off.">
          <NumberInput value={d.draft.offline_alert_seconds} min={0} max={3600} suffix="sec" onChange={(v) => d.set('offline_alert_seconds', v)} />
        </Field>
      </div>
    </Section>
  )
}

// ---- Recording -----------------------------------------------------------------------------
function RecordingSection({ value, save, encoder }: { value: Settings['recording']; save: Save; encoder?: string }) {
  const d = useDraft(value)
  return (
    <Section id="recording" title="Recording" description={encoder && `Encoder: ${encoder}`} dirty={d.dirty} onReset={d.reset} onSave={() => save({ recording: d.changes })}>
      <div className="grid gap-4 sm:grid-cols-4">
        <Field label="Include before" hint="Footage kept from before the trigger">
          <NumberInput value={d.draft.preroll_seconds} min={0} max={15} suffix="sec" onChange={(v) => d.set('preroll_seconds', v)} />
        </Field>
        <Field label="Keep recording after" hint="Once the person has left">
          <NumberInput value={d.draft.postroll_seconds} min={0} max={60} suffix="sec" onChange={(v) => d.set('postroll_seconds', v)} />
        </Field>
        <Field label="Split clips every">
          <NumberInput value={d.draft.max_clip_seconds} min={30} max={1800} suffix="sec" onChange={(v) => d.set('max_clip_seconds', v)} />
        </Field>
        <Field label="Delete clips after" hint="Event pictures too. 0 keeps them forever">
          <NumberInput value={d.draft.retention_days} min={0} max={3650} suffix="days" onChange={(v) => d.set('retention_days', v)} />
        </Field>
      </div>
    </Section>
  )
}

// ---- Notifications ---------------------------------------------------------------------------
type Notifications = Settings['notifications']
type SecretKey = 'discord_webhook' | 'telegram_token' | 'ntfy_token' | 'webhook_url' | 'smtp_password'
const SECRET_KEYS: SecretKey[] = ['discord_webhook', 'telegram_token', 'ntfy_token', 'webhook_url', 'smtp_password']
const CHANNEL_NAMES: Record<NotificationChannel, string> = { discord: 'Discord', telegram: 'Telegram', ntfy: 'ntfy', webhook: 'Webhook', email: 'E-mail' }
const CHAT_TYPES: Record<string, string> = { private: 'private chat', group: 'group', supergroup: 'group', channel: 'channel' }

const notificationDraft = (n: Notifications) => ({
  telegram_chat_id: n.telegram_chat_id,
  ntfy_url: n.ntfy_url,
  smtp_host: n.smtp_host,
  smtp_port: n.smtp_port,
  smtp_user: n.smtp_user,
  email_to: n.email_to,
  // Secrets start empty; typing one replaces the saved value
  discord_webhook: '',
  telegram_token: '',
  ntfy_token: '',
  webhook_url: '',
  smtp_password: '',
})
type NotificationDraft = ReturnType<typeof notificationDraft>

// The fields of each channel, for its "Unsaved" hint
const CHANNEL_FIELDS: Record<NotificationChannel, (keyof NotificationDraft)[]> = {
  telegram: ['telegram_token', 'telegram_chat_id'],
  ntfy: ['ntfy_url', 'ntfy_token'],
  discord: ['discord_webhook'],
  email: ['smtp_host', 'smtp_port', 'smtp_user', 'smtp_password', 'email_to'],
  webhook: ['webhook_url'],
}

function Channel({ name, on, unsaved, children, hint }: { name: string; on: boolean; unsaved: boolean; children: ReactNode; hint: ReactNode }) {
  return (
    <div className="space-y-3 rounded-md border border-zinc-800 p-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        <Dot className={on ? 'bg-emerald-500' : 'bg-zinc-600'} /> {name}
        <span className="text-xs font-normal text-zinc-500">{on ? 'On' : 'Off'}</span>
        {unsaved && <span className="ml-auto text-xs font-normal text-amber-400">Unsaved</span>}
      </div>
      {children}
      <p className="hint">{hint}</p>
    </div>
  )
}

function NotificationsSection({ value, save }: { value: Notifications; save: Save }) {
  const notify = useToast()
  const d = useDraft(notificationDraft(value))
  const [removing, setRemoving] = useState<SecretKey[]>([])
  const [testing, setTesting] = useState(false)
  const [results, setResults] = useState<NotificationResult[] | null>(null)
  const [chats, setChats] = useState<{ id: string; name: string; type: string }[] | null>(null)
  const [finding, setFinding] = useState(false)

  // A secret left blank (or only spaces) keeps the saved one; Remove clears it when saving.
  const changes: Partial<NotificationDraft> = { ...d.changes }
  for (const key of SECRET_KEYS) {
    const typed = d.draft[key].trim()
    if (removing.includes(key)) changes[key] = ''
    else if (typed) changes[key] = typed
    else delete changes[key]
  }
  const dirty = Object.keys(changes).length > 0
  // On/Off is what is saved, decided by the server the same way it decides when sending
  const on = (c: NotificationChannel) => value.configured.includes(c)
  const unsaved = (c: NotificationChannel) => CHANNEL_FIELDS[c].some((f) => f in changes)

  const reset = () => {
    d.reset()
    setRemoving([])
    setChats(null)
  }
  const keep = (key: SecretKey) => setRemoving((r) => r.filter((k) => k !== key))

  const secret = (key: SecretKey, label: string, placeholder: string, hidden = true) => {
    const saved = value[`${key}_set`]
    const removed = removing.includes(key)
    return (
      <Field label={label}>
        <div className="flex gap-2">
          <input
            type={hidden ? 'password' : 'text'}
            autoComplete="off"
            spellCheck={false}
            className="input"
            value={d.draft[key]}
            placeholder={removed ? 'Removed when you save' : saved ? 'Saved; type to replace' : placeholder}
            onChange={(e) => {
              d.set(key, e.target.value)
              keep(key)
            }}
          />
          {removed ? (
            <Button variant="ghost" onClick={() => keep(key)}>
              Undo
            </Button>
          ) : (
            saved &&
            !d.draft[key].trim() && (
              <Button
                variant="ghost"
                onClick={() => {
                  d.set(key, '')
                  setRemoving((r) => [...r, key])
                }}
              >
                Remove
              </Button>
            )
          )}
        </div>
      </Field>
    )
  }

  const test = async () => {
    setTesting(true)
    setResults(null)
    try {
      setResults((await api.testNotifications()).results)
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setTesting(false)
    }
  }

  const findChat = async () => {
    setFinding(true)
    try {
      const found = (await api.telegramChats(d.draft.telegram_token.trim())).chats
      if (!found.length) notify('No messages yet. Send your bot any message in Telegram, then try again.', 'error')
      // A single chat is picked straight away; its name stays on screen so you can see whose it is
      if (found.length === 1) d.set('telegram_chat_id', found[0].id)
      setChats(found.length ? found : null)
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setFinding(false)
    }
  }

  const suggestTopic = () => {
    const random = Array.from(crypto.getRandomValues(new Uint8Array(6)), (b) => b.toString(36).padStart(2, '0')).join('')
    d.set('ntfy_url', `https://ntfy.sh/guardian-${random}`)
  }

  return (
    <Section
      id="notifications"
      title="Notifications"
      description="Where alerts go when someone reaches the alert level, with a picture; the clip follows once it is saved. Use as many as you like."
      dirty={dirty}
      onReset={reset}
      onSave={async () => {
        if (await save({ notifications: changes })) reset()
      }}
      extraActions={
        <Button icon={<Send className="h-4 w-4" />} loading={testing} disabled={dirty || !value.configured.length} onClick={test} title={dirty ? 'Save first' : 'Sends a test alert with a small picture to every channel that is on'}>
          Send test
        </Button>
      }
    >
      {results && (
        <ul className="space-y-1 rounded-md border border-zinc-800 p-3 text-sm">
          {results.map((r) => (
            <li key={r.channel} className="flex gap-2">
              <Dot className={cx('mt-1.5', !r.ok ? 'bg-red-500' : r.note ? 'bg-amber-500' : 'bg-emerald-500')} />
              <span className="font-medium">{CHANNEL_NAMES[r.channel]}</span>
              <span className={cx('min-w-0', !r.ok ? 'text-red-400' : r.note ? 'text-amber-400' : 'text-zinc-400')}>
                {!r.ok ? r.error : r.note ? `Test sent. ${r.note}` : 'Test sent'}
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="grid items-start gap-3 xl:grid-cols-2">
        <Channel
          name="Telegram"
          on={on('telegram')}
          unsaved={unsaved('telegram')}
          hint={<>In Telegram, create a bot with <span className="font-mono">@BotFather</span> and paste its token. Then send your bot any message, press Find chat and check that the name shown is yours.</>}
        >
          {secret('telegram_token', 'Bot token', '123456789:AA…')}
          <Field label="Chat">
            <div className="flex gap-2">
              <input className="input" value={d.draft.telegram_chat_id} placeholder="Chat id" onChange={(e) => d.set('telegram_chat_id', e.target.value)} />
              <Button loading={finding} disabled={!value.telegram_token_set && !d.draft.telegram_token.trim()} onClick={findChat}>
                Find chat
              </Button>
            </div>
          </Field>
          {chats && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-zinc-500">{chats.length === 1 ? 'Found:' : 'Pick your chat:'}</span>
              {chats.map((c) => (
                <Button key={c.id} size="sm" variant={d.draft.telegram_chat_id === c.id ? 'primary' : 'secondary'} onClick={() => d.set('telegram_chat_id', c.id)}>
                  {c.name}
                  {c.type && <span className="font-normal opacity-70">{CHAT_TYPES[c.type] ?? c.type}</span>}
                </Button>
              ))}
            </div>
          )}
        </Channel>
        <Channel
          name="ntfy (phone push)"
          on={on('ntfy')}
          unsaved={unsaved('ntfy')}
          hint="Install the ntfy app, subscribe to a long, hard-to-guess topic and paste its address here. Anyone who knows the topic can read the alerts. A self-hosted server needs attachments turned on to send pictures."
        >
          <Field label="Topic address">
            <div className="flex gap-2">
              <input className="input" spellCheck={false} value={d.draft.ntfy_url} placeholder="https://ntfy.sh/your-topic" onChange={(e) => d.set('ntfy_url', e.target.value)} />
              {!d.draft.ntfy_url && <Button onClick={suggestTopic}>Suggest</Button>}
            </div>
          </Field>
          {secret('ntfy_token', 'Access token (optional)', 'Only for protected topics')}
        </Channel>
        <Channel name="Discord" on={on('discord')} unsaved={unsaved('discord')} hint="Server settings → Integrations → Webhooks → New webhook → Copy webhook URL.">
          {secret('discord_webhook', 'Webhook URL', 'https://discord.com/api/webhooks/…', false)}
        </Channel>
        <Channel name="E-mail" on={on('email')} unsaved={unsaved('email')} hint="For Gmail, use an app password (Google account → Security → App passwords), not your normal password.">
          <div className="grid gap-3 sm:grid-cols-[1fr_7rem]">
            <Field label="Mail server">
              <input className="input" value={d.draft.smtp_host} onChange={(e) => d.set('smtp_host', e.target.value)} />
            </Field>
            <Field label="Port">
              <NumberInput value={d.draft.smtp_port} min={1} max={65535} onChange={(v) => d.set('smtp_port', v)} />
            </Field>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Sign in as">
              <input className="input" autoComplete="off" value={d.draft.smtp_user} placeholder="you@gmail.com" onChange={(e) => d.set('smtp_user', e.target.value)} />
            </Field>
            {secret('smtp_password', 'Password', 'App password')}
          </div>
          <Field label="Send alerts to">
            <input className="input" type="email" value={d.draft.email_to} placeholder="you@example.com" onChange={(e) => d.set('email_to', e.target.value)} />
          </Field>
        </Channel>
        <Channel
          name="Webhook"
          on={on('webhook')}
          unsaved={unsaved('webhook')}
          hint={<>Guardian POSTs JSON with <span className="font-mono">title</span>, <span className="font-mono">message</span>, <span className="font-mono">severity</span>, <span className="font-mono">time</span> and <span className="font-mono">snapshot_jpeg_base64</span>. Works with Home Assistant, Node-RED or n8n.</>}
        >
          {secret('webhook_url', 'Address', 'https://…', false)}
        </Channel>
      </div>
    </Section>
  )
}

// ---- Briefing ------------------------------------------------------------------------------------
function BriefingSection({ value, save, channels, status }: { value: Settings['briefing']; save: Save; channels: number; status: Status | null }) {
  const d = useDraft(value)
  const zone = status ? ` (UTC${status.time_zone.utc_offset})` : ''
  return (
    <Section
      id="briefing"
      title="Daily briefing"
      description="A few sentences on the last 24 hours on the Live page: incidents and how far they escalated, alerts, who was recognised, cameras that went offline and arming changes. The language model writes it from the event log and is checked against it; when it isn’t available, the briefing is written from a template."
      dirty={d.dirty}
      canSave={!!d.draft.time}
      onReset={d.reset}
      onSave={() => save({ briefing: d.changes })}
    >
      <Toggle checked={d.draft.enabled} onChange={(v) => d.set('enabled', v)} label="Write a daily briefing" description="Refresh on the Live page writes a new one at any time." />
      <Field label="Write it every day at" hint={`In the Guardian computer’s time zone${zone}. If Guardian is off then, it is written when it starts.`}>
        <input type="time" aria-label="Briefing time" className="input w-36" value={d.draft.time} disabled={!d.draft.enabled} onChange={(e) => d.set('time', e.target.value)} />
      </Field>
      <Toggle
        checked={d.draft.send}
        onChange={(v) => d.set('send', v)}
        disabled={!d.draft.enabled}
        label="Also send it to me"
        description={
          channels > 0 ? (
            'At that time, through the alert channels set up under Notifications.'
          ) : (
            <span className="text-amber-400">No alert channel is set up yet; set one up under Notifications.</span>
          )
        }
      />
    </Section>
  )
}

// ---- System ------------------------------------------------------------------------------------
function Stat({ label, value, warn }: { label: string; value: ReactNode; warn?: boolean }) {
  return (
    <div className="rounded-md border border-zinc-800 p-3">
      <div className="text-xs text-zinc-500">{label}</div>
      <div title={typeof value === 'string' ? value : undefined} className={cx('mt-0.5 truncate text-sm tabular-nums', warn ? 'text-amber-400' : 'text-zinc-100')}>{value}</div>
    </div>
  )
}

function SystemSection() {
  const { data: s } = usePoll(api.system, 5000)
  if (!s) return null
  return (
    <Section id="system" title="System">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="CPU" value={`${Math.round(s.cpu_percent)}% of ${s.cpu_count} cores (Guardian ${s.process_cpu_percent}%)`} />
        <Stat label="Memory" value={`${formatBytes(s.memory_used)} / ${formatBytes(s.memory_total)} (Guardian ${formatBytes(s.process_memory)})`} />
        <Stat label="Disk free" value={formatBytes(s.disk_free)} warn={s.disk_free < 2 * 1024 ** 3} />
        <Stat label="Uptime" value={formatUptime(s.uptime_seconds)} />
        <Stat label="Person detector" value={s.detector.loaded ? `${s.detector.model} on ${s.detector.device}` : s.detector.error ?? 'Loads with the first frame'} warn={!!s.detector.error} />
        <Stat label="Detection time" value={s.detector.inference_ms != null ? `${s.detector.inference_ms} ms` : '–'} />
        <Stat label="Language model" value={s.ai.loading ? `Loading ${s.ai.model ?? 'model'}…` : s.ai.last_error ?? s.ai.model ?? '–'} warn={!s.ai.loading && !!s.ai.last_error} />
        <Stat label="Voice lines ready" value={`${s.ai.ready_lines} prepared · ${s.ai.rejected_replies} rejected`} />
        <Stat label="Recordings" value={`${formatBytes(s.recordings_bytes)} · ${s.recording_encoder}`} />
        <Stat label="Speech" value={s.voice.available ? s.voice.engine : s.voice.available === false ? 'Not available' : 'Checking…'} warn={s.voice.available === false} />
        <Stat label="Siren player" value={s.siren.available ? s.siren.player : 'Not available'} warn={!s.siren.available} />
        <Stat label="Talk on this computer" value={!s.talk.mode ? 'No audio player' : `${s.talk.player} · ${s.talk.mode === 'live' ? 'as you speak' : 'when you let go'}`} warn={!s.talk.mode} />
        <Stat label="Face models" value={s.faces.state} warn={s.faces.state === 'error'} />
        <Stat label="Fall check model" value={s.pose.state === 'idle' ? 'Loads when someone may be lying down' : s.pose.state} warn={s.pose.state === 'error'} />
        <Stat label="Platform" value={`${s.platform} · Python ${s.python}`} />
        <Stat label="Phone cameras" value={!s.phone.enabled ? 'Turned off' : s.phone.public_url || `https://${s.phone.addresses[0] ?? 'this computer'}:${s.phone.port}`} />
      </div>
      <p className="hint">
        Data folder: <span className="font-mono">{s.data_dir}</span>
      </p>
      <VaultPanel />
    </Section>
  )
}

// ---- Property map -------------------------------------------------------------------------------
function MapSection() {
  const [info, setInfo] = useState<MapInfo | null>(null)
  useEffect(() => {
    mapApi.get().then(setInfo, () => setInfo(null))
  }, [])
  const placed = info?.cameras.filter((c) => c.calibration) ?? []
  const state = !info
    ? null
    : !info.image
      ? 'Not set up.'
      : `${info.image === 'grid' ? 'Blank grid' : 'Floorplan picture'}, ${info.metres_per_px ? 'scale set' : 'scale not set'}, ${placed.length} of ${info.cameras.length} cameras placed${placed.length ? ` (${placed.map((c) => c.name).join(', ')})` : ''}.`
  return (
    <Section
      id="map"
      title="Property map"
      description="A view from above of where people are, joined up across cameras. The floorplan, its scale and where each camera looks are set on the Map page."
      extraActions={
        <a href={href('map', 'setup')} className="inline-flex h-9 items-center rounded-md border border-zinc-700 bg-zinc-900 px-3.5 text-sm font-medium text-zinc-100 hover:bg-zinc-800">
          Set up the map
        </a>
      }
    >
      {state && <p className="text-sm text-zinc-300">{state}</p>}
      <p className="hint">
        People seen by two cameras within 1.5 m of each other at the same moment count as one person. Someone who leaves one camera and appears on another
        keeps their number if they could have walked there (3 m/s at most), and a recognised face always decides who it is.
      </p>
    </Section>
  )
}

// ---- Page ------------------------------------------------------------------------------------
const SECTIONS = [
  ['cameras', 'Cameras'],
  ['map', 'Property map'],
  ['schedule', 'Schedule'],
  ['ai', 'Language model'],
  ['guard-bot', 'Guard Bot'],
  ['voice', 'Voice'],
  ['detection', 'Detection'],
  ['learning', 'Learning'],
  ['escalation', 'Escalation'],
  ['recording', 'Recording'],
  ['notifications', 'Notifications'],
  ['briefing', 'Briefing'],
  ['system', 'System'],
] as const

export default function SettingsPage({ section }: { section: string }) {
  const notify = useToast()
  const { status, refresh } = useStatus()
  const [settings, setSettings] = useState<Settings | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const sys = usePoll(api.system, 30000)

  useEffect(() => {
    api.settings().then(setSettings, (err) => setLoadError(errorMessage(err)))
  }, [])

  useEffect(() => {
    if (settings && section) document.getElementById(section)?.scrollIntoView({ behavior: 'smooth' })
  }, [settings, section])

  const save: Save = async (patch) => {
    try {
      setSettings(await api.updateSettings(patch))
      notify('Settings saved', 'success')
      refresh()
      return true
    } catch (err) {
      notify(errorMessage(err), 'error')
      return false
    }
  }

  if (loadError) return <ErrorNote>{loadError}</ErrorNote>
  if (!settings) return <p className="text-sm text-zinc-500">Loading settings…</p>

  // Sections re-mount with fresh drafts whenever the saved values change.
  const k = (v: unknown) => JSON.stringify(v)
  return (
    <div className="grid gap-6 lg:grid-cols-[180px_minmax(0,1fr)]">
      <nav className="hidden lg:block">
        <ul className="sticky top-0 space-y-0.5 text-sm">
          {SECTIONS.map(([id, label]) => (
            <li key={id}>
              <a href={`#/settings/${id}`} className={cx('block rounded px-2 py-1.5', section === id ? 'bg-zinc-800 text-zinc-50' : 'text-zinc-400 hover:text-zinc-100')}>
                {label}
              </a>
            </li>
          ))}
        </ul>
      </nav>
      <div className="min-w-0 space-y-4">
        <CamerasSection key={k(settings.phone)} phoneSettings={settings.phone} savePhone={(phone) => save({ phone })} />
        <MapSection />
        <ScheduleSection key={k(settings.schedule)} value={settings.schedule} save={save} status={status} />
        <ModelSection key={k({ ...modelFields(settings.ai), key: settings.ai.api_key_set })} value={settings.ai} save={save} />
        <GuardBotSection key={k(guardFields(settings.ai))} value={settings.ai} save={save} />
        <VoiceSection key={k(voiceFields(settings.ai))} value={settings.ai} save={save} voice={status?.voice} />
        <DetectionSection key={k(settings.detection)} value={settings.detection} save={save} />
        <LearningSection key={k(settings.learning)} value={settings.learning} save={save} />
        <EscalationSection key={k(settings.escalation)} value={settings.escalation} save={save} sirenAvailable={status?.siren.available} />
        <RecordingSection key={k(settings.recording)} value={settings.recording} save={save} encoder={sys.data?.recording_encoder} />
        <NotificationsSection key={k(settings.notifications)} value={settings.notifications} save={save} />
        <BriefingSection key={k(settings.briefing)} value={settings.briefing} save={save} channels={settings.notifications.configured.length} status={status} />
        <SystemSection />
      </div>
    </div>
  )
}
