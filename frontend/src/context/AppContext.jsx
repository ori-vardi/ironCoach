import { createContext, useContext, useState, useMemo, useCallback, useEffect } from 'react'
import { useAuth } from './AuthContext'
import { api } from '../api'

const AppContext = createContext()

const DEFAULT_FROM = '2025-12-01'

function todayStr() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function AppProvider({ children }) {
  const { user } = useAuth()
  const [allWorkouts, setAllWorkouts] = useState([])
  const [race, setRace] = useState(null)
  const [aiEnabled, setAiEnabled] = useState(true)
  const [dateFrom, setDateFrom] = useState(() => localStorage.getItem('dateFrom') || DEFAULT_FROM)
  const [dateTo, setDateTo] = useState(todayStr())

  // Load all workouts + AI status once when user is authenticated
  useEffect(() => {
    if (!user) return
    api('/api/summary')
      .then(setAllWorkouts)
      .catch(err => console.error('Failed to load:', err))
    api('/api/ai-status').then(r => setAiEnabled(r.ai_enabled)).catch(() => {})
    const onUpdate = () => {
      api('/api/ai-status').then(r => setAiEnabled(r.ai_enabled)).catch(() => {})
      api('/api/summary').then(setAllWorkouts).catch(() => {})
    }
    window.addEventListener('coach-data-update', onUpdate)
    return () => window.removeEventListener('coach-data-update', onUpdate)
  }, [user])

  // Filtered workouts based on global date range
  const workouts = useMemo(() => {
    if (!allWorkouts.length) return allWorkouts
    return allWorkouts.filter(w => {
      const d = (w.startDate || '').slice(0, 10)
      return d >= dateFrom && d <= dateTo
    })
  }, [allWorkouts, dateFrom, dateTo])

  const setWorkouts = useCallback((data) => {
    setAllWorkouts(data)
  }, [])

  const refreshWorkouts = useCallback(() => {
    api('/api/summary')
      .then(setAllWorkouts)
      .catch(err => console.error('Failed to load:', err))
  }, [])

  const setDateRange = useCallback((from, to) => {
    setDateFrom(from)
    setDateTo(to)
    localStorage.setItem('dateFrom', from)
  }, [])

  const contextValue = useMemo(() => ({
    workouts, allWorkouts, setWorkouts, refreshWorkouts, race, setRace,
    aiEnabled, dateFrom, dateTo, setDateRange,
  }), [workouts, allWorkouts, setWorkouts, refreshWorkouts, race, aiEnabled, dateFrom, dateTo, setDateRange])

  return (
    <AppContext.Provider value={contextValue}>
      {children}
    </AppContext.Provider>
  )
}

export const useApp = () => useContext(AppContext)
