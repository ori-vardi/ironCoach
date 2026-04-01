export function classifyType(type) {
  const t = (type || '').toLowerCase()
  if (t.includes('running') || t.includes('walking')) return 'run'
  if (t.includes('cycling')) return 'bike'
  if (t.includes('swimming')) return 'swim'
  if (t.includes('strength')) return 'strength'
  return 'other'
}

export function hrZone(hr) {
  if (hr < 130) return 'Z1'
  if (hr < 143) return 'Z2'
  if (hr < 156) return 'Z3'
  if (hr < 169) return 'Z4'
  return 'Z5'
}

export function recoveryColor(score) {
  if (score >= 75) return '#c3e88d'
  if (score >= 50) return '#ffc777'
  if (score >= 25) return '#ff966c'
  return '#ff757f'
}

export function recoveryLabel(score) {
  if (score >= 75) return 'fresh'
  if (score >= 50) return 'moderate'
  if (score >= 25) return 'fatigued'
  return 'depleted'
}

export function statusColor(val, greenThresh, yellowThresh, higherIsBetter = true) {
  if (higherIsBetter) {
    if (val >= greenThresh) return '#c3e88d'
    if (val >= yellowThresh) return '#ffc777'
    return '#ff5370'
  }
  if (val <= greenThresh) return '#c3e88d'
  if (val <= yellowThresh) return '#ffc777'
  return '#ff5370'
}

export function fatigueColor(val, phase) {
  if (phase === 'build' || phase === 'mid') return statusColor(val, 80, 100, false)
  if (phase === 'taper') return statusColor(val, 50, 70, false)
  return statusColor(val, 40, 60, false)
}

/**
 * Calculate training phase from days until race.
 * Taper (14 days) and peak (14 days) are fixed — well-established in triathlon.
 * Build and mid split the remaining time proportionally (60/40).
 * Returns: 'build' | 'mid' | 'peak' | 'taper'
 */
export function trainingPhase(daysToRace) {
  if (daysToRace <= 14) return 'taper'       // Final 2 weeks — reduce volume
  if (daysToRace <= 28) return 'peak'        // 2-4 weeks out — highest intensity
  // Remaining days split: first ~60% = build, last ~40% = mid
  const remaining = daysToRace - 28
  const midDays = Math.max(14, Math.round(remaining * 0.4))
  if (daysToRace <= 28 + midDays) return 'mid'
  return 'build'
}

export function getEventTypeLabel(eventType) {
  switch (eventType) {
    case 'half_ironman': return '70.3'
    case 'ironman': return '140.6'
    case 'olympic_tri': return 'OD'
    case 'sprint_tri': return 'Sprint'
    case 'marathon': return '42K'
    case 'half_marathon': return '21K'
    case '10k': return '10K'
    case '5k': return '5K'
    default: return ''
  }
}
