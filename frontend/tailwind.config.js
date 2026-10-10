// Colours come from CSS variables (src/theme.css) so the dashboard can switch between dark and light themes.
const HUES = ['zinc', 'red', 'blue', 'amber', 'emerald', 'yellow', 'orange', 'cyan', 'indigo', 'green', 'sky']
const STEPS = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950]
const themed = Object.fromEntries(
  HUES.map((h) => [h, Object.fromEntries(STEPS.map((s) => [s, `rgb(var(--${h}-${s}) / <alpha-value>)`]))]),
)

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: themed,
      fontFamily: {
        sans: ['Inter', 'ui-sans-serif', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      keyframes: {
        'fade-in': { from: { opacity: '0', transform: 'translateY(4px)' }, to: { opacity: '1', transform: 'none' } },
        'scale-in': { from: { opacity: '0', transform: 'scale(.97)' }, to: { opacity: '1', transform: 'none' } },
      },
      animation: {
        // "backwards", not "both": a transform animation that stays in effect would trap fixed-position
        // children (modals) inside the animated element.
        'fade-in': 'fade-in .25s ease-out backwards',
        'scale-in': 'scale-in .15s ease-out backwards',
      },
    },
  },
  plugins: [],
}
