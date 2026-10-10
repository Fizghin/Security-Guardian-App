import { useState } from 'react'
import { api, type SecurityEvent } from '../api'
import { EventPicture } from '../components/EventRow'
import FaceSamples from '../components/learning/FaceSamples'
import FeedbackStats from '../components/learning/FeedbackStats'
import ForgetCamera from '../components/learning/ForgetCamera'
import IgnoredSpots from '../components/learning/IgnoredSpots'
import RoutineSection from '../components/learning/RoutineSection'
import { ErrorNote } from '../components/ui'
import { usePoll } from '../lib/usePoll'

/** What Guardian has learned from watching, from the owner's feedback and by itself. Each part is its own section. */
export default function LearningPage() {
  const { data, error, refresh } = usePoll(api.learning, 10000)
  const [picture, setPicture] = useState<SecurityEvent | null>(null)

  if (!data) return error ? <ErrorNote>{error.message}</ErrorNote> : <p className="text-sm text-zinc-500">Loading…</p>

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
      <div className="min-w-0 space-y-4">
        {error && <ErrorNote>{error.message}</ErrorNote>}
        <RoutineSection />
        <IgnoredSpots data={data} onChanged={refresh} onOpenPicture={setPicture} />
      </div>
      <div className="space-y-4">
        <FaceSamples data={data} />
        <FeedbackStats data={data} />
        <ForgetCamera data={data} onChanged={refresh} />
      </div>
      <EventPicture event={picture} onClose={() => setPicture(null)} />
    </div>
  )
}
