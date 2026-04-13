import useCountUp from '../../utils/useCountUp'
import InfoTip from './InfoTip'

/**
 * KPI card with optional count-up animation and vertical fill bar.
 * Props:
 *   animate   - numeric value to count up to (omit for static display)
 *   fillPct   - 0-100, shows a vertical fill bar behind the value
 *   fillColor - color for the fill bar (default: accent)
 */
export default function KpiCard({ value, label, sublabel, info, infoChildren, className = '', style = {}, animate, fillPct, fillColor, onClick }) {
  const animatedNum = useCountUp(animate, { duration: 900, enabled: animate != null })
  const displayValue = animate != null ? animatedNum : value

  return (
    <div className={`card kpi-card ${className}`} style={{ ...(style?.color ? { '--kpi-accent': style.color } : {}), ...(onClick ? { cursor: 'pointer' } : {}) }} onClick={onClick}>
      {info && <div className="kpi-info-corner"><InfoTip text={info}>{infoChildren}</InfoTip></div>}
      {fillPct > 0 && (
        <div
          className="kpi-fill-bar"
          style={{
            height: `${Math.min(fillPct, 100)}%`,
            '--kpi-fill-color': fillColor || 'var(--accent)',
          }}
        />
      )}
      <div className="kpi-value" style={style}>{displayValue}</div>
      <div className="kpi-label">
        {label}
        {sublabel && <><br /><span className="text-sm text-dim">{sublabel}</span></>}
      </div>
    </div>
  )
}
