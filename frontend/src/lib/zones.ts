// Zone geometry, matching backend/services/zones.py so the editor refuses what the server would.

export type Point = number[]
export type Zone = Point[]

export const MIN_AREA = 0.001 // share of the picture
// Zones are stored in 0..1, so on a picture of another shape they cover different places
const SHAPE_TOLERANCE = 0.05

// Corners are stored to 4 decimals; as whole numbers the checks below are exact
const grid = (zone: Zone) => zone.map(([x, y]) => [Math.round(x * 10000), Math.round(y * 10000)])

const orient = (a: Point, b: Point, c: Point) => Math.sign((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))

const between = (p: Point, q: Point, r: Point) =>
  Math.min(p[0], q[0]) <= r[0] && r[0] <= Math.max(p[0], q[0]) && Math.min(p[1], q[1]) <= r[1] && r[1] <= Math.max(p[1], q[1])

/** Whether the segments ab and cd share any point. */
function touch(a: Point, b: Point, c: Point, d: Point) {
  const [o1, o2, o3, o4] = [orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)]
  if (o1 !== o2 && o3 !== o4) return true
  return (o1 === 0 && between(a, b, c)) || (o2 === 0 && between(a, b, d)) || (o3 === 0 && between(c, d, a)) || (o4 === 0 && between(c, d, b))
}

/** Whether the outline crosses or touches itself; `closed` includes the edge back to the first corner. */
export function crossesItself(zone: Zone, closed: boolean) {
  const p = grid(zone)
  const n = p.length
  const edges = closed ? n : n - 1
  for (let i = 0; i < edges; i++) {
    for (let j = i + 2; j < edges; j++) {
      if (closed && i === 0 && j === n - 1) continue // neighbours through the first corner
      if (touch(p[i], p[i + 1], p[j], p[(j + 1) % n])) return true
    }
  }
  // An open outline whose newest edge runs back along the one before it
  if (!closed && n >= 3) {
    const [a, b, c] = p.slice(-3)
    if (orient(a, b, c) === 0 && (a[0] - b[0]) * (c[0] - b[0]) + (a[1] - b[1]) * (c[1] - b[1]) > 0) return true
  }
  return false
}

/** Share of the picture inside the zone. */
export function area(zone: Zone) {
  const p = grid(zone)
  let twice = 0
  p.forEach(([x, y], i) => {
    const [px, py] = p.at(i - 1)!
    twice += px * y - x * py
  })
  return Math.abs(twice) / 2 / 10000 ** 2
}

/** Why a finished zone can't be used, or null. */
export function zoneProblem(zone: Zone): string | null {
  if (crossesItself(zone, true)) return "A zone's outline can't cross or touch itself."
  if (area(zone) < MIN_AREA) return 'That zone is too small.'
  return null
}

/** Even-odd rule, like the server: what counts as inside a zone. */
export function inside([x, y]: Point, zone: Zone) {
  let hit = false
  zone.forEach(([xi, yi], i) => {
    const [xj, yj] = zone.at(i - 1)!
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) hit = !hit
  })
  return hit
}

/** Whether the picture's shape (width / height) differs from the one the zones were drawn on. */
export const shapeChanged = (drawnOn: number | null | undefined, now: number | null | undefined) =>
  !!drawnOn && !!now && Math.abs(now / drawnOn - 1) > SHAPE_TOLERANCE
