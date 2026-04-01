import { useState } from 'react'
import { useAuth } from '../context/AuthContext'
import { useI18n } from '../i18n/I18nContext'

export default function LoginPage() {
  const { login, setup, signup, needsSetup } = useAuth()
  const { t } = useI18n()
  const [mode, setMode] = useState('login') // 'login' or 'signup'
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [heightCm, setHeightCm] = useState('')
  const [birthDate, setBirthDate] = useState('')
  const [sex, setSex] = useState('male')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const isSetup = needsSetup
  const isSignup = !isSetup && mode === 'signup'

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const profile = {}
      if (heightCm) profile.height_cm = Number(heightCm)
      if (birthDate) profile.birth_date = birthDate
      if (sex) profile.sex = sex
      if (isSetup) {
        await setup(username, password, displayName || username, profile)
      } else if (isSignup) {
        await signup(username, password, displayName || username, profile)
      } else {
        await login(username, password)
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  function switchMode() {
    setMode(m => m === 'login' ? 'signup' : 'login')
    setError('')
  }

  const showNameField = isSetup || isSignup

  return (
    <div className="login-page">
      <form className="login-form" onSubmit={handleSubmit}>
        <h1 className="login-title">{t('login_title')}</h1>
        {isSetup && (
          <div className="setup-notice">
            {t('login_setup_notice')} <strong>{t('login_admin')}</strong>
          </div>
        )}
        {error && <div className="login-error">{error}</div>}
        <div className="form-group">
          <label>{t('login_username')}</label>
          <input
            type="text"
            className="input-full"
            value={username}
            onChange={e => setUsername(e.target.value)}
            autoFocus
            autoComplete="username"
            required
          />
        </div>
        <div className="form-group">
          <label>{t('login_password')}</label>
          <input
            type="password"
            className="input-full"
            value={password}
            onChange={e => setPassword(e.target.value)}
            autoComplete={showNameField ? 'new-password' : 'current-password'}
            required
          />
        </div>
        {showNameField && (
          <>
            <div className="form-group">
              <label>{t('login_display_name')}</label>
              <input
                type="text"
                className="input-full"
                value={displayName}
                onChange={e => setDisplayName(e.target.value)}
                placeholder={t('login_optional')}
                autoComplete="name"
              />
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.4fr 0.8fr', gap: 10, alignItems: 'end' }}>
              <div className="form-group">
                <label>{t('login_height')}</label>
                <input type="number" className="input-full" placeholder={t('login_height_placeholder')} value={heightCm} onChange={e => setHeightCm(e.target.value)} />
              </div>
              <div className="form-group">
                <label>{t('login_birth_date')}</label>
                <input type="date" className="input-full" value={birthDate} onChange={e => setBirthDate(e.target.value)} />
              </div>
              <div className="form-group">
                <label>{t('login_sex')}</label>
                <select className="input-full" value={sex} onChange={e => setSex(e.target.value)}>
                  <option value="male">{t('login_male')}</option>
                  <option value="female">{t('login_female')}</option>
                </select>
              </div>
            </div>
          </>
        )}
        <button className="btn btn-accent btn-block" type="submit" disabled={loading}>
          {loading
            ? (isSetup ? t('login_creating') : isSignup ? t('login_signing_up') : t('login_signing_in'))
            : (isSetup ? t('login_create_admin') : isSignup ? t('login_sign_up') : t('login_sign_in'))
          }
        </button>
        {!isSetup && (
          <div className="login-switch">
            {mode === 'login'
              ? <>{t('login_no_account')} <button type="button" className="link-btn" onClick={switchMode}>{t('login_sign_up')}</button></>
              : <>{t('login_have_account')} <button type="button" className="link-btn" onClick={switchMode}>{t('login_sign_in')}</button></>
            }
          </div>
        )}
      </form>
    </div>
  )
}
