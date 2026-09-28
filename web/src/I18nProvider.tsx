// The chosen UI language for the whole app, and the picker that changes it (see ./i18n.ts).
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { CATALOGS, I18nContext, initialLang, LANGS, makeT, saveLang, useT, type Lang } from './i18n'

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLang] = useState<Lang>(initialLang)
  useEffect(() => { document.documentElement.lang = lang }, [lang])
  const value = useMemo(() => makeT(lang, (l: Lang) => { saveLang(l); setLang(l) }), [lang])
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}

// Each language is named in its own language ("language.name" in its JSON).
export function LanguagePicker() {
  const { lang, setLang, t } = useT()
  return (
    <select className="lang" value={lang} onChange={(e) => setLang(e.target.value)} aria-label={t('top.language')}>
      {LANGS.map((l) => <option key={l} value={l}>{CATALOGS[l]['language.name']}</option>)}
    </select>
  )
}
