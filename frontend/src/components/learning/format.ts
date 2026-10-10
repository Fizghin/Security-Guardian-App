import { formatSeen } from '../../lib/format'

/** "today 13:45" or "Oct 3, 13:45", from epoch seconds. */
export const seen = (epoch: number) => formatSeen(new Date(epoch * 1000).toISOString())
