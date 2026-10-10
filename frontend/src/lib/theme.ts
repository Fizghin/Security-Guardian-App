import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { local } from './storage'

export type ThemePref = 'system' | 'light' | 'dark'
const KEY = 'guardian.theme'
const media = () => window.matchMedia('(prefers-color-scheme: light)')

function resolve(pref: ThemePref): 'light' | 'dark' {
  return pref === 'system' ? (media().matches ? 'light' : 'dark') : pref
}

function apply(pref: ThemePref) {
  const theme = resolve(pref)
  document.documentElement.dataset.theme = theme
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'light' ? '#f4f4f5' : '#09090b')
}

/** The theme preference, applied to the page and remembered in this browser. */
export function useTheme() {
  const [pref, setPref] = useState<ThemePref>(() => (local.get(KEY) as ThemePref) || 'system')
  useEffect(() => {
    apply(pref)
    if (pref !== 'system') return
    const m = media()
    const onChange = () => apply('system')
    m.addEventListener('change', onChange)
    return () => m.removeEventListener('change', onChange)
  }, [pref])
  const choose = useCallback((p: ThemePref) => {
    local.set(KEY, p)
    setPref(p)
  }, [])
  return { pref, resolved: resolve(pref), choose }
}

export const ThemeContext = createContext<{ pref: ThemePref; choose: (p: ThemePref) => void }>({ pref: 'system', choose: () => {} })
export const useThemeContext = () => useContext(ThemeContext)
