// Settings: OAC URL, provider and model, API key of the chosen provider (liveinsight/settings.py).
// Keys never come back from the backend: we only know whether they are set. An empty field keeps the saved one.
// The listed models are suggestions: "Other model…" accepts any model ID, and "Verify" makes a test call with a
// tool, to know before saving whether tool calling really works.
import { useEffect, useState } from 'react'
import { api, type ProbeResult, type SettingsView } from './api'
import { useT } from './i18n'

const providerOf = (model: string) => model.split(':')[0]
const OTHER = '__other__'

export default function Settings({ firstRun, onDone }: {
  firstRun: boolean
  onDone: (saved?: SettingsView) => void          // no argument: cancelled
}) {
  const { t, tn, tm, err, money, number } = useT()
  const [view, setView] = useState<SettingsView | null>(null)
  const [oacUrl, setOacUrl] = useState('')
  const [model, setModel] = useState('')
  const [custom, setCustom] = useState(false)      // model ID typed by hand
  const [apiKey, setApiKey] = useState('')
  const [compartment, setCompartment] = useState('')
  const [baseUrl, setBaseUrl] = useState('')         // the custom provider's server
  const [busy, setBusy] = useState<'' | 'save' | 'verify'>('')
  const [error, setError] = useState<unknown>(null)   // raw: written with err() when shown, in the current language
  const [probe, setProbe] = useState<{ result: ProbeResult; of: string } | null>(null)   // of: the verified choice

  useEffect(() => {
    api.settings()
      .then((v) => {
        // no default model: the first proposed one of the first provider, until the user picks
        const m = v.model ?? v.providers[0].models[0]?.id ?? `${v.providers[0].id}:`
        const known = v.providers.find((p) => p.id === providerOf(m))?.models.some((x) => x.id === m)
        setView(v); setOacUrl(v.oac_url ?? ''); setModel(m); setCustom(!known); setCompartment(v.oci_compartment ?? '')
        setBaseUrl(v.custom_base_url ?? '')
      })
      .catch(setError)
  }, [])

  if (!view) return <main className="settings">{error ? <p className="error">{err(error)}</p> : <p className="hint">{t('settings.loading')}</p>}</main>

  const provider = view.providers.find((p) => p.id === providerOf(model)) ?? view.providers[0]
  const freeModel = custom || provider.models.length === 0
  const keySet = view.keys_set[provider.id]
  const keyOptional = view.keyless.includes(provider.id)
  const choice = {
    model, api_key: apiKey.trim() || undefined, oci_compartment: compartment.trim() || undefined,
    custom_base_url: provider.id === 'custom' ? baseUrl.trim() || undefined : undefined,
  }
  const shown = probe && probe.of === JSON.stringify(choice) ? probe.result : null   // the choice changed: the result no longer applies

  const chooseProvider = (id: string) => {
    const p = view.providers.find((x) => x.id === id)!
    setModel(p.models[0]?.id ?? `${id}:`)
    setCustom(p.models.length === 0)
    setApiKey('')
  }
  const chooseModel = (value: string) => {
    if (value === OTHER) { setCustom(true); setModel(`${provider.id}:`) }
    else { setCustom(false); setModel(value) }
  }

  const run = async (what: 'save' | 'verify') => {
    setBusy(what)
    setError(null)
    try {
      if (what === 'verify') setProbe({ result: await api.verifyModel(choice), of: JSON.stringify(choice) })
      else onDone(await api.saveSettings({ ...choice, oac_url: oacUrl.trim() }))
    } catch (e) {
      setError(e)
    } finally {
      setBusy('')
    }
  }

  return (
    <main className="settings">
      <h1>{t('settings.title')}</h1>
      {firstRun && <p className="hint">{t('settings.firstRun')}</p>}
      <form onSubmit={(e) => { e.preventDefault(); run('save') }}>
        <label className="field">
          <span>{t('settings.oacUrl')}</span>
          <input type="url" required value={oacUrl} onChange={(e) => setOacUrl(e.target.value)}
            placeholder={t('settings.oacUrlPlaceholder')} />
          <small>{t('settings.oacUrlHint')}</small>
        </label>

        <label className="field">
          <span>{t('settings.provider')}</span>
          <select value={provider.id} onChange={(e) => chooseProvider(e.target.value)}>
            {view.providers.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          </select>
        </label>

        <div className="field">
          <label htmlFor="model">{t('settings.model')}</label>
          {provider.models.length > 0 && (
            <select id={freeModel ? undefined : 'model'} value={freeModel ? OTHER : model} onChange={(e) => chooseModel(e.target.value)}>
              {provider.models.map((m) => <option key={m.id} value={m.id}>{m.label}{m.note && ` (${t(m.note)})`}</option>)}
              <option value={OTHER}>{t('settings.otherModel')}</option>
            </select>
          )}
          {freeModel && (
            <input id="model" required value={model.slice(provider.id.length + 1)}
              placeholder={provider.id === 'oci' ? t('settings.modelPlaceholderOci')
                : provider.id === 'custom' ? t('settings.modelPlaceholderCustom') : t('settings.modelPlaceholder')}
              onChange={(e) => setModel(`${provider.id}:${e.target.value.trim()}`)} />
          )}
          <small>{t('settings.modelHint')}{provider.id === 'oci' && ` ${t('settings.region', { region: view.oci_region })}`}</small>
        </div>

        {provider.id === 'custom' && (
          <label className="field">
            <span>{t('settings.baseUrl')}</span>
            <input type="url" required value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="http://localhost:11434/v1" />
            <small>{t('settings.baseUrlHint')}</small>
          </label>
        )}

        <label className="field">
          <span>{t('settings.apiKey', { provider: provider.label })}</span>
          <input type="password" autoComplete="off" value={apiKey} onChange={(e) => setApiKey(e.target.value)}
            placeholder={keySet ? t('settings.apiKeySaved') : keyOptional ? t('settings.apiKeyOptional') : t('settings.apiKeyPaste')}
            required={!keySet && !keyOptional} />
          <small>{tn('settings.apiKeyHint', { path: <code key="path">.secrets/settings.json</code> })}</small>
        </label>

        {provider.id === 'oci' && (
          <label className="field">
            <span>{t('settings.compartment')}</span>
            <input value={compartment} onChange={(e) => setCompartment(e.target.value)} required
              placeholder="ocid1.compartment.oc1…" />
            <small>{t('settings.compartmentHint')}</small>
          </label>
        )}

        {shown && (
          <p className={shown.ok ? 'probe ok' : 'probe error'}>
            {shown.ok ? t('settings.verified') : t('settings.notWorking')}{tm(shown.detail)}
            <span className="meta"> · {shown.api} · {number.format(shown.seconds)} s{shown.cost ? ` · ${money(shown.cost, shown.currency)}` : ''}</span>
          </p>
        )}
        {error ? <p className="error">{err(error)}</p> : null}
        <div className="actions">
          <button disabled={!!busy}>{busy === 'save' ? t('settings.saving') : t('settings.save')}</button>
          <button type="button" className="secondary" disabled={!!busy} onClick={() => run('verify')}>
            {busy === 'verify' ? t('settings.verifying') : t('settings.verify')}
          </button>
          {!firstRun && <button type="button" className="secondary" onClick={() => onDone()}>{t('settings.cancel')}</button>}
        </div>
      </form>
    </main>
  )
}
