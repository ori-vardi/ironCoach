import { createContext, useContext, useState, useEffect, useCallback } from 'react'

const AuthContext = createContext()
const SESSIONS_KEY = 'ironcoach_sessions'

function getSavedSessions() {
  try { return JSON.parse(localStorage.getItem(SESSIONS_KEY) || '[]') } catch { return [] }
}

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)
  const [needsSetup, setNeedsSetup] = useState(false)
  const [savedSessions, setSavedSessions] = useState(getSavedSessions)

  function saveSession(userData) {
    const sessions = getSavedSessions().filter(s => s.username !== userData.username)
    sessions.unshift({
      username: userData.username,
      display_name: userData.display_name,
      role: userData.role,
      token: userData.token,
    })
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions))
    setSavedSessions(sessions)
  }

  function removeSession(username) {
    const sessions = getSavedSessions().filter(s => s.username !== username)
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(sessions))
    setSavedSessions(sessions)
  }

  useEffect(() => {
    Promise.all([
      fetch('/api/auth/me').then(r => r.ok ? r.json() : null).catch(() => null),
      fetch('/api/auth/has-users').then(r => r.json()).catch(() => ({ has_users: true })),
    ]).then(([me, status]) => {
      if (me) setUser(me)
      if (!status.has_users) setNeedsSetup(true)
      setLoading(false)
    })
  }, [])

  const login = useCallback(async (username, password) => {
    const r = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    })
    if (!r.ok) {
      const err = await r.json().catch(() => ({}))
      throw new Error(err.detail || 'Login failed')
    }
    const u = await r.json()
    saveSession(u)
    setUser(u)
    setNeedsSetup(false)
    return u
  }, [])

  const setup = useCallback(async (username, password, display_name, profile = {}) => {
    const r = await fetch('/api/auth/setup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, display_name, ...profile }),
    })
    if (!r.ok) {
      const err = await r.json().catch(() => ({}))
      throw new Error(err.detail || 'Setup failed')
    }
    const u = await r.json()
    saveSession(u)
    setUser(u)
    setNeedsSetup(false)
    return u
  }, [])

  const signup = useCallback(async (username, password, display_name, profile = {}) => {
    const r = await fetch('/api/auth/signup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, display_name, ...profile }),
    })
    if (!r.ok) {
      const err = await r.json().catch(() => ({}))
      throw new Error(err.detail || 'Signup failed')
    }
    const u = await r.json()
    saveSession(u)
    setUser(u)
    return u
  }, [])

  const changePassword = useCallback(async (current_password, new_password) => {
    const r = await fetch('/api/auth/change-password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current_password, new_password }),
    })
    if (!r.ok) {
      const err = await r.json().catch(() => ({}))
      throw new Error(err.detail || 'Failed')
    }
    return true
  }, [])

  const switchToUser = useCallback(async (session) => {
    const r = await fetch('/api/auth/switch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: session.token }),
    })
    if (!r.ok) {
      // Token expired — remove from saved sessions
      removeSession(session.username)
      throw new Error('Session expired — please sign in again')
    }
    const u = await r.json()
    saveSession(u)
    setUser(u)
    return u
  }, [])

  const logout = useCallback(async () => {
    await fetch('/api/auth/logout', { method: 'POST' })
    localStorage.removeItem(SESSIONS_KEY)
    setSavedSessions([])
    setUser(null)
  }, [])

  const logoutKeepSession = useCallback(async () => {
    // Switch user — log out but keep session saved
    await fetch('/api/auth/logout', { method: 'POST' })
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider value={{
      user, loading, needsSetup, savedSessions,
      login, logout, logoutKeepSession, setup, signup, changePassword, switchToUser,
    }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => useContext(AuthContext)
