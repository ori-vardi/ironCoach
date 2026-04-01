import { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react'
import { api } from '../api'
import { useAuth } from './AuthContext'

const ChatContext = createContext()

export function ChatProvider({ children }) {
  const { user } = useAuth()
  const userId = user?.id
  const [chatMode, setChatModeRaw] = useState(() => sessionStorage.getItem('chat-mode') || 'coach')
  const [sessionId, setSessionId] = useState(() => sessionStorage.getItem('chat-session-id') || crypto.randomUUID())
  const [sessionAgent, setSessionAgent] = useState(() => sessionStorage.getItem('chat-session-agent') || 'main-coach')
  const [messages, setMessages] = useState([])
  const [sessions, setSessions] = useState([])
  const [streamingSessions, setStreamingSessions] = useState([])
  const [attachedFiles, setAttachedFiles] = useState([])
  const [chatOpen, setChatOpen] = useState(() => sessionStorage.getItem('chat-open') === '1')
  const [pendingScrollIndex, setPendingScrollIndex] = useState(null)
  const [pendingInput, setPendingInput] = useState(null)
  // Ref to prevent the chatMode effect from overwriting session set by switchToSession
  const _switchingRef = useRef(false)

  // Persist session id, agent, mode to sessionStorage
  useEffect(() => {
    sessionStorage.setItem('chat-session-id', sessionId)
  }, [sessionId])
  useEffect(() => {
    sessionStorage.setItem('chat-session-agent', sessionAgent)
  }, [sessionAgent])
  useEffect(() => {
    sessionStorage.setItem('chat-mode', chatMode)
  }, [chatMode])

  const loadSessions = useCallback(async (mode) => {
    const m = mode || chatMode
    try {
      const list = await api(`/api/chat/sessions?mode=${m}`)
      setSessions(list)
      return list
    } catch {
      return []
    }
  }, [chatMode])

  useEffect(() => {
    if (!userId) return
    if (_switchingRef.current) {
      _switchingRef.current = false
      loadSessions(chatMode)
      return
    }
    loadSessions(chatMode).then(list => {
      const persisted = sessionStorage.getItem('chat-session-id')
      const inList = persisted && list.some(s => s.session_id === persisted)
      if (!inList) {
        if (list.length > 0) {
          setSessionId(list[0].session_id)
          setSessionAgent(list[0].agent_name || 'main-coach')
        } else {
          setSessionId(crypto.randomUUID())
        }
        setMessages([])
      }
    })
  }, [userId, chatMode]) // eslint-disable-line react-hooks/exhaustive-deps

  // Persist chat open state + refresh sessions when chat opens
  useEffect(() => {
    sessionStorage.setItem('chat-open', chatOpen ? '1' : '0')
    if (chatOpen) loadSessions()
  }, [chatOpen, loadSessions])

  const setChatMode = useCallback((mode) => {
    setChatModeRaw(mode)
    // Reset to new session for the new mode — sessions will reload via effect
    setSessionId(crypto.randomUUID())
    setSessionAgent(mode === 'dev' ? 'frontend-dev' : 'main-coach')
    setMessages([])
  }, [])

  const newSession = useCallback((agentName) => {
    const defaultAgent = chatMode === 'dev' ? 'frontend-dev' : 'main-coach'
    const id = crypto.randomUUID()
    setSessionId(id)
    setSessionAgent(agentName || defaultAgent)
    setMessages([])
    setTimeout(() => loadSessions(), 500)
  }, [chatMode, loadSessions])

  const switchSession = useCallback((id, agentName) => {
    if (id === sessionId) return
    setSessionId(id)
    setSessionAgent(agentName || 'main-coach')
    setMessages([])
  }, [sessionId])

  const switchToSession = useCallback((id, agentName, mode) => {
    setMessages([])
    if (mode && mode !== chatMode) {
      _switchingRef.current = true
      setChatModeRaw(mode)
    } else {
      loadSessions(mode || chatMode)
    }
    setSessionId(id)
    setSessionAgent(agentName || (mode === 'dev' ? 'frontend-dev' : 'main-coach'))
  }, [chatMode, loadSessions])

  const deleteSession = useCallback(async (id) => {
    try {
      await api(`/api/chat/sessions/${id}`, { method: 'DELETE' })
      setSessions(prev => prev.filter(s => s.session_id !== id))
      if (id === sessionId) {
        newSession()
      }
      window.dispatchEvent(new CustomEvent('coach-data-update'))
    } catch { /* ignore */ }
  }, [sessionId, newSession])

  const renameSession = useCallback(async (id, title) => {
    try {
      await api(`/api/chat/sessions/${id}/title`, {
        method: 'PATCH',
        body: JSON.stringify({ title }),
      })
      setSessions(prev => prev.map(s => s.session_id === id ? { ...s, title } : s))
    } catch { /* ignore */ }
  }, [])

  const startStreaming = useCallback((sid) => {
    setStreamingSessions(prev => prev.includes(sid) ? prev : [...prev, sid])
  }, [])
  const stopStreaming = useCallback((sid) => {
    setStreamingSessions(prev => prev.filter(x => x !== sid))
  }, [])

  return (
    <ChatContext.Provider value={{
      chatMode, setChatMode,
      sessionId, sessionAgent, setSessionAgent,
      messages, setMessages, streamingSessions, startStreaming, stopStreaming,
      attachedFiles, setAttachedFiles, chatOpen, setChatOpen,
      sessions, newSession, switchSession, switchToSession, deleteSession, renameSession, loadSessions,
      pendingScrollIndex, setPendingScrollIndex,
      pendingInput, setPendingInput
    }}>
      {children}
    </ChatContext.Provider>
  )
}

export const useChat = () => useContext(ChatContext)
