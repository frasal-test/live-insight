// UI translations: one JSON per language in ./locales, all with the same keys (tests/test_locales.py checks it).
// No i18n library: the npm registry is blocked on the corporate network and we only need lookup, {placeholder}
// interpolation and plurals. Plurals follow the i18next convention (key_one, key_other) and Intl.PluralRules.
// The backend sends no UI prose (liveinsight/messages.py): messages {key, params}, written here with tm(), and
// ⟦key⟧ tokens inside data it builds (Vega-Lite titles, column labels), replaced by localize() before drawing.
// Model answers are not translated here: the model replies in the language of the question.
import { createContext, useContext, type ReactNode } from 'react'
import en from './locales/en.json'
import it from './locales/it.json'

type Catalog = Record<string, string>
export const CATALOGS = { en, it } as Record<string, Catalog>
export type Lang = string
export const LANGS: Lang[] = Object.keys(CATALOGS)
const FALLBACK: Lang = 'en'
const STORAGE_KEY = 'liveinsight.lang'

// Last choice, then the browser languages, then English. Storage can be unavailable (private windows).
export function initialLang(): Lang {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved && saved in CATALOGS) return saved
  } catch { /* no storage: use the browser */ }
  return navigator.languages.map((l) => l.split('-')[0]).find((l) => l in CATALOGS) ?? FALLBACK
}

export function saveLang(lang: Lang) {
  try { localStorage.setItem(STORAGE_KEY, lang) } catch { /* the choice lasts for this page only */ }
}

type Params = Record<string, string | number>
export type Msg = { key: string; params?: Params }
export type T = {
  lang: Lang
  setLang: (lang: Lang) => void
  t: (key: string, params?: Params) => string
  tn: (key: string, nodes: Record<string, ReactNode>) => ReactNode[]   // placeholders filled with elements
  number: Intl.NumberFormat
  tm: (m: Msg) => string                                               // a message from the backend
  localize: <V>(value: V) => V                                          // ⟦key⟧ tokens replaced, at any depth
  err: (e: unknown) => string                                           // an error, as the UI shows it
  money: (v: number | null | undefined, currency: 'USD' | 'EUR' | null | undefined) => string   // '' without both
}

const TOKEN = /⟦([\w.]+)⟧/g
export const isMsg = (v: unknown): v is Msg =>
  typeof v === 'object' && v !== null && typeof (v as Msg).key === 'string'

// Errors from the backend carry a message (see api.ts: ApiError.detail); anything else shows its own text.
function errorMessage(e: unknown): Msg | string {
  const detail = (e as { detail?: unknown })?.detail
  if (isMsg(detail)) return detail
  return e instanceof Error ? e.message : String(e)
}

function lookup(lang: Lang, key: string, count?: number): string {
  const keys = count === undefined ? [key] : [`${key}_${new Intl.PluralRules(lang).select(count)}`, `${key}_other`, key]
  for (const catalog of [CATALOGS[lang], CATALOGS[FALLBACK]]) {
    const found = keys.find((k) => k in catalog)
    if (found) return catalog[found]
  }
  return key
}

export function makeT(lang: Lang, setLang: (lang: Lang) => void): T {
  const number = new Intl.NumberFormat(lang, { maximumFractionDigits: 2 })
  const t = (key: string, params: Params = {}) => {
    const count = typeof params.count === 'number' ? params.count : undefined
    return lookup(lang, key, count).replace(/\{(\w+)\}/g, (m, name: string) => {
      const v = params[name]
      return v === undefined ? m : typeof v === 'number' ? number.format(v) : v
    })
  }
  const tn = (key: string, nodes: Record<string, ReactNode>) =>
    lookup(lang, key).split(/(\{\w+\})/).map((part) => {
      const name = part.match(/^\{(\w+)\}$/)?.[1]
      return name && name in nodes ? nodes[name] : part
    })
  const localizeString = (s: string) => s.replace(TOKEN, (_, key: string) => t(key))
  const localize = <V,>(value: V): V => {
    if (typeof value === 'string') return (value.includes('⟦') ? localizeString(value) : value) as V
    if (Array.isArray(value)) return value.map(localize) as V
    if (value && typeof value === 'object') {
      return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, localize(v)])) as V
    }
    return value
  }
  const tm = (m: Msg) => t(m.key, localize(m.params ?? {}))
  const err = (e: unknown) => {
    const m = errorMessage(e)
    return typeof m === 'string' ? m : tm(m)
  }
  const money = (v: number | null | undefined, currency: 'USD' | 'EUR' | null | undefined) =>
    v === null || v === undefined || !currency ? ''
      : new Intl.NumberFormat(lang, { style: 'currency', currency, minimumFractionDigits: 4, maximumFractionDigits: 4 }).format(v)
  return { lang, setLang, t, tn, number, tm, localize, err, money }
}

export const I18nContext = createContext<T>(makeT(FALLBACK, () => {}))
export const useT = () => useContext(I18nContext)
