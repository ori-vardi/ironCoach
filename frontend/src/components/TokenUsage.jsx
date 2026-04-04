import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react'
import { useAuth } from '../context/AuthContext'
import { useI18n } from '../i18n/I18nContext'
import { api } from '../api'
import Modal from './common/Modal'
import InfoTip from './common/InfoTip'
import { formatCost, formatTokens, shortModel } from '../utils/formatters'
import { AGENT_LABELS } from '../utils/constants'

const DAILY_INLINE_LIMIT = 5
const CACHE_STALE_TIME_MS = 30000

const CACHE_READ_TIP = "**Cache Read** — Prompt Caching\nTokens reused from previous turns in the same session.\n\n**Cost:** 10% of the model's base input price (90% discount).\n\n**How it works:** Each message you send includes the full conversation history. Tokens from previous turns are already cached — Claude reads them from cache instead of re-processing.\n\n**Per turn:** previous history = cache read (cheap), your new message = cache write (one-time).\n\n**Benefit:** Longer sessions = more cache hits = lower cost per call.\n\n**Resets on:** session rotation (at 400KB) or new session.\n\n**Docs:** docs.anthropic.com/en/docs/build-with-claude/prompt-caching"
const CACHE_WRITE_TIP = "**Cache Write** — Prompt Caching\nNew tokens written to cache for the first time.\n\n**Cost:** 125% of the model's base input price (25% surcharge, one-time).\n\n**How it works:** Each turn, the new content (your message + response) is written to cache. On the next turn, those tokens become cache reads at 90% discount.\n\n**Per turn:** both cache read AND cache write happen — read for old turns, write for the new one.\n\n**Benefit:** Small incremental cost per turn, big savings on all subsequent turns.\n\n**Resets on:** session rotation (at 400KB) starts fresh.\n\n**Docs:** docs.anthropic.com/en/docs/build-with-claude/prompt-caching"

function CacheHeaders() {
  return <>
    <th style={{ textAlign: 'end', whiteSpace: 'nowrap' }}>
      Cache Read <InfoTip text={CACHE_READ_TIP} />
    </th>
    <th style={{ textAlign: 'end', whiteSpace: 'nowrap' }}>
      Cache Write <InfoTip text={CACHE_WRITE_TIP} />
    </th>
  </>
}

function ModelBadge({ model, currentModel }) {
  const isCurrent = currentModel && model && (model === currentModel || model.includes(currentModel) || currentModel.includes(shortModel(model)))
  return (
    <span style={{ fontSize: 10, color: isCurrent ? 'var(--accent)' : 'var(--text-dim)', marginInlineStart: 4 }}>
      {shortModel(model)}{isCurrent ? ' *' : ''}
    </span>
  )
}

export default function TokenUsage() {
  const { user } = useAuth()
  const { t } = useI18n()
  const [usage, setUsage] = useState(null)
  const [showDetail, setShowDetail] = useState(false)
  const [daily, setDaily] = useState([])
  const [detailModal, setDetailModal] = useState(false)
  const [detailTab, setDetailTab] = useState('agent') // 'agent' | 'daily' | 'model'
  const [byAgent, setByAgent] = useState([])
  const [currentModel, setCurrentModel] = useState('')
  const [byModel, setByModel] = useState([])
  const [expandedDate, setExpandedDate] = useState(null)
  const [dateAgents, setDateAgents] = useState([])
  const detailRef = useRef(null)
  const cachedDataRef = useRef({ byAgent: null, daily: null, byModel: null, timestamp: 0 })

  const fetchUsage = useCallback(async () => {
    if (!user) return
    try {
      const data = await api('/api/usage')
      setUsage(data)
    } catch {}
  }, [user])

  const displayCost = useMemo(() => formatCost(usage?.total_cost || 0), [usage?.total_cost])
  const displayCalls = useMemo(() => usage?.total_calls || 0, [usage?.total_calls])
  const displayTokens = useMemo(() =>
    formatTokens((usage?.total_input || 0) + (usage?.total_output || 0)),
    [usage?.total_input, usage?.total_output]
  )

  // Fetch once on mount, then only on LLM events — no polling (zero cost)
  useEffect(() => {
    fetchUsage()
    const onUpdate = () => fetchUsage()
    window.addEventListener('token-usage-update', onUpdate)
    window.addEventListener('llm-task-end', onUpdate)
    return () => {
      window.removeEventListener('token-usage-update', onUpdate)
      window.removeEventListener('llm-task-end', onUpdate)
    }
  }, [fetchUsage])

  // Close detail dropdown on outside click
  useEffect(() => {
    if (!showDetail) return
    const handler = (e) => {
      if (detailRef.current && !detailRef.current.contains(e.target)) setShowDetail(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showDetail])

  const toggleDetail = async () => {
    if (!showDetail) {
      try {
        const [fresh, dailyData] = await Promise.all([
          api('/api/usage'),
          api('/api/usage/daily?from_date=' + new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10))
        ])
        setUsage(fresh)
        setDaily(dailyData)
      } catch {}
    }
    setShowDetail(p => !p)
  }

  const openDetails = async () => {
    setShowDetail(false)
    setExpandedDate(null)
    setDateAgents([])
    const now = Date.now()
    const cache = cachedDataRef.current
    if (cache.byAgent && cache.daily && cache.byModel && (now - cache.timestamp < CACHE_STALE_TIME_MS)) {
      setByAgent(cache.byAgent)
      setCurrentModel(cache.currentModel || '')
      setDaily(cache.daily)
      setByModel(cache.byModel)
      setDetailModal(true)
      return
    }
    try {
      const [agentResp, dailyData, modelData] = await Promise.all([
        api('/api/usage/by-agent'),
        api('/api/usage/daily?from_date=' + new Date(Date.now() - 90 * 86400000).toISOString().slice(0, 10)),
        api('/api/usage/by-model'),
      ])
      const rows = agentResp.rows || []
      const model = agentResp.current_model || ''
      setByAgent(rows)
      setCurrentModel(model)
      setDaily(dailyData)
      setByModel(modelData)
      cachedDataRef.current = { byAgent: rows, currentModel: model, daily: dailyData, byModel: modelData, timestamp: now }
    } catch {}
    setDetailModal(true)
  }

  const toggleDailyExpand = async (date) => {
    if (expandedDate === date) {
      setExpandedDate(null)
      setDateAgents([])
      return
    }
    try {
      const resp = await api(`/api/usage/daily-agents?date=${date}`)
      setDateAgents(resp.rows || [])
      if (resp.current_model) setCurrentModel(resp.current_model)
      setExpandedDate(date)
    } catch {}
  }

  if (!user) return null

  return (
    <div className="token-usage-wrapper" ref={detailRef}>
      <button
        className="token-usage-btn"
        onClick={toggleDetail}
        title={(t('token_usage_title') || 'API Usage') + ' — ' + (t('token_cost_estimate_note') || 'Estimated costs, may differ from actual billing')}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
        </svg>
        <span className="token-usage-cost">~{displayCost}</span>
        {displayCalls > 0 && <span className="token-usage-tokens">{displayTokens}t</span>}
      </button>

      {showDetail && (
        <div className="token-usage-dropdown">
          <div className="token-usage-header">{t('token_usage_title') || 'LLM API Usage'} <span style={{ fontSize: 10, fontWeight: 400, color: 'var(--yellow)' }}>({t('token_cost_estimate_short') || 'estimated'})</span></div>
          <div className="token-usage-summary">
            <div className="token-usage-row">
              <span>{t('token_total_cost') || 'Total Cost'}</span>
              <span className="token-usage-val">{displayCost}</span>
            </div>
            <div className="token-usage-row">
              <span>{t('token_api_calls') || 'LLM Calls'}</span>
              <span className="token-usage-val">{displayCalls}</span>
            </div>
            {displayCalls > 0 && <>
              <div className="token-usage-row">
                <span>{t('token_input') || 'Input Tokens'}</span>
                <span className="token-usage-val">{formatTokens(usage.total_input)}</span>
              </div>
              <div className="token-usage-row">
                <span>{t('token_output') || 'Output Tokens'}</span>
                <span className="token-usage-val">{formatTokens(usage.total_output)}</span>
              </div>
            </>}
          </div>
          {daily.length > 0 && (
            <>
              <div className="token-usage-header" style={{ marginTop: 8 }}>{t('token_daily') || 'Daily'}</div>
              <div className="token-usage-daily">
                {daily.slice(0, DAILY_INLINE_LIMIT).map(d => (
                  <div key={d.date} className="token-usage-row">
                    <span>{d.date.slice(5)}</span>
                    <span className="token-usage-val">{formatCost(d.cost_usd)} <span style={{ color: 'var(--text-dim)', fontSize: 10 }}>({d.calls})</span></span>
                  </div>
                ))}
              </div>
            </>
          )}
          <button className="btn btn-sm" style={{ width: '100%', marginTop: 8, fontSize: 11 }} onClick={openDetails}>
            Details
          </button>
          {displayCalls === 0 && (
            <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 6 }}>
              {t('token_no_usage') || 'No usage yet. Send a chat message or generate insights.'}
            </div>
          )}
        </div>
      )}

      {detailModal && (
        <Modal title={`${t('token_usage_title') || 'LLM Usage'} — Details`} onClose={() => setDetailModal(false)} wide>
          <div style={{ fontSize: 11, color: 'var(--yellow)', marginBottom: 8 }}>
            {t('token_cost_estimate_note') || 'Costs shown are estimates based on published token prices and may not reflect actual billing.'}
          </div>
          {/* Current model indicator */}
          {currentModel && (
            <div style={{ fontSize: 11, color: 'var(--text-dim)', marginBottom: 8 }}>
              Active model: <strong style={{ color: 'var(--accent)' }}>{shortModel(currentModel)}</strong> <span style={{ color: 'var(--accent)' }}>*</span>
            </div>
          )}

          {/* Tabs */}
          <div style={{ display: 'flex', gap: 4, marginBottom: 12, borderBottom: '1px solid var(--border)', paddingBottom: 8 }}>
            <button
              className={`btn btn-sm${detailTab === 'agent' ? ' btn-accent' : ''}`}
              onClick={() => setDetailTab('agent')}
            >
              Per Agent
            </button>
            <button
              className={`btn btn-sm${detailTab === 'daily' ? ' btn-accent' : ''}`}
              onClick={() => { setDetailTab('daily'); setExpandedDate(null) }}
            >
              Daily
            </button>
            <button
              className={`btn btn-sm${detailTab === 'model' ? ' btn-accent' : ''}`}
              onClick={() => setDetailTab('model')}
            >
              Per Model
            </button>
          </div>

          {/* Per Agent tab — grouped by agent+model */}
          {detailTab === 'agent' && byAgent.length > 0 && (
            <div style={{ maxHeight: '55vh', overflowY: 'auto' }}>
              <table className="data-table" style={{ fontSize: 12 }}>
                <thead>
                  <tr>
                    <th>Agent</th>
                    <th style={{ textAlign: 'end' }}>Model</th>
                    <th style={{ textAlign: 'end' }}>Cost</th>
                    <th style={{ textAlign: 'end' }}>Calls</th>
                    <th style={{ textAlign: 'end' }}>Input</th>
                    <th style={{ textAlign: 'end' }}>Output</th>
                    <CacheHeaders />
                  </tr>
                </thead>
                <tbody>
                  {byAgent.map(a => {
                    const pct = (usage?.total_cost || 0) > 0 ? (a.cost / usage.total_cost * 100).toFixed(0) : 0
                    return (
                      <tr key={`${a.agent}-${a.model}`}>
                        <td>
                          {AGENT_LABELS[a.agent] || a.agent || 'Unknown'}
                          <span style={{ color: 'var(--text-dim)', fontSize: 10, marginInlineStart: 4 }}>{pct}%</span>
                        </td>
                        <td style={{ textAlign: 'end' }}>
                          <ModelBadge model={a.model} currentModel={currentModel} />
                        </td>
                        <td style={{ textAlign: 'end' }}>{formatCost(a.cost)}</td>
                        <td style={{ textAlign: 'end' }}>{a.calls}</td>
                        <td style={{ textAlign: 'end' }}>{formatTokens(a.input_tokens || 0)}</td>
                        <td style={{ textAlign: 'end' }}>{formatTokens(a.output_tokens || 0)}</td>
                        <td style={{ textAlign: 'end' }}>{formatTokens(a.cache_read || 0)}</td>
                        <td style={{ textAlign: 'end' }}>{formatTokens(a.cache_write || 0)}</td>
                      </tr>
                    )
                  })}
                </tbody>
                <tfoot>
                  <tr style={{ fontWeight: 600 }}>
                    <td>Total</td>
                    <td></td>
                    <td style={{ textAlign: 'end' }}>{formatCost(byAgent.reduce((s, a) => s + a.cost, 0))}</td>
                    <td style={{ textAlign: 'end' }}>{byAgent.reduce((s, a) => s + a.calls, 0)}</td>
                    <td style={{ textAlign: 'end' }}>{formatTokens(byAgent.reduce((s, a) => s + (a.input_tokens || 0), 0))}</td>
                    <td style={{ textAlign: 'end' }}>{formatTokens(byAgent.reduce((s, a) => s + (a.output_tokens || 0), 0))}</td>
                    <td style={{ textAlign: 'end' }}>{formatTokens(byAgent.reduce((s, a) => s + (a.cache_read || 0), 0))}</td>
                    <td style={{ textAlign: 'end' }}>{formatTokens(byAgent.reduce((s, a) => s + (a.cache_write || 0), 0))}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          )}
          {detailTab === 'agent' && byAgent.length === 0 && (
            <div className="text-dim" style={{ fontSize: 12 }}>No usage data yet.</div>
          )}

          {/* Daily tab */}
          {detailTab === 'daily' && daily.length > 0 && (
            <div style={{ maxHeight: '55vh', overflowY: 'auto' }}>
              <table className="data-table" style={{ fontSize: 12 }}>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th style={{ textAlign: 'end' }}>Cost</th>
                    <th style={{ textAlign: 'end' }}>Calls</th>
                    <th style={{ textAlign: 'end' }}>Input</th>
                    <th style={{ textAlign: 'end' }}>Output</th>
                    <CacheHeaders />
                  </tr>
                </thead>
                <tbody>
                  {daily.map(d => (<React.Fragment key={d.date}>
                    <tr
                      onClick={() => toggleDailyExpand(d.date)}
                      style={{ cursor: 'pointer' }}
                      className={expandedDate === d.date ? 'row-expanded' : ''}
                    >
                      <td>{expandedDate === d.date ? '▾' : '▸'} {d.date}</td>
                      <td style={{ textAlign: 'end' }}>{formatCost(d.cost_usd)}</td>
                      <td style={{ textAlign: 'end' }}>{d.calls}</td>
                      <td style={{ textAlign: 'end' }}>{formatTokens(d.input_tokens || 0)}</td>
                      <td style={{ textAlign: 'end' }}>{formatTokens(d.output_tokens || 0)}</td>
                      <td style={{ textAlign: 'end' }}>{formatTokens(d.cache_read_tokens || 0)}</td>
                      <td style={{ textAlign: 'end' }}>{formatTokens(d.cache_creation_tokens || 0)}</td>
                    </tr>
                    {expandedDate === d.date && dateAgents.map(a => (
                      <tr key={`${d.date}-${a.agent}-${a.model}`} style={{ background: 'var(--bg-1)', fontSize: 11 }}>
                        <td style={{ paddingInlineStart: 24, color: 'var(--text-dim)' }}>
                          {AGENT_LABELS[a.agent] || a.agent || 'Unknown'}
                          <ModelBadge model={a.model} currentModel={currentModel} />
                        </td>
                        <td style={{ textAlign: 'end' }}>{formatCost(a.cost)}</td>
                        <td style={{ textAlign: 'end' }}>{a.calls}</td>
                        <td style={{ textAlign: 'end' }}>{formatTokens(a.input_tokens || 0)}</td>
                        <td style={{ textAlign: 'end' }}>{formatTokens(a.output_tokens || 0)}</td>
                        <td style={{ textAlign: 'end' }}>{formatTokens(a.cache_read || 0)}</td>
                        <td style={{ textAlign: 'end' }}>{formatTokens(a.cache_write || 0)}</td>
                      </tr>
                    ))}
                  </React.Fragment>))}
                </tbody>
                <tfoot>
                  <tr style={{ fontWeight: 600 }}>
                    <td>Total</td>
                    <td style={{ textAlign: 'end' }}>{formatCost(daily.reduce((s, d) => s + d.cost_usd, 0))}</td>
                    <td style={{ textAlign: 'end' }}>{daily.reduce((s, d) => s + d.calls, 0)}</td>
                    <td style={{ textAlign: 'end' }}>{formatTokens(daily.reduce((s, d) => s + (d.input_tokens || 0), 0))}</td>
                    <td style={{ textAlign: 'end' }}>{formatTokens(daily.reduce((s, d) => s + (d.output_tokens || 0), 0))}</td>
                    <td style={{ textAlign: 'end' }}>{formatTokens(daily.reduce((s, d) => s + (d.cache_read_tokens || 0), 0))}</td>
                    <td style={{ textAlign: 'end' }}>{formatTokens(daily.reduce((s, d) => s + (d.cache_creation_tokens || 0), 0))}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          )}
          {detailTab === 'daily' && daily.length === 0 && (
            <div className="text-dim" style={{ fontSize: 12 }}>No daily data yet.</div>
          )}

          {/* Per Model tab */}
          {detailTab === 'model' && byModel.length > 0 && (
            <div style={{ maxHeight: '55vh', overflowY: 'auto' }}>
              <table className="data-table" style={{ fontSize: 12 }}>
                <thead>
                  <tr>
                    <th>Model</th>
                    <th style={{ textAlign: 'end' }}>Cost</th>
                    <th style={{ textAlign: 'end' }}>Calls</th>
                    <th style={{ textAlign: 'end' }}>Input</th>
                    <th style={{ textAlign: 'end' }}>Output</th>
                    <CacheHeaders />
                  </tr>
                </thead>
                <tbody>
                  {byModel.map(m => {
                    const pct = (usage?.total_cost || 0) > 0 ? (m.cost / usage.total_cost * 100).toFixed(0) : 0
                    return (
                      <tr key={m.model}>
                        <td>
                          <ModelBadge model={m.model} currentModel={currentModel} />
                          <span style={{ color: 'var(--text-dim)', fontSize: 10, marginInlineStart: 4 }}>{pct}%</span>
                        </td>
                        <td style={{ textAlign: 'end' }}>{formatCost(m.cost)}</td>
                        <td style={{ textAlign: 'end' }}>{m.calls}</td>
                        <td style={{ textAlign: 'end' }}>{formatTokens(m.input_tokens || 0)}</td>
                        <td style={{ textAlign: 'end' }}>{formatTokens(m.output_tokens || 0)}</td>
                        <td style={{ textAlign: 'end' }}>{formatTokens(m.cache_read || 0)}</td>
                        <td style={{ textAlign: 'end' }}>{formatTokens(m.cache_write || 0)}</td>
                      </tr>
                    )
                  })}
                </tbody>
                {byModel.length > 1 && (
                  <tfoot>
                    <tr style={{ fontWeight: 600 }}>
                      <td>Total</td>
                      <td style={{ textAlign: 'end' }}>{formatCost(byModel.reduce((s, m) => s + m.cost, 0))}</td>
                      <td style={{ textAlign: 'end' }}>{byModel.reduce((s, m) => s + m.calls, 0)}</td>
                      <td style={{ textAlign: 'end' }}>{formatTokens(byModel.reduce((s, m) => s + (m.input_tokens || 0), 0))}</td>
                      <td style={{ textAlign: 'end' }}>{formatTokens(byModel.reduce((s, m) => s + (m.output_tokens || 0), 0))}</td>
                      <td style={{ textAlign: 'end' }}>{formatTokens(byModel.reduce((s, m) => s + (m.cache_read || 0), 0))}</td>
                      <td style={{ textAlign: 'end' }}>{formatTokens(byModel.reduce((s, m) => s + (m.cache_write || 0), 0))}</td>
                    </tr>
                  </tfoot>
                )}
              </table>
            </div>
          )}
          {detailTab === 'model' && byModel.length === 0 && (
            <div className="text-dim" style={{ fontSize: 12 }}>No usage data yet.</div>
          )}
        </Modal>
      )}
    </div>
  )
}
