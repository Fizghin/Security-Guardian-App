import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Plus, RefreshCw, Send, Sparkles, Trash2 } from 'lucide-react'
import { api, type AITestResult, type ScheduleRule, type ScheduleStatus, type Settings, type SettingsPatch } from '../api'
import CamerasSection from '../components/CamerasSection'
import { Button, Card, Dot, ErrorNote, Field, Slider, Toggle } from '../components/ui'
import { cx } from '../lib/cx'
import { formatBytes, formatUpcoming, formatUptime } from '../lib/format'
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
  onSave,
  onReset,
  extraActions,
}: {
  id: string
  title: string
  description?: ReactNode
  children: ReactNode
  dirty?: boolean
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
                disabled={!dirty}
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
          description="e.g. “Welcome back, Sam.” Needs insiders with photos and face recognition turned on."
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

function ScheduleSection({ value, save, status }: { value: Settings['schedule']; save: Save; status?: ScheduleStatus }) {
  const d = useDraft(value)
  const rules = d.draft.rules
  const problem = scheduleProblem(rules)
  const setRule = (i: number, patch: Partial<ScheduleRule>) => d.set('rules', rules.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  const toggleDay = (i: number, day: number) => {
    const days = rules[i].days.includes(day) ? rules[i].days.filter((x) => x !== day) : [...rules[i].days, day].sort()
    setRule(i, { days })
  }
  const now =
    status?.enabled && status.next_change && !d.dirty
      ? `${status.active ? 'Armed' : 'Disarmed'} by the schedule now; ${status.active ? 'disarms' : 'arms'} ${formatUpcoming(status.next_change)}.`
      : null
  return (
    <Section
      id="schedule"
      title="Schedule"
      description="Guardian arms itself during these periods and disarms outside them. Arming or disarming by hand lasts until the next change."
      dirty={d.dirty && !problem}
      onReset={d.reset}
      onSave={() => save({ schedule: d.changes })}
    >
      <Toggle checked={d.draft.enabled} onChange={(v) => d.set('enabled', v)} label="Arm and disarm on a schedule" description={now ?? 'Times are this computer’s local time.'} />
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
        <Field label="Alert the owner at" hint="Discord and/or e-mail, see Notifications">
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
        <Field label="Delete clips after" hint="0 keeps them forever">
          <NumberInput value={d.draft.retention_days} min={0} max={3650} suffix="days" onChange={(v) => d.set('retention_days', v)} />
        </Field>
      </div>
    </Section>
  )
}

// ---- Notifications ---------------------------------------------------------------------------
function NotificationsSection({ status }: { status?: { discord: boolean; email: boolean; email_to: string | null } }) {
  const notify = useToast()
  const [busy, setBusy] = useState(false)
  const test = async () => {
    setBusy(true)
    try {
      const { results } = await api.testNotifications()
      for (const r of results) notify(r.ok ? `${r.channel}: test sent` : `${r.channel}: ${r.error}`, r.ok ? 'success' : 'error')
    } catch (err) {
      notify(errorMessage(err), 'error')
    } finally {
      setBusy(false)
    }
  }
  return (
    <Section
      id="notifications"
      title="Notifications"
      description="Alerts include a snapshot; when the clip is saved it is sent as well. Credentials are set in backend/.env and never pass through the dashboard."
      extraActions={
        <Button icon={<Send className="h-4 w-4" />} loading={busy} disabled={!status || (!status.discord && !status.email)} onClick={test}>
          Send test
        </Button>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="rounded-md border border-zinc-800 p-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Dot className={status?.discord ? 'bg-emerald-500' : 'bg-zinc-600'} /> Discord
          </div>
          <p className="mt-1 text-xs text-zinc-500">{status?.discord ? 'Webhook configured' : <>Set <span className="font-mono">DISCORD_WEBHOOK_URL</span></>}</p>
        </div>
        <div className="rounded-md border border-zinc-800 p-3">
          <div className="flex items-center gap-2 text-sm font-medium">
            <Dot className={status?.email ? 'bg-emerald-500' : 'bg-zinc-600'} /> E-mail
          </div>
          <p className="mt-1 text-xs text-zinc-500">
            {status?.email ? `Sends to ${status.email_to}` : <>Set <span className="font-mono">SMTP_USER</span>, <span className="font-mono">SMTP_PASSWORD</span> and <span className="font-mono">ALERT_EMAIL</span></>}
          </p>
        </div>
      </div>
      <p className="hint">Restart Guardian after editing backend/.env.</p>
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
        <Stat label="Face models" value={s.faces.state} warn={s.faces.state === 'error'} />
        <Stat label="Platform" value={`${s.platform} · Python ${s.python}`} />
        <Stat label="Phone cameras" value={!s.phone.enabled ? 'Turned off' : s.phone.public_url || `https://${s.phone.addresses[0] ?? 'this computer'}:${s.phone.port}`} />
      </div>
      <p className="hint">
        Data folder: <span className="font-mono">{s.data_dir}</span>
      </p>
    </Section>
  )
}

// ---- Page ------------------------------------------------------------------------------------
const SECTIONS = [
  ['cameras', 'Cameras'],
  ['schedule', 'Schedule'],
  ['ai', 'Language model'],
  ['voice', 'Voice'],
  ['detection', 'Detection'],
  ['escalation', 'Escalation'],
  ['recording', 'Recording'],
  ['notifications', 'Notifications'],
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
        <ScheduleSection key={k(settings.schedule)} value={settings.schedule} save={save} status={status?.schedule} />
        <ModelSection key={k({ ...modelFields(settings.ai), key: settings.ai.api_key_set })} value={settings.ai} save={save} />
        <VoiceSection key={k(voiceFields(settings.ai))} value={settings.ai} save={save} voice={status?.voice} />
        <DetectionSection key={k(settings.detection)} value={settings.detection} save={save} />
        <EscalationSection key={k(settings.escalation)} value={settings.escalation} save={save} sirenAvailable={status?.siren.available} />
        <RecordingSection key={k(settings.recording)} value={settings.recording} save={save} encoder={sys.data?.recording_encoder} />
        <NotificationsSection status={sys.data?.notifications} />
        <SystemSection />
      </div>
    </div>
  )
}
