// Chart colours read the theme tokens, so charts follow light and dark mode.
const token = (name: string) => `rgb(var(--${name}))`

export const CHART = {
  grid: token('zinc-800'),
  axis: token('zinc-700'),
  tick: token('zinc-500'),
  bar: token('blue-500'),
  cursor: token('zinc-800'),
}

export const chartTooltip = {
  contentStyle: { background: token('zinc-900'), border: `1px solid ${token('zinc-700')}`, borderRadius: 8, fontSize: 12, color: token('zinc-200') },
  labelStyle: { color: token('zinc-200') },
  itemStyle: { color: token('zinc-300') },
  cursor: { fill: token('zinc-800') },
}
