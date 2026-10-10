import { MessageSquareText } from 'lucide-react'
import type { Learning } from '../../api'
import { Card, Empty } from '../ui'

/** How the owner answered, per camera. */
export default function FeedbackStats({ data }: { data: Learning }) {
  const { days, cameras } = data.feedback
  const wrongPerson = cameras.some((c) => c.wrong_person > 0)
  return (
    <Card title="Your feedback" actions={<span className="text-xs text-zinc-500">Last {days} days</span>}>
      {cameras.length === 0 ? (
        <Empty icon={<MessageSquareText className="h-8 w-8" />} title="No answers yet">
          Mark detections “Correct” or “False alarm” in the event log or in Recent activity on the Live page. Guardian
          learns from each answer.
        </Empty>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-zinc-500">
              <th className="pb-2 font-medium">Camera</th>
              <th className="pb-2 text-right font-medium">Correct</th>
              <th className="pb-2 text-right font-medium">False alarm</th>
              {wrongPerson && <th className="pb-2 text-right font-medium">Wrong person</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/80">
            {cameras.map((c) => (
              <tr key={c.camera ?? ''}>
                <td className="py-2 text-zinc-200">{c.camera ?? 'No camera'}</td>
                <td className="py-2 text-right tabular-nums text-zinc-300">{c.real}</td>
                <td className="py-2 text-right tabular-nums text-zinc-300">{c.false_alarm}</td>
                {wrongPerson && <td className="py-2 text-right tabular-nums text-zinc-300">{c.wrong_person}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  )
}
