import { useState, useEffect } from 'react'
import { api } from '../api'
import { fmtDate, autoGrow } from '../utils/formatters'
import { useI18n } from '../i18n/I18nContext'
import { useChat } from '../context/ChatContext'
import LoadingSpinner from '../components/common/LoadingSpinner'
import ConfirmDialog from '../components/common/ConfirmDialog'

const EVENT_TYPES = [
  'ironman', 'half_ironman', 'olympic_tri', 'sprint_tri',
  'marathon', 'half_marathon', '10k', '5k', 'custom'
]

const RUN_ONLY_TYPES = ['marathon', 'half_marathon', '10k', '5k']

export default function RacePage() {
  const { t } = useI18n()
  const { newSession, setChatOpen, setPendingInput } = useChat()
  const [events, setEvents] = useState([])
  const [presets, setPresets] = useState({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [formData, setFormData] = useState(getEmptyForm())
  const [formStep, setFormStep] = useState(0) // 0 = pick type, 1 = full form
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [confirmTarget, setConfirmTarget] = useState(null)
  const [primaryPrompt, setPrimaryPrompt] = useState(null) // event id to offer as primary

  function getEmptyForm() {
    return {
      event_name: '', event_type: 'custom', event_date: '',
      swim_km: '', bike_km: '', run_km: '',
      cutoff_swim: '', cutoff_bike: '', cutoff_finish: '',
      target_swim: '', target_bike: '', target_run: '', target_total: '',
      goal: '', notes: ''
    }
  }

  async function loadEvents() {
    try {
      const [eventsData, presetsData] = await Promise.all([
        api('/api/events'),
        api('/api/events/presets')
      ])
      setEvents(eventsData || [])
      setPresets(presetsData || {})
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadEvents()
    window.addEventListener('coach-data-update', loadEvents)
    return () => window.removeEventListener('coach-data-update', loadEvents)
  }, [])

  const primaryEvent = events.find(e => e.is_primary)

  function handleTypeChange(type) {
    setFormData(f => ({ ...f, event_type: type }))
    if (presets[type]) {
      const p = presets[type]
      setFormData(f => ({
        ...f,
        swim_km: p.swim_km || '',
        bike_km: p.bike_km || '',
        run_km: p.run_km || ''
      }))
    }
  }

  function handleEditClick(event) {
    setEditingId(event.id)
    setFormData({
      event_name: event.event_name,
      event_type: event.event_type,
      event_date: event.event_date,
      swim_km: event.swim_km || '',
      bike_km: event.bike_km || '',
      run_km: event.run_km || '',
      cutoff_swim: event.cutoff_swim || '',
      cutoff_bike: event.cutoff_bike || '',
      cutoff_finish: event.cutoff_finish || '',
      target_swim: event.target_swim || '',
      target_bike: event.target_bike || '',
      target_run: event.target_run || '',
      target_total: event.target_total || '',
      goal: event.goal || '',
      notes: event.notes || ''
    })
    setFormStep(1) // skip type picker when editing
    setShowForm(true)
  }

  function handleCancelEdit() {
    setEditingId(null)
    setFormData(getEmptyForm())
    setFormStep(0)
    setShowForm(false)
  }

  async function handleSaveEvent() {
    if (!formData.event_name?.trim()) { setError(t('events_name_required')); return }
    if (!formData.event_date) { setError(t('events_date_required')); return }
    setError(null)
    try {
      const isNew = !editingId
      let newEvent = null
      if (editingId) {
        await api(`/api/events/${editingId}`, {
          method: 'PUT',
          body: JSON.stringify(formData)
        })
      } else {
        newEvent = await api('/api/events', {
          method: 'POST',
          body: JSON.stringify(formData)
        })
      }
      const updated = await api('/api/events')
      setEvents(updated)
      handleCancelEdit()

      if (isNew && newEvent?.id) {
        const hasPrimary = updated.some(e => e.is_primary)
        if (!hasPrimary) {
          // First event — auto-set as primary
          await api(`/api/events/${newEvent.id}/primary`, { method: 'PUT' })
          const refreshed = await api('/api/events')
          setEvents(refreshed)
        } else {
          // Has existing primary — ask user
          setPrimaryPrompt(newEvent.id)
        }
      }

      window.dispatchEvent(new CustomEvent('coach-data-update'))
    } catch (e) {
      setError(e.message)
    }
  }

  function requestDelete(id) {
    setConfirmTarget(id)
    setConfirmOpen(true)
  }

  async function confirmDelete() {
    if (!confirmTarget) return
    try {
      await api(`/api/events/${confirmTarget}`, { method: 'DELETE' })
      const updated = await api('/api/events')
      setEvents(updated)
      window.dispatchEvent(new CustomEvent('coach-data-update'))
    } catch (e) {
      setError(e.message)
    } finally {
      setConfirmOpen(false)
      setConfirmTarget(null)
    }
  }

  async function handleSetPrimary(id) {
    try {
      await api(`/api/events/${id}/primary`, { method: 'PUT' })
      const updated = await api('/api/events')
      setEvents(updated)
      window.dispatchEvent(new CustomEvent('coach-data-update'))
    } catch (e) {
      setError(e.message)
    }
  }

  if (loading) return <LoadingSpinner />
  if (error) return <div className="loading-msg">Error: {error}</div>

  const isRunOnly = RUN_ONLY_TYPES.includes(formData.event_type)

  return (
    <>
      <h1 className="page-title">{t('events_title')}</h1>

      {/* Primary event countdown banner */}
      {primaryEvent && (
        <div className="race-countdown">
          <div className="countdown-number">
            {primaryEvent.days_until ?? '?'}
            <span className="countdown-days-label">{t('events_days_until')}</span>
          </div>
          <div className="countdown-label">
            <span dir="auto">{primaryEvent.event_name}</span> — {fmtDate(primaryEvent.event_date)}
          </div>
        </div>
      )}

      {/* Add Event button */}
      <div className="events-header">
        <button className="btn btn-accent" onClick={() => { setShowForm(true); setFormStep(0); setFormData(getEmptyForm()); }}>
          {t('events_add')}
        </button>
      </div>

      {/* Add/Edit Form */}
      {showForm && (
        <div className="event-form-container">
          <h3 style={{ marginBottom: 16 }}>
            {editingId ? t('events_edit') : formStep === 0 ? t('events_select_type') : t('events_create_event')}
          </h3>

          {formStep === 0 ? (
            <div className="event-type-picker">
              {EVENT_TYPES.map(type => (
                <button
                  key={type}
                  className={`btn event-type-btn${formData.event_type === type ? ' btn-accent' : ''}`}
                  onClick={() => {
                    handleTypeChange(type)
                    setFormStep(1)
                  }}
                >
                  {t(`events_type_${type}`) || type}
                </button>
              ))}
              <div style={{ marginTop: 12 }}>
                <button className="btn" onClick={handleCancelEdit}>{t('events_cancel_edit')}</button>
              </div>
            </div>
          ) : (
          <>
          <div className="event-form-grid">
            <div className="form-group">
              <label>{t('events_event_name')}</label>
              <input
                type="text"
                className="input-full"
                dir="auto"
                value={formData.event_name}
                onChange={e => setFormData(f => ({ ...f, event_name: e.target.value }))}
              />
            </div>

            <div className="form-group">
              <label>{t('events_event_type')}</label>
              <select
                className="input-full"
                value={formData.event_type}
                onChange={e => handleTypeChange(e.target.value)}
              >
                {EVENT_TYPES.map(type => (
                  <option key={type} value={type}>
                    {t(`events_type_${type}`) || type}
                  </option>
                ))}
              </select>
            </div>

            <div className="form-group">
              <label>{t('events_event_date')}</label>
              <input
                type="date"
                className="input-full"
                value={formData.event_date}
                onChange={e => setFormData(f => ({ ...f, event_date: e.target.value }))}
              />
            </div>

            {!isRunOnly && (
              <>
                <div className="form-group">
                  <label>{t('events_swim_km')}</label>
                  <input
                    type="number"
                    step="0.1"
                    className="input-full"
                    value={formData.swim_km}
                    onChange={e => setFormData(f => ({ ...f, swim_km: e.target.value }))}
                  />
                </div>

                <div className="form-group">
                  <label>{t('events_bike_km')}</label>
                  <input
                    type="number"
                    step="0.1"
                    className="input-full"
                    value={formData.bike_km}
                    onChange={e => setFormData(f => ({ ...f, bike_km: e.target.value }))}
                  />
                </div>
              </>
            )}

            <div className="form-group">
              <label>{t('events_run_km')}</label>
              <input
                type="number"
                step="0.1"
                className="input-full"
                value={formData.run_km}
                onChange={e => setFormData(f => ({ ...f, run_km: e.target.value }))}
              />
            </div>

            {!isRunOnly && (
              <>
                <div className="form-group">
                  <label>{t('events_cutoff_swim')}</label>
                  <input
                    type="text"
                    placeholder="h:mm"
                    className="input-full"
                    value={formData.cutoff_swim}
                    onChange={e => setFormData(f => ({ ...f, cutoff_swim: e.target.value }))}
                  />
                </div>

                <div className="form-group">
                  <label>{t('events_cutoff_bike')}</label>
                  <input
                    type="text"
                    placeholder="h:mm"
                    className="input-full"
                    value={formData.cutoff_bike}
                    onChange={e => setFormData(f => ({ ...f, cutoff_bike: e.target.value }))}
                  />
                </div>
              </>
            )}

            <div className="form-group">
              <label>{t('events_cutoff_finish')}</label>
              <input
                type="text"
                placeholder="h:mm"
                className="input-full"
                value={formData.cutoff_finish}
                onChange={e => setFormData(f => ({ ...f, cutoff_finish: e.target.value }))}
              />
            </div>

            {!isRunOnly && (
              <>
                <div className="form-group">
                  <label>{t('events_target_swim')}</label>
                  <input
                    type="text"
                    placeholder="h:mm"
                    className="input-full"
                    value={formData.target_swim}
                    onChange={e => setFormData(f => ({ ...f, target_swim: e.target.value }))}
                  />
                </div>

                <div className="form-group">
                  <label>{t('events_target_bike')}</label>
                  <input
                    type="text"
                    placeholder="h:mm"
                    className="input-full"
                    value={formData.target_bike}
                    onChange={e => setFormData(f => ({ ...f, target_bike: e.target.value }))}
                  />
                </div>
              </>
            )}

            <div className="form-group">
              <label>{t('events_target_run')}</label>
              <input
                type="text"
                placeholder="h:mm"
                className="input-full"
                value={formData.target_run}
                onChange={e => setFormData(f => ({ ...f, target_run: e.target.value }))}
              />
            </div>

            <div className="form-group">
              <label>{t('events_target_total')}</label>
              <input
                type="text"
                placeholder="h:mm"
                className="input-full"
                value={formData.target_total}
                onChange={e => setFormData(f => ({ ...f, target_total: e.target.value }))}
              />
            </div>

            <div className="form-group event-form-full">
              <label>{t('events_goal')}</label>
              <input
                type="text"
                className="input-full"
                dir="auto"
                value={formData.goal}
                onChange={e => setFormData(f => ({ ...f, goal: e.target.value }))}
              />
            </div>

            <div className="form-group event-form-full">
              <label>{t('events_notes')}</label>
              <textarea
                rows={4}
                className="input-full"
                dir="auto"
                value={formData.notes}
                onChange={e => setFormData(f => ({ ...f, notes: e.target.value }))}
                onInput={autoGrow}
                style={{ overflow: 'hidden' }}
              />
            </div>
          </div>

          <div className="event-form-actions">
            <button className="btn" onClick={handleCancelEdit}>
              {t('events_cancel_edit')}
            </button>
            <button className="btn btn-accent" onClick={handleSaveEvent}>
              {editingId ? t('events_edit') : t('events_create_event')}
            </button>
            <button className="btn btn-coach" onClick={() => {
              const etype = t(`events_type_${formData.event_type}`) || formData.event_type
              const parts = []
              if (formData.event_name) parts.push(`Event: ${formData.event_name}`)
              parts.push(`Type: ${etype}`)
              if (formData.event_date) parts.push(`Date: ${formData.event_date}`)
              if (formData.swim_km) parts.push(`Swim: ${formData.swim_km}km`)
              if (formData.bike_km) parts.push(`Bike: ${formData.bike_km}km`)
              if (formData.run_km) parts.push(`Run: ${formData.run_km}km`)
              if (formData.goal) parts.push(`Goal: ${formData.goal}`)
              const ctx = parts.join(', ')
              const msg = editingId
                ? `I'm editing my event ID ${editingId} (${ctx}). Help me set realistic targets, cutoffs, and a training plan. Save changes when we're done.`
                : `I'm planning a new event (${ctx}). Help me define goals, targets, and a training plan. Save it when we're done.`
              newSession('main-coach')
              setPendingInput(msg)
              setChatOpen(true)
            }}>
              {t('events_plan_with_coach')}
            </button>
          </div>
          </>
          )}
        </div>
      )}

      {/* Events Grid */}
      {events.length === 0 ? (
        <p className="text-dim">{t('events_no_events')}</p>
      ) : (
        <div className="events-grid">
          {events.map(event => {
            const isRunOnlyEvent = RUN_ONLY_TYPES.includes(event.event_type)
            return (
              <div key={event.id} className={`event-card card ${!!event.is_primary ? 'primary' : ''}`}>
                <div className="event-card-header">
                  <div className="event-card-title" dir="auto">{event.event_name}</div>
                  <div className="event-badges">
                    {!!event.is_primary && (
                      <span className="event-badge primary-badge">{t('events_primary')}</span>
                    )}
                    <span className="event-badge type">
                      {t(`events_type_${event.event_type}`) || event.event_type}
                    </span>
                  </div>
                </div>

                {event.event_date && (
                  <div className="event-info">
                    <div className="event-info-label">{t('th_date')}</div>
                    <div>{fmtDate(event.event_date)}</div>
                  </div>
                )}

                {event.event_date && event.days_until != null && (
                  <div className="event-info">
                    <div className="event-info-label">{t('events_countdown')}</div>
                    <div style={{ fontSize: 18, fontWeight: 600, color: 'var(--accent)' }}>
                      {event.days_until}
                    </div>
                  </div>
                )}

                {!isRunOnlyEvent ? (
                  <div className="event-distances">
                    {event.swim_km && (
                      <div className="event-distance-item">
                        <div className="event-distance-value">{event.swim_km}</div>
                        <div className="event-distance-label" style={{ color: 'var(--swim)' }}>
                          Swim (km)
                        </div>
                      </div>
                    )}
                    {event.bike_km && (
                      <div className="event-distance-item">
                        <div className="event-distance-value">{event.bike_km}</div>
                        <div className="event-distance-label" style={{ color: 'var(--bike)' }}>
                          Bike (km)
                        </div>
                      </div>
                    )}
                    {event.run_km && (
                      <div className="event-distance-item">
                        <div className="event-distance-value">{event.run_km}</div>
                        <div className="event-distance-label" style={{ color: 'var(--run)' }}>
                          Run (km)
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="event-distances">
                    {event.run_km && (
                      <div className="event-distance-item" style={{ gridColumn: '1 / -1' }}>
                        <div className="event-distance-value">{event.run_km}</div>
                        <div className="event-distance-label" style={{ color: 'var(--run)' }}>
                          Run (km)
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {event.goal && (
                  <div className="event-info">
                    <div className="event-info-label">{t('events_goal')}</div>
                    <div dir="auto">{event.goal}</div>
                  </div>
                )}

                {/* Targets */}
                {(event.target_swim || event.target_bike || event.target_run || event.target_total) && (
                  <div className="event-info">
                    <div className="event-info-label">{t('events_targets')}</div>
                    <div className="event-targets-row">
                      {event.target_swim && <span style={{ color: 'var(--swim)' }}>S: {event.target_swim}</span>}
                      {event.target_bike && <span style={{ color: 'var(--bike)' }}>B: {event.target_bike}</span>}
                      {event.target_run && <span style={{ color: 'var(--run)' }}>R: {event.target_run}</span>}
                      {event.target_total && <span><strong>{event.target_total}</strong></span>}
                    </div>
                  </div>
                )}

                {/* Cutoffs */}
                {(event.cutoff_swim || event.cutoff_bike || event.cutoff_finish) && (
                  <div className="event-info">
                    <div className="event-info-label">{t('events_cutoffs')}</div>
                    <div className="event-targets-row text-dim">
                      {event.cutoff_swim && <span>S: {event.cutoff_swim}</span>}
                      {event.cutoff_bike && <span>B: {event.cutoff_bike}</span>}
                      {event.cutoff_finish && <span>Finish: {event.cutoff_finish}</span>}
                    </div>
                  </div>
                )}

                {event.notes && (
                  <div className="event-info">
                    <div className="event-info-label">{t('events_notes')}</div>
                    <div className="text-sm text-dim" dir="auto">{event.notes}</div>
                  </div>
                )}

                <div className="event-card-actions">
                  <button className="btn btn-sm" onClick={() => handleEditClick(event)}>
                    {t('events_edit')}
                  </button>
                  <button className="btn btn-sm btn-coach" onClick={() => {
                    const etype = t(`events_type_${event.event_type}`) || event.event_type
                    newSession('main-coach')
                    setPendingInput(`Help me create a training plan for ${event.event_name} (${etype}) on ${event.event_date}. My goal: ${event.goal || 'finish strong'}.`)
                    setChatOpen(true)
                  }}>
                    {t('events_plan_with_coach')}
                  </button>
                  {!event.is_primary && (
                    <button className="btn btn-sm" onClick={() => handleSetPrimary(event.id)}>
                      {t('events_set_primary')}
                    </button>
                  )}
                  <button className="btn btn-sm btn-red" onClick={() => requestDelete(event.id)}>
                    {t('del')}
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
      <ConfirmDialog
        open={confirmOpen}
        title={t('delete_event')}
        message={t('delete_event_confirm')}
        onConfirm={confirmDelete}
        onCancel={() => { setConfirmOpen(false); setConfirmTarget(null) }}
      />
      <ConfirmDialog
        open={!!primaryPrompt}
        title={t('events_set_primary')}
        message={t('events_set_primary_prompt')}
        danger={false}
        confirmLabel={t('yes')}
        onConfirm={async () => {
          await handleSetPrimary(primaryPrompt)
          setPrimaryPrompt(null)
        }}
        onCancel={() => setPrimaryPrompt(null)}
      />
    </>
  )
}
