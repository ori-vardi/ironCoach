import { useState, useEffect, useRef } from 'react'

/**
 * Animates a number from 0 to `end` over `duration` ms.
 * Returns the current animated value (integer by default, float if decimals > 0).
 */
export default function useCountUp(end, { duration = 800, decimals = 0, enabled = true } = {}) {
  const [value, setValue] = useState(enabled ? 0 : end)
  const prev = useRef(0)

  useEffect(() => {
    if (!enabled || typeof end !== 'number' || isNaN(end)) {
      setValue(end)
      return
    }
    const start = prev.current
    const diff = end - start
    if (diff === 0) return
    const startTime = performance.now()
    let raf
    function tick(now) {
      const elapsed = now - startTime
      const progress = Math.min(elapsed / duration, 1)
      // ease-out quad
      const eased = 1 - (1 - progress) * (1 - progress)
      const current = start + diff * eased
      setValue(decimals > 0 ? parseFloat(current.toFixed(decimals)) : Math.round(current))
      if (progress < 1) {
        raf = requestAnimationFrame(tick)
      } else {
        prev.current = end
      }
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [end, duration, decimals, enabled])

  return value
}
