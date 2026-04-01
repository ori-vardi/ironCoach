import { useI18n } from '../../i18n/I18nContext'
import { computeRaceTsbData } from '../../utils/formatters'

export default function RaceReadinessBar({ event, tsb, compact = false }) {
  const { t } = useI18n()
  const daysToRace = event.days_until ?? Math.ceil((new Date(event.event_date) - new Date()) / (1000 * 60 * 60 * 24))
  const eventLabel = event.event_name || event.event_type || t('race')
  const dateStr = new Date(event.event_date).toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit', year: 'numeric' })
  const { tsbPct, tsbZone, recKey, recColor } = computeRaceTsbData(tsb, daysToRace)

  return (
    <div className={`race-readiness-bar${compact ? '' : ' mt-12'}`} style={compact ? { marginBottom: 8 } : undefined}>
      <div
        className={compact ? undefined : 'race-days'}
        style={compact
          ? { display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 4, direction: 'ltr', unicodeBidi: 'isolate', flexWrap: 'wrap' }
          : { marginBottom: 4, direction: 'ltr', unicodeBidi: 'isolate' }
        }
      >
        <strong dir="auto" style={compact ? { fontSize: 15 } : undefined}>{eventLabel}</strong>
        {!compact && <span className="text-dim text-sm" style={{ marginInlineStart: 8 }}>{dateStr}</span>}
        <span style={compact ? { color: 'var(--accent)' } : { marginInlineStart: 8 }}>
          {daysToRace > 0 ? `${daysToRace}${compact ? 'd' : ' days to race'}` : 'Race day!'}
        </span>
        {!!event.is_primary && <span style={{ marginInlineStart: 8, color: 'var(--yellow)', fontSize: '0.75rem' }}>PRIMARY</span>}
      </div>
      <div className="race-tsb-bar-wrapper" style={{ marginTop: compact ? 8 : 14 }}>
        <div className="race-tsb-marker" style={{ left: `${tsbPct}%` }} title={`TSB: ${tsb} (${tsbZone})`} />
        <div className="race-tsb-bar">
          <div className="race-tsb-zone zone-building" style={{ flex: 25 }} title={t('tsb_zone_building')}>{t('building')}</div>
          <div className="race-tsb-zone zone-maintaining" style={{ flex: 25 }} title={t('tsb_zone_maintaining')}>{t('maintaining')}</div>
          <div className="race-tsb-zone zone-tapering" style={{ flex: 25 }} title={t('tsb_zone_tapering')}>{t('tapering')}</div>
          <div className="race-tsb-zone zone-peaked" style={{ flex: 25 }} title={t('tsb_zone_peaked')}>{t('peaked')}</div>
        </div>
      </div>
      <div className="race-tsb-recommendation" style={{ borderInlineStart: `3px solid ${recColor}`, marginTop: compact ? 8 : undefined }} dir="auto">
        {t(recKey)}
      </div>
    </div>
  )
}
