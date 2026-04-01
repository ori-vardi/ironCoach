import { createContext, useContext, useState, useCallback } from 'react'
import { translations } from './translations'

const I18nContext = createContext()

const STORAGE_KEY = 'ironcoach_lang'

export function I18nProvider({ children }) {
  const [lang, setLangState] = useState(() => {
    const saved = localStorage.getItem(STORAGE_KEY) || 'en'
    // Set dir/lang on initial load so RTL is active before first render
    document.documentElement.dir = saved === 'he' ? 'rtl' : 'ltr'
    document.documentElement.lang = saved
    return saved
  })

  const setLang = useCallback((l) => {
    setLangState(l)
    localStorage.setItem(STORAGE_KEY, l)
    document.documentElement.dir = l === 'he' ? 'rtl' : 'ltr'
    document.documentElement.lang = l
  }, [])

  const t = useCallback((key, params) => {
    let str = translations[lang]?.[key] ?? translations.en?.[key] ?? (typeof params === 'string' ? params : key)
    if (params && typeof params === 'object') {
      for (const [k, v] of Object.entries(params)) {
        str = str.replace(`{${k}}`, v)
      }
    }
    return str
  }, [lang])

  return (
    <I18nContext.Provider value={{ lang, setLang, t }}>
      {children}
    </I18nContext.Provider>
  )
}

export function useI18n() {
  return useContext(I18nContext)
}
