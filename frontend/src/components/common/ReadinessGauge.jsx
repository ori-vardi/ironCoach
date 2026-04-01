import useCountUp from '../../utils/useCountUp'
import { useI18n } from '../../i18n/I18nContext'
import InfoTip from './InfoTip'

const COMPONENT_KEYS = {
  tsb: 'readiness_component_tsb',
  rhr: 'readiness_component_rhr',
  hrv: 'readiness_component_hrv',
  sleep: 'readiness_component_sleep',
  atl_ctl: 'readiness_component_load',
}

const COMPONENT_TIP_KEYS = {
  tsb: 'readiness_tip_tsb',
  rhr: 'readiness_tip_rhr',
  hrv: 'readiness_tip_hrv',
  sleep: 'readiness_tip_sleep',
  atl_ctl: 'readiness_tip_load',
}

const DATE_COMPONENTS = new Set(['rhr', 'hrv', 'sleep'])

function scoreColor(score) {
  if (score >= 70) return '#2ecc40'
  if (score >= 45) return '#e6c820'
  return '#ff3b5c'
}

function scoreLabel(score, t) {
  if (score >= 75) return t('readiness_fresh')
  if (score >= 50) return t('readiness_moderate')
  if (score >= 25) return t('readiness_fatigued')
  return t('readiness_depleted')
}

function fmtCompDate(dateStr) {
  if (!dateStr) return ''
  try {
    const d = new Date(dateStr + 'T00:00:00')
    return d.toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit' })
  } catch { return dateStr }
}

// Arc zone definitions: [startPct, endPct, color]
const ZONES = [
  [0, 25, '#ff3b5c'],
  [25, 45, '#ff8c1a'],
  [45, 70, '#e6c820'],
  [70, 100, '#2ecc40'],
]

// 270° arc: starts at 135° (bottom-left), ends at 405° (bottom-right)
const ARC_START = 135
const ARC_SWEEP = 270

/**
 * Circular 270° readiness gauge with animated needle and score centered inside.
 * Props: { score: 0-100, components: {}, compact?: boolean }
 */
export default function ReadinessGauge({ score = 50, components = {}, compact = false, infoTip = '' }) {
  const { t } = useI18n()
  const animatedScore = useCountUp(score, { duration: 900, enabled: true })
  const color = scoreColor(score)

  // Use animated score for live color updates as needle moves
  const liveColor = scoreColor(animatedScore)

  const size = compact ? 220 : 260
  const strokeW = compact ? 14 : 18
  const arcRadius = size / 2 - strokeW / 2 - 4
  const cx = size / 2
  const cy = size / 2
  const needleLen = arcRadius - 6
  const needleAngle = ARC_START + (Math.max(0, Math.min(100, animatedScore)) / 100) * ARC_SWEEP
  const needleRad = (needleAngle * Math.PI) / 180
  const nx = cx + needleLen * Math.cos(needleRad)
  const ny = cy + needleLen * Math.sin(needleRad)
  // Needle base perpendicular
  const baseHalf = compact ? 3.5 : 4.5
  const perpRad = needleRad + Math.PI / 2
  const bx1 = cx + 8 * Math.cos(needleRad) + baseHalf * Math.cos(perpRad)
  const by1 = cy + 8 * Math.sin(needleRad) + baseHalf * Math.sin(perpRad)
  const bx2 = cx + 8 * Math.cos(needleRad) - baseHalf * Math.cos(perpRad)
  const by2 = cy + 8 * Math.sin(needleRad) - baseHalf * Math.sin(perpRad)

  return (
    <div className={`readiness-gauge${compact ? ' readiness-gauge-compact' : ''}`}>
      <svg
        width={size}
        height={size * 0.78}
        viewBox={`0 0 ${size} ${size * 0.78}`}
        className="readiness-gauge-svg"
      >
        {/* Background arc */}
        <path
          d={describeArc(cx, cy, arcRadius, ARC_START, ARC_START + ARC_SWEEP)}
          fill="none"
          stroke="var(--bg-3, #282e44)"
          strokeWidth={strokeW}
          strokeLinecap="butt"
        />
        {/* Color zones */}
        {ZONES.map(([startPct, endPct, zoneColor], i) => {
          const startAng = ARC_START + (startPct / 100) * ARC_SWEEP
          const endAng = ARC_START + (endPct / 100) * ARC_SWEEP
          return (
            <path
              key={i}
              d={describeArc(cx, cy, arcRadius, startAng, endAng)}
              fill="none"
              stroke={zoneColor}
              strokeWidth={strokeW - 2}
              strokeLinecap="butt"
              opacity={0.85}
            />
          )
        })}
        {/* Needle */}
        <polygon
          className="readiness-gauge-needle"
          points={`${bx1},${by1} ${nx},${ny} ${bx2},${by2}`}
          fill="#c8d3f5"
          opacity={0.9}
        />
        {/* Center pivot */}
        <circle cx={cx} cy={cy} r={compact ? 6 : 8} fill="var(--bg-2, #1e2030)" stroke="#c8d3f5" strokeWidth={2} />
      </svg>
      {/* Score + label below the gauge */}
      <div className="readiness-gauge-value">
        <span className="readiness-gauge-score" style={{ color: liveColor }}>{String(animatedScore)}</span>
        <span className="readiness-gauge-label" style={{ color: liveColor }}>{String(scoreLabel(score, t))}</span>
        {infoTip && <span className="readiness-gauge-info"><InfoTip text={infoTip} /></span>}
      </div>
      {!compact && Object.keys(components).length > 0 && (
        <div className="readiness-gauge-components">
          {Object.entries(components).map(([key, comp]) => (
            <div key={key} className="readiness-gauge-comp">
              <span className="readiness-gauge-comp-dot" style={{ background: scoreColor(comp.score) }} />
              <span className="readiness-gauge-comp-label">{t(COMPONENT_KEYS[key] || key)}</span>
              <span className="readiness-gauge-comp-score" style={{ color: scoreColor(comp.score) }}>
                {Math.round(comp.score)}
              </span>
              {DATE_COMPONENTS.has(key) && comp.date && (
                <span className="readiness-gauge-comp-date">{fmtCompDate(comp.date)}</span>
              )}
              <InfoTip text={t(COMPONENT_TIP_KEYS[key] || '')} />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// SVG arc path helper
function polarToCartesian(cx, cy, r, angleDeg) {
  const rad = (angleDeg * Math.PI) / 180
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) }
}

function describeArc(cx, cy, r, startAngle, endAngle) {
  const start = polarToCartesian(cx, cy, r, startAngle)
  const end = polarToCartesian(cx, cy, r, endAngle)
  const largeArc = (endAngle - startAngle) > 180 ? 1 : 0
  return `M ${start.x} ${start.y} A ${r} ${r} 0 ${largeArc} 1 ${end.x} ${end.y}`
}
