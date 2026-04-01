import { Routes, Route, Navigate } from 'react-router-dom'
import { useAuth } from './context/AuthContext'
import Layout from './components/Layout'
import LoginPage from './pages/LoginPage'
import AdminPage from './pages/AdminPage'
import OverviewPage from './pages/OverviewPage'
import RunningPage from './pages/RunningPage'
import CyclingPage from './pages/CyclingPage'
import SwimmingPage from './pages/SwimmingPage'
import AllWorkoutsPage from './pages/AllWorkoutsPage'
import InsightsPage from './pages/InsightsPage'
import TrainingPlanPage from './pages/TrainingPlanPage'
import NutritionPage from './pages/NutritionPage'
import RacePage from './pages/RacePage'
import BodyMetricsPage from './pages/BodyMetricsPage'
import BricksPage from './pages/BricksPage'
import RecoveryPage from './pages/RecoveryPage'
import SessionsPage from './pages/SessionsPage'
import LoadingSpinner from './components/common/LoadingSpinner'

export default function App() {
  const { user, loading, needsSetup } = useAuth()

  if (loading) return <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}><LoadingSpinner /></div>

  if (!user || needsSetup) return <LoginPage />

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<OverviewPage />} />
        <Route path="running" element={<RunningPage />} />
        <Route path="cycling" element={<CyclingPage />} />
        <Route path="swimming" element={<SwimmingPage />} />
        <Route path="workouts" element={<AllWorkoutsPage />} />
        <Route path="bricks" element={<BricksPage />} />
        <Route path="insights" element={<InsightsPage />} />
        <Route path="plan" element={<TrainingPlanPage />} />
        <Route path="nutrition" element={<NutritionPage />} />
        <Route path="events" element={<RacePage />} />
        <Route path="race" element={<Navigate to="/events" replace />} />
        <Route path="body" element={<BodyMetricsPage />} />
        <Route path="recovery" element={<RecoveryPage />} />
        <Route path="sessions" element={<SessionsPage />} />
        {user.role === 'admin' && <Route path="admin" element={<AdminPage />} />}
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
