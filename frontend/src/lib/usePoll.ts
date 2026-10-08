import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Calls `fetcher` immediately and then every `interval` ms. Polling pauses while the
 * tab is hidden. `deps` restart polling (e.g. when filters change).
 */
export function usePoll<T>(fetcher: () => Promise<T>, interval: number, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [loading, setLoading] = useState(true)
  const fetcherRef = useRef(fetcher)
  const seq = useRef(0)

  useEffect(() => {
    fetcherRef.current = fetcher
  })

  const refresh = useCallback(async () => {
    const id = ++seq.current
    try {
      const result = await fetcherRef.current()
      if (id === seq.current) {
        setData(result)
        setError(null)
      }
    } catch (err) {
      if (id === seq.current) setError(err as Error)
    } finally {
      if (id === seq.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined
    let stopped = false
    const tick = async () => {
      if (stopped) return
      if (document.visibilityState === 'visible') await refresh()
      if (!stopped) timer = setTimeout(tick, interval)
    }
    tick()
    const onVisible = () => {
      if (document.visibilityState === 'visible') refresh()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      stopped = true
      clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [interval, refresh, ...deps])

  return { data, error, loading, refresh }
}
