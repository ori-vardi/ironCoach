import { useState, useEffect } from 'react'
import useCountUp from '../../utils/useCountUp'

/**
 * Circular progress ring with animated fill and count-up value.
 * Props:
 *   value     - current value (number)
 *   target    - target value (ring shows value/target %)
 *   label     - text below the number
 *   unit      - suffix after the number (e.g. "g", "ml")
 *   color     - ring color
 *   size      - diameter in px (default 130)
 *   thickness - ring stroke width (default 10)
 */
export default function ProgressRing({ value = 0, target, label, unit = '', color = 'var(--accent)', size = 130, thickness = 10 }) {
  const [mounted, setMounted] = useState(false)
  useEffect(() => { setMounted(true) }, [])

  const pct = target > 0 ? Math.min(value / target, 1.5) : 0
  const displayPct = Math.min(pct, 1)

  const radius = (size - thickness) / 2
  const circumference = 2 * Math.PI * radius
  const offset = circumference - (mounted ? displayPct : 0) * circumference

  const animatedValue = useCountUp(Math.round(value), { duration: 1000 })

  // Over-target glow effect
  const isOver = pct > 1

  return (
    <div className="progress-ring-wrapper" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="progress-ring-svg">
        {/* Background track */}
        <circle
          cx={size / 2} cy={size / 2} r={radius}
          fill="none"
          stroke="var(--bg-3)"
          strokeWidth={thickness}
        />
        {/* Colored fill */}
        <circle
          cx={size / 2} cy={size / 2} r={radius}
          fill="none"
          stroke={color}
          strokeWidth={thickness}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          className="progress-ring-fill"
          style={{
            filter: isOver ? `drop-shadow(0 0 6px ${color})` : undefined,
          }}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
      <div className="progress-ring-text">
        <span className="progress-ring-value" style={{ color }}>
          {animatedValue}{unit && <span className="progress-ring-unit">{unit}</span>}
        </span>
        <span className="progress-ring-label">{label}</span>
        {target > 0 && (
          <span className="progress-ring-target">/ {target}{unit}</span>
        )}
      </div>
    </div>
  )
}
