import { useState, useEffect, useRef, useCallback } from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { api } from '../../api'
import { uploadFilesToServer, handleFilePaste, detectDir } from '../../utils/formatters'
import { useChat } from '../../context/ChatContext'
import { useAuth } from '../../context/AuthContext'
import { useApp } from '../../context/AppContext'
import ChatMessage from './ChatMessage'
import { notifyLlmStart, notifyLlmEnd } from '../NotificationBell'
import { useI18n } from '../../i18n/I18nContext'
import InfoTip from '../common/InfoTip'
import { AGENT_LABELS, getAgentLabels } from '../../utils/constants'

let _lastInputDir = null
function inputDirAtCursor(text, _cursorPos, lang) {
  const langDir = lang === 'he' ? 'rtl' : 'ltr'
  if (!text) return _lastInputDir || langDir
  const detected = detectDir(text)
  if (detected) _lastInputDir = detected
  return detected || _lastInputDir || langDir
}

function formatSessionDate(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: '2-digit' }) +
    ' ' + d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })
}

export default function ChatPanel() {
  const { t, lang } = useI18n()
  const { user } = useAuth()
  const { aiEnabled } = useApp()
  const {
    chatMode,
    sessionId, sessionAgent, setSessionAgent,
    messages, setMessages,
    streamingSessions, startStreaming, stopStreaming,
    attachedFiles, setAttachedFiles,
    chatOpen, setChatOpen,
    sessions, newSession, switchSession, deleteSession, renameSession, loadSessions,
    pendingScrollIndex, setPendingScrollIndex,
    pendingInput, setPendingInput,
  } = useChat()

  // Per-session draft helpers
  function _readDrafts() {
    try { return JSON.parse(sessionStorage.getItem('chat-drafts') || '{}') } catch { return {} }
  }
  function _writeDrafts(map) { sessionStorage.setItem('chat-drafts', JSON.stringify(map)) }

  const [input, setInput] = useState(() => {
    // Migrate old single-key draft to per-session map
    const oldDraft = sessionStorage.getItem('chat-draft')
    const drafts = _readDrafts()
    if (oldDraft && !Object.keys(drafts).length) {
      drafts[sessionId] = oldDraft
      _writeDrafts(drafts)
      sessionStorage.removeItem('chat-draft')
    }
    return drafts[sessionId] || ''
  })
  const [cursorPos, setCursorPos] = useState(null)
  const [inputExpanded, setInputExpanded] = useState(false)
  const [editingLastMsg, setEditingLastMsg] = useState(false)
  const [typingStatusMap, setTypingStatusMap] = useState({})   // session_id -> status text
  const [streamingTextMap, setStreamingTextMap] = useState({})  // session_id -> accumulated text
  const [showSessions, setShowSessions] = useState(false)
  const [confirmDeleteId, setConfirmDeleteId] = useState(null)
  const [editingTitleId, setEditingTitleId] = useState(null)
  const [editingTitleValue, setEditingTitleValue] = useState('')
  const [maximized, setMaximized] = useState(false)
  const [specialistInfo, setSpecialistInfo] = useState({})
  const wsRef = useRef(null)
  const messagesEndRef = useRef(null)
  const messagesRef = useRef(messages)
  const resizeRef = useRef(null)
  const panelRef = useRef(null)
  const fileInputRef = useRef(null)
  const sessionIdRef = useRef(sessionId)
  const prevSessionIdRef = useRef(sessionId)
  const streamingTextMapRef = useRef({})
  const resizeSaveTimerRef = useRef(null)
  const chatModeRef = useRef(chatMode)
  chatModeRef.current = chatMode
  const sessionsRef = useRef(sessions)
  sessionsRef.current = sessions

  // Derived: current session's streaming state
  const streaming = streamingSessions.includes(sessionId)
  const typingStatus = typingStatusMap[sessionId] || null
  const streamingText = streamingTextMap[sessionId] || ''

  function statusText(status, mode = chatMode) {
    const isDev = mode === 'dev'
    if (status === 'thinking') return isDev ? t('agent_thinking') : t('coach_thinking')
    if (status === 'writing') return isDev ? t('agent_writing') : 'Coach is writing...'
    if (status?.startsWith('using ')) return isDev ? t('agent_working') : 'Coach is working...'
    return status
  }

  // Persist draft input per session to sessionStorage (debounced)
  useEffect(() => {
    const timer = setTimeout(() => {
      const drafts = _readDrafts()
      if (input) drafts[sessionId] = input
      else delete drafts[sessionId]
      _writeDrafts(drafts)
    }, 300)
    return () => clearTimeout(timer)
  }, [input, sessionId])

  // When sessionId changes, save draft for old session and load draft for new session
  useEffect(() => {
    const prevSid = prevSessionIdRef.current
    if (prevSid !== sessionId) {
      const drafts = _readDrafts()
      // Save current input for the old session (input state still has old value)
      const currentInput = input
      if (currentInput) drafts[prevSid] = currentInput
      else delete drafts[prevSid]
      _writeDrafts(drafts)
      // Load draft for new session
      setInput(drafts[sessionId] || '')
      prevSessionIdRef.current = sessionId
    }
  }, [sessionId])

  // Keep refs in sync (combined into single effect to reduce overhead)
  useEffect(() => {
    sessionIdRef.current = sessionId
    messagesRef.current = messages
  }, [sessionId, messages])

  // Save all streaming text to DB on page unload so it's not lost on refresh
  useEffect(() => {
    const onUnload = () => {
      const map = streamingTextMapRef.current
      for (const [sid, text] of Object.entries(map)) {
        if (text) {
          navigator.sendBeacon('/api/chat/save-partial', JSON.stringify({
            session_id: sid,
            content: text,
          }))
        }
      }
    }
    window.addEventListener('beforeunload', onUnload)
    return () => window.removeEventListener('beforeunload', onUnload)
  }, [])

  // Pick up pending input from other pages (e.g. "Plan with Coach", "Discuss with Coach")
  useEffect(() => {
    if (pendingInput && chatOpen) {
      setInput(pendingInput)
      setPendingInput(null)
      setShowSessions(false)
      // Focus the textarea — retry a few times to survive re-renders and transitions
      const focusChat = () => {
        const el = document.getElementById('chat-input')
        if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length) }
      }
      setTimeout(focusChat, 100)
      setTimeout(focusChat, 400)
      setTimeout(focusChat, 800)
    }
  }, [pendingInput, chatOpen, setPendingInput])

  // After chat panel open/close transition, trigger resize so Plotly charts refit
  useEffect(() => {
    const el = panelRef.current
    if (!el) return
    const handler = () => window.dispatchEvent(new Event('resize'))
    el.addEventListener('transitionend', handler)
    return () => el.removeEventListener('transitionend', handler)
  }, [])

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  // Scroll to a specific message when navigating from notification
  useEffect(() => {
    if (pendingScrollIndex == null || !messages.length) return
    const timer = setTimeout(() => {
      const el = document.querySelector(`[data-msg-index="${pendingScrollIndex}"]`)
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
        el.classList.add('chat-msg-highlight')
        setTimeout(() => el.classList.remove('chat-msg-highlight'), 2000)
      }
      setPendingScrollIndex(null)
    }, 300)
    return () => clearTimeout(timer)
  }, [messages, pendingScrollIndex, setPendingScrollIndex])

  // Restore saved chat panel width from localStorage
  useEffect(() => {
    const saved = localStorage.getItem('chatWidth')
    if (saved && panelRef.current) {
      panelRef.current.style.setProperty('--chat-w', saved + 'px')
      document.documentElement.style.setProperty('--chat-w', saved + 'px')
    }
  }, [])

  const specialistInfoRef = useRef({})
  useEffect(() => { specialistInfoRef.current = specialistInfo }, [specialistInfo])

  // Load chat history (wait for auth to be ready)
  // Track sessionAgent in a ref so loadHistory can read it without re-triggering
  const sessionAgentRef = useRef(sessionAgent)
  useEffect(() => { sessionAgentRef.current = sessionAgent }, [sessionAgent])

  useEffect(() => {
    if (!user) return
    async function loadHistory() {
      const agent = sessionAgentRef.current
      const welcomeKey = `chat_welcome_${agent}`
      const welcomeMsg = t(welcomeKey) !== welcomeKey ? t(welcomeKey) : t('chat_welcome')
      try {
        const msgs = await api(`/api/chat/history/${sessionId}`)
        if (msgs.length) {
          setMessages(msgs.map(m => ({ role: m.role, content: m.content, timestamp: m.timestamp })))
        } else {
          // For specialists with existing insight sessions, add context to welcome
          const info = specialistInfoRef.current[agent]
          if (info && info.message_count > 0) {
            const contextNote = `\n\n*I have ${info.message_count} workout insights from previous analyses. I'll use that context when answering your questions.*`
            setMessages([{ role: 'assistant', content: welcomeMsg + contextNote }])
          } else {
            setMessages([{ role: 'assistant', content: welcomeMsg }])
          }
        }
      } catch {
        setMessages([{ role: 'assistant', content: welcomeMsg }])
      }
    }
    loadHistory()
  }, [user, sessionId, setMessages, t])

  // On mount/refresh, check which sessions are still streaming on the server
  useEffect(() => {
    api('/api/chat/streaming').then(serverSessions => {
      const ids = serverSessions.map(s => s.session_id || s)
      if (ids.length) {
        serverSessions.forEach(s => {
          const sid = s.session_id || s
          const mode = s.mode || chatModeRef.current
          startStreaming(sid)
          setTypingStatusMap(prev => ({ ...prev, [sid]: statusText('thinking', mode) }))
        })
      }
    }).catch(err => console.error('Failed to load:', err))
  }, [startStreaming])

  // WebSocket connection
  useEffect(() => {
    let ws = null
    let reconnectTimer = null

    function connect() {
      const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
      ws = new WebSocket(`${proto}//${location.host}/ws/chat`)
      wsRef.current = ws

      ws.onmessage = (e) => {
        const data = JSON.parse(e.data)
        const sid = data.session_id  // server now includes session_id in all messages
        const isCurrent = sid === sessionIdRef.current

        if (data.type === 'status') {
          setTypingStatusMap(prev => ({ ...prev, [sid]: statusText(data.text) }))
        } else if (data.type === 'delta') {
          if (isCurrent) {
            setTypingStatusMap(prev => ({ ...prev, [sid]: null }))
          }
          setStreamingTextMap(prev => {
            const next = { ...prev, [sid]: (prev[sid] || '') + data.text }
            streamingTextMapRef.current = next
            return next
          })
        } else if (data.type === 'done') {
          // Finalize: move streaming text into messages if this is current session
          setStreamingTextMap(prev => {
            const text = prev[sid]
            if (text && isCurrent) {
              setMessages(msgs => [...msgs, { role: 'assistant', content: text, timestamp: new Date().toISOString() }])
            }
            const next = { ...prev }
            delete next[sid]
            streamingTextMapRef.current = next
            return next
          })
          setTypingStatusMap(prev => { const next = { ...prev }; delete next[sid]; return next })
          stopStreaming(sid)
          notifyLlmEnd(`coach-chat-${sid}`)
          loadSessions()
          window.dispatchEvent(new CustomEvent('coach-data-update'))
          setTimeout(() => window.dispatchEvent(new CustomEvent('coach-data-update')), 2000)
        } else if (data.type === 'action_result') {
          // Agent action executed server-side — trigger data refresh
          window.dispatchEvent(new CustomEvent('coach-data-update'))
        } else if (data.type === 'usage') {
          window.dispatchEvent(new CustomEvent('token-usage-update', { detail: { cost: data.cost } }))
        } else if (data.type === 'error') {
          setTypingStatusMap(prev => { const next = { ...prev }; delete next[sid]; return next })
          if (isCurrent) {
            setMessages(msgs => [...msgs, { role: 'error', content: data.text, timestamp: new Date().toISOString() }])
          }
          setStreamingTextMap(prev => { const next = { ...prev }; delete next[sid]; return next })
          stopStreaming(sid)
          notifyLlmEnd(`coach-chat-${sid}`, data.text)
        }
      }

      ws.onopen = () => {
        // After reconnect, check for orphaned streaming sessions (e.g. server restarted)
        // and restore notification bell for still-active sessions
        api('/api/chat/streaming').then(serverSessions => {
          // serverSessions is now array of {session_id, mode, agent_name} (backward compat with plain IDs)
          const serverIds = serverSessions.map(s => s.session_id || s)
          // Restore bell indicator for sessions still streaming on server
          if (serverIds.length) {
            serverSessions.forEach(s => {
              const sid = s.session_id || s
              const mode = s.mode || chatModeRef.current
              const agent = s.agent_name || 'main-coach'
              startStreaming(sid)
              setTypingStatusMap(prev => ({ ...prev, [sid]: statusText('using tool', mode) }))
              notifyLlmStart(`coach-chat-${sid}`, AGENT_LABELS[agent] || (mode === 'dev' ? 'Dev Chat' : 'Coach Chat'), `chat:${sid}:${mode}:${agent}`)
            })
          }
          setStreamingTextMap(prev => {
            const orphaned = Object.keys(prev).filter(sid => !serverIds.includes(sid))
            if (!orphaned.length) return prev
            const next = { ...prev }
            orphaned.forEach(sid => {
              // Save partial text as assistant message if it's the current session
              if (next[sid] && sid === sessionIdRef.current) {
                setMessages(msgs => [...msgs,
                  { role: 'assistant', content: next[sid], timestamp: new Date().toISOString() },
                  { role: 'error', content: 'Server restarted — response may be incomplete. You can resend your message.', timestamp: new Date().toISOString() }
                ])
              }
              delete next[sid]
              stopStreaming(sid)
              setTypingStatusMap(p => { const n = { ...p }; delete n[sid]; return n })
              notifyLlmEnd(`coach-chat-${sid}`, 'Server restarted — response incomplete')
            })
            return next
          })
        }).catch(() => {})
      }

      ws.onclose = () => {
        reconnectTimer = setTimeout(connect, 3000)
      }
      ws.onerror = () => {}
    }

    connect()
    return () => {
      clearTimeout(reconnectTimer)
      if (ws) {
        ws.onmessage = null
        ws.onopen = null
        ws.onclose = null
        ws.onerror = null
        ws.close()
      }
    }
  }, [setMessages, startStreaming, stopStreaming, loadSessions])

  // Scroll on new messages or streaming
  useEffect(() => {
    // Double-delay: first for DOM render, second for content layout
    const t1 = setTimeout(scrollToBottom, 50)
    const t2 = setTimeout(scrollToBottom, 300)
    return () => { clearTimeout(t1); clearTimeout(t2) }
  }, [messages, streamingText, typingStatus, scrollToBottom])

  // Send a message via WebSocket (used both by input and external events)
  const sendViaWs = useCallback((msg, sid, files) => {
    if ((!msg && (!files || !files.length)) || streaming) return
    const targetSession = sid || sessionId
    startStreaming(targetSession)
    setTypingStatusMap(prev => ({ ...prev, [targetSession]: statusText('thinking') }))
    const agentLabel = AGENT_LABELS[sessionAgent] || (chatMode === 'dev' ? 'Dev Chat' : 'Coach Chat')
    const preview = msg ? msg.slice(0, 50).replace(/\n/g, ' ') : ''
    const label = preview ? `${agentLabel}: ${preview}${msg.length > 50 ? '...' : ''}` : agentLabel
    notifyLlmStart(`coach-chat-${targetSession}`, label, `chat:${targetSession}:${chatMode}:${sessionAgent}:${messagesRef.current.length}`)
    const payload = { message: msg, session_id: targetSession, agent_name: sessionAgent, mode: chatMode }
    if (files && files.length) {
      payload.attachments = files
    }
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(payload))
      // Refresh sessions list if this session isn't tracked yet (new session)
      if (!sessionsRef.current.find(s => s.session_id === targetSession)) {
        setTimeout(() => loadSessions(), 1000)
      }
    } else {
      setMessages(prev => [...prev, { role: 'error', content: !aiEnabled ? t('ai_disabled_chat') : 'Chat disconnected. Reconnecting...' }])
      stopStreaming(targetSession)
      setTypingStatusMap(prev => { const next = { ...prev }; delete next[targetSession]; return next })
    }
  }, [streaming, sessionId, sessionAgent, chatMode, setMessages, startStreaming, stopStreaming, loadSessions])

  // Listen for external chat-send events (e.g. Discuss button on Insights page)
  useEffect(() => {
    function handleChatSend(e) {
      const { message, session_id } = e.detail || {}
      if (message) sendViaWs(message, session_id)
    }
    window.addEventListener('chat-send', handleChatSend)
    return () => window.removeEventListener('chat-send', handleChatSend)
  }, [sendViaWs])

  function sendMessage() {
    const msg = input.trim()
    if ((!msg && !attachedFiles.length) || streaming) return

    const fileNames = attachedFiles.map(f => f.filename)
    const displayMsg = fileNames.length
      ? (msg ? msg + '\n' : '') + '[Files: ' + fileNames.join(', ') + ']'
      : msg
    const actualMsg = msg || 'Please look at the attached image(s) and describe what you see.'

    if (editingLastMsg) {
      // Remove the last user message (and any incomplete assistant response after it)
      setMessages(prev => {
        const lastUserIdx = prev.map(m => m.role).lastIndexOf('user')
        if (lastUserIdx >= 0) return [...prev.slice(0, lastUserIdx), { role: 'user', content: displayMsg, timestamp: new Date().toISOString() }]
        return [...prev, { role: 'user', content: displayMsg, timestamp: new Date().toISOString() }]
      })
      setEditingLastMsg(false)
    } else {
      setMessages(prev => [...prev, { role: 'user', content: displayMsg, timestamp: new Date().toISOString() }])
    }
    setInput('')
    setCursorPos(null)
    sendViaWs(actualMsg, null, attachedFiles.length ? attachedFiles : null)
    setAttachedFiles([])
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  function uploadFiles(files) {
    uploadFilesToServer(files, setAttachedFiles)
  }

  function handleDrop(e) {
    e.preventDefault()
    panelRef.current?.classList.remove('drag-over')
    if (e.dataTransfer.files.length) uploadFiles(e.dataTransfer.files)
  }

  function handleNewChat() {
    newSession()
    setShowSessions(false)
  }


  // Resize handle
  function initResize(e) {
    e.preventDefault()
    const panel = panelRef.current
    if (!panel) return
    const startX = e.clientX
    const startWidth = panel.offsetWidth

    function onMove(ev) {
      const delta = startX - ev.clientX
      const newWidth = Math.max(280, Math.min(800, startWidth + delta))
      panel.style.setProperty('--chat-w', newWidth + 'px')
      document.documentElement.style.setProperty('--chat-w', newWidth + 'px')
      panel.style.width = newWidth + 'px'
      panel.style.minWidth = newWidth + 'px'
    }
    function onUp() {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      panel.classList.remove('resizing')
      resizeRef.current?.classList.remove('dragging')
      if (resizeSaveTimerRef.current) clearTimeout(resizeSaveTimerRef.current)
      resizeSaveTimerRef.current = setTimeout(() => {
        localStorage.setItem('chatWidth', panel.offsetWidth)
      }, 300)
      panel.style.removeProperty('width')
      panel.style.removeProperty('min-width')
    }
    panel.classList.add('resizing')
    resizeRef.current?.classList.add('dragging')
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }

  // Render streaming content
  const streamingHtml = streamingText
    ? DOMPurify.sanitize(marked.parse(streamingText))
    : ''

  return (
    <aside
      id="chat-panel"
      className={`${chatOpen ? 'chat-open' : ''}${maximized ? ' chat-maximized' : ''}`}
      ref={panelRef}
      onDragOver={(e) => { e.preventDefault(); panelRef.current?.classList.add('drag-over') }}
      onDragLeave={(e) => { e.preventDefault(); panelRef.current?.classList.remove('drag-over') }}
      onDrop={handleDrop}
    >
      <div
        className="chat-resize-handle"
        ref={resizeRef}
        onMouseDown={initResize}
      />
      <div className="chat-header">
        <div className="chat-agent-switcher">
          {Object.entries(getAgentLabels(chatMode)).map(([agent, label]) => {
            const coachIcons = { 'main-coach': '\uD83E\uDD16', 'run-coach': '\uD83C\uDFC3', 'swim-coach': '\uD83C\uDFCA', 'bike-coach': '\uD83D\uDEB4', 'nutrition-coach': '\uD83C\uDF5C' }
            const devIcons = { 'frontend-dev': '\uD83D\uDDA5', 'backend-dev': '\uD83D\uDC0D', 'code-simplifier': '\u2728', 'security-reviewer': '\uD83D\uDEE1', 'frontend-reviewer': '\uD83C\uDFA8', 'backend-reviewer': '\uD83D\uDD27', 'data-reviewer': '\uD83D\uDCCB' }
            const icons = chatMode === 'dev' ? devIcons : coachIcons
            const isActive = sessionAgent === agent
            const agentSession = sessions.find(s => (s.agent_name || 'main-coach') === agent)
            const isStreaming = agentSession && streamingSessions.includes(agentSession.session_id)
            return (
              <button
                key={agent}
                className={`agent-switch-btn${isActive ? ' active' : ''}${isStreaming && !isActive ? ' streaming' : ''}`}
                title={label}
                onClick={() => {
                  if (isActive) return
                  const match = sessions.find(s => (s.agent_name || 'main-coach') === agent)
                  if (match) {
                    switchSession(match.session_id, agent)
                    setShowSessions(false)
                  } else {
                    newSession(agent)
                    setShowSessions(false)
                  }
                }}
              >
                {icons[agent] || label.charAt(0)}
              </button>
            )
          })}
        </div>
        <div className="chat-header-actions">
          <button
            className={`btn btn-sm${showSessions ? ' btn-active' : ''}`}
            onClick={() => {
              setShowSessions(s => !s)
              if (!showSessions) {
                loadSessions()
                api('/api/chat/specialist-sessions').then(setSpecialistInfo).catch(err => console.error('Failed to load:', err))
              }
            }}
            title={t('sessions')}
          >
            {t('sessions')}
          </button>
          <button className="btn btn-sm" onClick={handleNewChat} title={t('new_chat')}>{t('new_chat')}</button>
          <button className="btn btn-sm" onClick={() => setMaximized(m => !m)} title={maximized ? 'Minimize' : 'Maximize'}>{maximized ? '\u2922' : '\u2921'}</button>
          <button className="btn btn-sm" onClick={() => setChatOpen(false)} title={t('close')}>&times;</button>
        </div>
      </div>

      {showSessions ? (
        <div className="chat-sessions-list">
          {sessions.length > 0 && (() => {
            const totalMb = (sessions[0]?.total_all_bytes || 0) / (1024 * 1024)
            const maxMb = (sessions[0]?.max_bytes || 104857600) / (1024 * 1024)
            const pct = totalMb / maxMb
            const color = pct > 0.8 ? 'var(--red)' : pct > 0.5 ? 'var(--yellow)' : 'var(--green)'
            return (
              <div className="chat-sessions-usage" style={{ padding: '4px 10px', fontSize: 11, color, borderBottom: '1px solid var(--border)', textAlign: 'end', display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 4 }}>
                {t('chat_storage')}: {totalMb.toFixed(1)} / {maxMb.toFixed(0)} MB
                <InfoTip text={t('chat_storage_tip')} />
              </div>
            )
          })()}
          {sessions.length === 0 && <div className="text-dim" style={{ padding: 8 }}>{t('no_sessions')}</div>}
          {(() => {
            const modeLabels = getAgentLabels(chatMode)
            const modeAgents = Object.keys(modeLabels)
            const renderSession = (s) => {
              const isActive = s.session_id === sessionId
              const isEditing = editingTitleId === s.session_id
              const agentName = s.agent_name || 'main-coach'
              return (
                <div
                  key={s.session_id}
                  className={`chat-session-item${isActive ? ' current' : ''}`}
                  onClick={() => { if (!isEditing) { switchSession(s.session_id, agentName); setShowSessions(false) } }}
                >
                  <div className="chat-session-row">
                    <div style={{ flex: 1, minWidth: 0 }}>
                      {isEditing ? (
                        <input
                          className="input-full"
                          dir="auto"
                          autoFocus
                          value={editingTitleValue}
                          onChange={(e) => setEditingTitleValue(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              renameSession(s.session_id, editingTitleValue)
                              setEditingTitleId(null)
                            } else if (e.key === 'Escape') {
                              setEditingTitleId(null)
                            }
                          }}
                          onBlur={() => {
                            renameSession(s.session_id, editingTitleValue)
                            setEditingTitleId(null)
                          }}
                          onClick={(e) => e.stopPropagation()}
                          style={{ fontSize: 12, padding: '2px 6px', marginBottom: 2 }}
                        />
                      ) : (
                        <div className="chat-session-title" dir="auto">
                          <span className="agent-badge" data-agent={agentName}>{AGENT_LABELS[agentName] || agentName}</span>
                          <span
                            className="chat-session-title-text"
                            title={t('chat_click_rename')}
                            onClick={(e) => {
                              e.stopPropagation()
                              setEditingTitleId(s.session_id)
                              setEditingTitleValue(s.title || '')
                            }}
                          >
                            {s.title || <span className="text-dim">{s.preview || t('new_conversation')}</span>}
                          </span>
                        </div>
                      )}
                      <div className="chat-session-info">
                        <span className="chat-session-date">{formatSessionDate(s.last_msg || s.started)}</span>
                        <span className="chat-session-count">{s.msg_count} msgs</span>
                        {s.cli_file_size > 0 && (() => {
                          const kb = s.cli_file_size / 1024
                          const color = kb > 800 ? '#ff5370' : kb > 400 ? '#ffc777' : 'var(--green)'
                          return <span style={{ fontSize: 10, color, fontWeight: 500 }}>(LLM) {kb < 1024 ? `${kb.toFixed(0)} KB` : `${(kb / 1024).toFixed(1)} MB`}</span>
                        })()}
                      </div>
                    </div>
                    <button
                      className={`btn btn-sm btn-red${confirmDeleteId === s.session_id ? ' btn-confirm' : ''}`}
                      onClick={(e) => {
                        e.stopPropagation()
                        if (confirmDeleteId === s.session_id) {
                          deleteSession(s.session_id)
                          setConfirmDeleteId(null)
                        } else {
                          setConfirmDeleteId(s.session_id)
                          setTimeout(() => setConfirmDeleteId(prev => prev === s.session_id ? null : prev), 3000)
                        }
                      }}
                    >
                      {confirmDeleteId === s.session_id ? t('confirm') + '?' : t('del')}
                    </button>
                  </div>
                </div>
              )
            }

            if (chatMode === 'dev') {
              // Dev mode: flat list, all sessions sorted by last activity
              return <>
                {sessions.map(renderSession)}
                {sessions.length === 0 && modeAgents.slice(0, 3).map(agent => (
                  <div key={agent} className="chat-session-item" onClick={() => { newSession(agent); setShowSessions(false) }}>
                    <div className="chat-session-row">
                      <div style={{ flex: 1 }}>
                        <div className="chat-session-title">
                          <span className="agent-badge" data-agent={agent}>{AGENT_LABELS[agent] || agent}</span>
                          <span className="text-dim">New conversation</span>
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </>
            }

            // Coach mode: grouped view
            const mainSessions = sessions.filter(s => (s.agent_name || 'main-coach') === 'main-coach')
            const specialistSessions = sessions.filter(s => s.agent_name && s.agent_name !== 'main-coach')
            const SPECIALIST_AGENTS = ['run-coach', 'swim-coach', 'bike-coach', 'nutrition-coach']
            const specialistByAgent = {}
            specialistSessions.forEach(s => { specialistByAgent[s.agent_name] = s })

            return <>
              {mainSessions.length > 0 && (
                <div className="session-group-label">IronCoach</div>
              )}
              {mainSessions.map(renderSession)}
              <div className="session-group-label">Specialists</div>
              {SPECIALIST_AGENTS.map(agent => {
                const existing = specialistByAgent[agent]
                if (existing) return renderSession(existing)
                const info = specialistInfo[agent]
                const isActive = sessionAgent === agent && !specialistByAgent[agent]
                return (
                  <div
                    key={agent}
                    className={`chat-session-item${isActive ? ' current' : ''}`}
                    onClick={() => { newSession(agent); setShowSessions(false) }}
                  >
                    <div className="chat-session-row">
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div className="chat-session-title">
                          <span className="agent-badge" data-agent={agent}>{AGENT_LABELS[agent] || agent}</span>
                          {info ? (<>
                            <span className="text-dim text-sm">
                              {info.message_count} insights · {formatSessionDate(info.last_used_at)}
                            </span>
                            {info.file_size > 0 && (() => {
                              const kb = info.file_size / 1024
                              const color = kb > 800 ? '#ff5370' : kb > 400 ? '#ffc777' : 'var(--green)'
                              return <span style={{ fontSize: 10, color, fontWeight: 500, marginInlineStart: 4 }}>(LLM) {kb < 1024 ? `${kb.toFixed(0)} KB` : `${(kb / 1024).toFixed(1)} MB`}</span>
                            })()}
                          </>) : (
                            <span className="text-dim">New conversation</span>
                          )}
                        </div>
                      </div>
                    </div>
                  </div>
                )
              })}
            </>
          })()}

        </div>
      ) : (<>
      {(() => {
        const cur = sessions.find(s => s.session_id === sessionId)
        const cliKb = (cur?.cli_file_size || 0) / 1024
        if (cliKb <= 0) return null
        const color = cliKb > 800 ? '#ff5370' : cliKb > 400 ? '#ffc777' : 'var(--green)'
        const label = cliKb < 1024 ? `${cliKb.toFixed(0)} KB` : `${(cliKb / 1024).toFixed(1)} MB`
        return (
          <div style={{ padding: '2px 10px', fontSize: 10, color, textAlign: 'start', borderBottom: '1px solid var(--border)', background: 'var(--bg-1)' }}>
            {t('llm_session')}: {label}
          </div>
        )
      })()}
      <div className="chat-messages" id="chat-messages">
        {messages.map((m, i) => (
          <div key={i} data-msg-index={i}>
            <ChatMessage role={m.role} content={m.content} timestamp={m.timestamp} />
          </div>
        ))}
        {streamingText && (
          <div className="chat-msg assistant">
            <div className="chat-msg-role">assistant</div>
            <div className="chat-msg-body" dir={detectDir(streamingText)} dangerouslySetInnerHTML={{ __html: streamingHtml }} />
          </div>
        )}
        {typingStatus && (
          <div className="chat-typing-indicator">
            <div className="dot" /><div className="dot" /><div className="dot" />
            <span style={{ marginInlineStart: 4 }}>{typingStatus}</span>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>
      </>)}

      {attachedFiles.length > 0 && (
        <div className="chat-file-preview">
          {attachedFiles.map((f, i) => (
            <span key={f.file_path || i} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, marginInlineEnd: 8 }}>
              {f.filename}
              <span className="remove-file" onClick={() => setAttachedFiles(prev => prev.filter((_, j) => j !== i))}>x</span>
            </span>
          ))}
        </div>
      )}

      {inputExpanded && <div className="expand-backdrop" onClick={() => setInputExpanded(false)} />}
      <div className={`chat-input-area${inputExpanded ? ' expanded' : ''}`}>
        <div className="chat-input-row">
          <input type="file" ref={fileInputRef} style={{ display: 'none' }} multiple
            onChange={(e) => { uploadFiles(e.target.files); e.target.value = '' }} />
          <button className="btn btn-sm" onClick={() => fileInputRef.current?.click()} title="Attach file">&#128206;</button>
          <textarea
            id="chat-input"
            dir={inputDirAtCursor(input, cursorPos, lang)}
            value={input}
            onChange={(e) => { setInput(e.target.value); setCursorPos(e.target.selectionStart) }}
            onSelect={(e) => setCursorPos(e.target.selectionStart)}
            onKeyDown={(e) => { if (e.key === 'Escape') { if (inputExpanded) { setInputExpanded(false); e.stopPropagation(); } return }; handleKeyDown(e) }}
            onPaste={e => handleFilePaste(e, uploadFiles)}
            placeholder={t('ask_coach')}
            rows={inputExpanded ? 10 : 2}
            style={inputExpanded ? {} : { overflow: 'hidden' }}
            onInput={(e) => {
              if (!inputExpanded) {
                e.target.style.height = 'auto'
                e.target.style.height = e.target.scrollHeight + 'px'
              }
            }}
          />
          <div className="chat-input-buttons">
            <button className="btn btn-sm btn-icon" onClick={() => setInputExpanded(x => !x)} title={inputExpanded ? 'Collapse' : 'Expand'} style={{ padding: '4px 6px' }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                {inputExpanded
                  ? <><polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="14" y1="10" x2="21" y2="3"/><line x1="3" y1="21" x2="10" y2="14"/></>
                  : <><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></>
                }
              </svg>
            </button>
            {streaming ? (
              <button className="btn btn-red btn-sm" onClick={() => {
                api('/api/chat/stop', { method: 'POST', body: JSON.stringify({ session_id: sessionId }) })
                // After stop, allow editing the last user message
                setTimeout(() => {
                  const lastUserMsg = [...messages].reverse().find(m => m.role === 'user')
                  if (lastUserMsg) {
                    // Extract text without [Files: ...] suffix
                    const text = lastUserMsg.content.replace(/\n?\[Files?:.*?\]$/, '').trim()
                    setInput(text)
                    setEditingLastMsg(true)
                  }
                }, 500)
              }}>{t('stop')}</button>
            ) : (
              <button className="btn btn-accent btn-sm" onClick={() => { sendMessage(); setInputExpanded(false) }} disabled={!aiEnabled}>{aiEnabled ? t('send') : t('ai_disabled_btn')}</button>
            )}
          </div>
        </div>
      </div>
    </aside>
  )
}
