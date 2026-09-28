// Live Insight: history (to do) on the left, conversation with its visuals in the middle, workbook on the right.
// State lives in the backend (in-memory session); here we keep a copy to draw. UI strings come from ./locales.
import { useEffect, useRef, useState } from 'react'
import Markdown from 'react-markdown'
import { api, ApiError, type AskEvent, type Dataset, type Currency, type Filter, type Me, type Message, type Notice, type SettingsView, type Visual } from './api'
import { useT, type Msg, type T } from './i18n'
import { LanguagePicker } from './I18nProvider'
import Preview from './Preview'
import Settings from './Settings'

const kindLabel = (t: T['t'], kind: string) => {
  const label = t(`kind.${kind}`)
  return label === `kind.${kind}` ? kind : label
}

// A filter chip in the UI language: "Order Date (year) = 2016", "Profit between −92,680 and −85,460".
function filterText({ t, number, localize }: T, f: Filter) {
  const column = localize(f.column)
  const value = (v: string | number) => (typeof v === 'number' ? number.format(v) : v)
  if (f.op === 'in') return `${column} = ${f.values.map(value).join(', ')}`
  const [from, to] = f.values.map(value)
  return `${column} ${t(typeof f.values[0] === 'number' ? 'filter.between' : 'filter.dateRange', { from, to })}`
}

// The chat keeps data, not sentences: every text is written when drawn, so a language switch translates it all.
type Activity = Msg & { error?: boolean; visual?: boolean }
type Meta = { seconds: number; steps: number; cost: number | null; currency: Currency }
// visuals: numbers of the visuals born from this answer, drawn inside the conversation.
// done: the answer is complete; notice: why it ended badly; failed: provider error; error: request error.
type ChatItem = Message & {
  activity?: Activity[]; meta?: Meta; visuals?: number[]; done?: boolean; notice?: Notice; failed?: string; error?: unknown
}

// Placeholder for features not built yet: visible in the UI, filled in later.
function Todo({ title, children, compact }: { title: string; children?: React.ReactNode; compact?: boolean }) {
  const { t } = useT()
  return (
    <div className={`todo ${compact ? 'compact' : ''}`} title={t('todo.tooltip', { title })}>
      <span className="todo-tag">{t('todo.tag')}</span>
      <span className="todo-title">{title}</span>
      {children && <span className="todo-note">{children}</span>}
    </div>
  )
}

function Logo() {
  return <span className="logo"><span className="logo-mark"><span /></span>Live Insight</span>
}

function Login() {
  const { t } = useT()
  return (
    <main className="login">
      <Logo />
      <p>{t('login.tagline')}</p>
      <a className="button primary" href="/api/auth/login">{t('login.button')}</a>
      <p className="hint">{t('login.hint')}</p>
      <LanguagePicker />
    </main>
  )
}

function VisualCard({ v, onPin }: { v: Visual; onPin: (v: Visual) => void }) {
  const i18n = useT()
  const { t } = i18n
  return (
    <article className={`card ${v.pinned ? 'pinned' : ''}`}>
      <header>
        <span className="badge">{v.n}</span>
        <div className="card-title">
          <h3>{v.title}</h3>
          <div className="kind">
            {kindLabel(t, v.kind)} · {t('card.rows', { count: v.rows.length })}
            {v.filters?.map((f) => filterText(i18n, f)).map((f) => <span key={f} className="chip">{f}</span>)}
          </div>
        </div>
        {/* TODO: change the chart type on the fly */}
        <span className="todo-inline" title={t('card.chartTypeTooltip')}>{t('card.chartType')}</span>
        {v.pinned ? (
          <button className="pin on" onClick={() => onPin(v)}>{t('card.inWorkbook')}<span>{t('card.remove')}</span></button>
        ) : (
          <button className="pin" onClick={() => onPin(v)}><span className="plus">+</span>{t('card.add')}</button>
        )}
      </header>
      <Preview preview={v.preview} />
      {/* TODO: key insights of the visual (peak, minimum, change), also highlighted on the chart */}
      <Todo title={t('card.insightTitle')} compact>{t('card.insightNote')}</Todo>
    </article>
  )
}

function Workbook({ visuals, order, sid, dvaDownload, onChange }: {
  visuals: Visual[]; order: number[]; sid: string; dvaDownload: boolean
  onChange: (order: number[], visual?: Visual) => void
}) {
  const { t, err } = useT()
  const [name, setName] = useState('Live Insight')
  const [busy, setBusy] = useState<'' | 'dva' | 'oac' | 'spec'>('')
  const [error, setError] = useState<unknown>(null)   // raw: written with err() when shown
  const [saved, setSaved] = useState<{ folder: string; name: string } | null>(null)
  const pinned = order.map((n) => visuals.find((v) => v.n === n)!).filter(Boolean)

  const rename = async (v: Visual, title: string) => {
    if (title.trim() && title !== v.title) {
      const r = await api.patchVisual(sid, v.n, { title: title.trim() })
      onChange(r.order, r.visual)
    }
  }
  const run = async (what: 'dva' | 'oac' | 'spec') => {
    setBusy(what)
    setError(null)
    setSaved(null)
    try {
      if (what === 'dva') await api.downloadWorkbook(sid, name.trim())
      else if (what === 'spec') await api.downloadSpec(sid, name.trim())
      else {
        const r = await api.saveToCatalog(sid, name.trim())
        setSaved(r)
      }
    } catch (e) {
      setError(e)
    } finally {
      setBusy('')
    }
  }
  const disabled = !pinned.length || !name.trim() || !!busy

  return (
    <aside className="workbook">
      <div className="wb-head">
        <span className="eyebrow">{t('workbook.eyebrow')}</span>
        <input className="wb-name" value={name} onChange={(e) => setName(e.target.value)} aria-label={t('workbook.nameLabel')} />
        <span className="meta">{pinned.length ? t('workbook.count', { count: pinned.length }) : t('workbook.none')}</span>
      </div>
      <div className="wb-list">
        {pinned.length === 0 ? (
          <p className="empty">{t('workbook.empty')}</p>
        ) : (
          <ol>
            {pinned.map((v, i) => (
              <li key={v.n}>
                <span className="pos">{String(i + 1).padStart(2, '0')}</span>
                <div className="wb-item">
                  <input defaultValue={v.title} onBlur={(e) => rename(v, e.target.value)} aria-label={t('workbook.visualTitleLabel')} />
                  <span className="kind">{kindLabel(t, v.kind)}</span>
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
      <div className="wb-actions">
        {saved && <p className="hint">{t('workbook.saved', saved)}</p>}
        {error ? <p className="error">{err(error)}</p> : null}
        <button className="primary" disabled={disabled} onClick={() => run('oac')}>
          {busy === 'oac' ? t('workbook.saving') : t('workbook.saveOac')}
        </button>
        {/* without a skeleton .dva (see README) the download is off; Save to OAC works anyway */}
        <button className="secondary" disabled={disabled || !dvaDownload} onClick={() => run('dva')}
          title={dvaDownload ? undefined : t('errors.dvaSkeletonMissing')}>
          {busy === 'dva' ? t('workbook.generating') : t('workbook.download')}
        </button>
        {/* the engine's output, platform-neutral: what a platform adapter reads (docs/ARCHITETTURA.md, §1) */}
        <button className="link" disabled={disabled} onClick={() => run('spec')} title={t('workbook.specTooltip')}>
          {t('workbook.downloadSpec')}
        </button>
      </div>
    </aside>
  )
}

export default function App() {
  const { t, tn, err, money } = useT()
  const [me, setMe] = useState<Me | null>(null)
  const [needsLogin, setNeedsLogin] = useState(false)
  const [firstRun, setFirstRun] = useState(false)          // no OAC URL yet: Settings first
  const [showSettings, setShowSettings] = useState(false)
  const [oacUrl, setOacUrl] = useState<string | null>(null)     // to tell, after saving, if the OAC instance changed
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [dataset, setDataset] = useState<Dataset | null>(null)
  const [sid, setSid] = useState('')
  const [chat, setChat] = useState<ChatItem[]>([])
  const [visuals, setVisuals] = useState<Visual[]>([])
  const [order, setOrder] = useState<number[]>([])
  const [cost, setCost] = useState<{ value: number | null; currency: Currency }>({ value: null, currency: null })
  const [question, setQuestion] = useState('')
  const [asking, setAsking] = useState(false)
  const [error, setError] = useState<unknown>(null)   // raw: written with err() when shown
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.me()
      .then((m) => {
        setMe(m)
        if (!m.model) {                           // no model chosen yet: Settings first
          void api.settings().then((v) => { setOacUrl(v.oac_url); setShowSettings(true) })
        }
        return api.datasets()
      })
      .then(setDatasets)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) setNeedsLogin(true)
        else if (e instanceof ApiError && e.status === 503) setFirstRun(true)
        else setError(e)
      })
  }, [])
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })   // returns a Promise in recent browsers: do not return it
  }, [chat])

  const chooseDataset = async (xsa: string) => {
    const ds = datasets.find((d) => d.xsa === xsa) ?? null
    setDataset(ds)
    setChat([]); setVisuals([]); setOrder([]); setCost({ value: null, currency: null }); setSid('')
    if (ds) setSid((await api.newSession(ds.xsa)).id)
  }

  // text: from a clicked follow-up; otherwise from the text box
  const ask = async (text?: string) => {
    const q = (text ?? question).trim()
    if (!q || !sid || asking) return
    if (text === undefined) setQuestion('')
    setAsking(true)
    setChat((c) => [...c, { role: 'user', text: q }, { role: 'assistant', text: '', activity: [], visuals: [] }])
    const update = (f: (last: ChatItem) => ChatItem) => setChat((c) => [...c.slice(0, -1), f(c[c.length - 1])])
    const onEvent = (e: AskEvent) => {
      if (e.kind === 'query') update((m) => ({ ...m, activity: [...(m.activity ?? []), { key: 'activity.query', params: { count: e.data.rows } }] }))
      if (e.kind === 'error') update((m) => ({ ...m, activity: [...(m.activity ?? []), { key: 'activity.retry', error: true }] }))
      if (e.kind === 'followups') update((m) => ({ ...m, followups: e.data.items }))
      if (e.kind === 'visual') {
        setVisuals((vs) => [...vs, e.data])
        update((m) => ({
          ...m,
          visuals: [...(m.visuals ?? []), e.data.n],
          activity: [...(m.activity ?? []), { key: 'activity.visual', params: { n: e.data.n, title: e.data.title }, visual: true }],
        }))
      }
      // "done" always closes the "analyzing…" state, even when the text is empty
      if (e.kind === 'done') update((m) => ({
        ...m, done: true, text: e.data.text,
        notice: e.data.notice ?? (e.data.text ? undefined : 'empty'),
        meta: { seconds: e.data.seconds, steps: e.data.steps, cost: e.data.cost, currency: e.data.currency },
      }))
      if (e.kind === 'failed') update((m) => ({ ...m, done: true, failed: e.data.error }))
    }
    try {
      await api.ask(sid, q, onEvent)
      const s = await api.session(sid)
      setCost({ value: s.cost, currency: s.currency })
    } catch (e) {
      update((m) => ({ ...m, done: true, error: e }))
    } finally {
      setAsking(false)
    }
  }

  const pin = async (v: Visual) => {
    const r = await api.patchVisual(sid, v.n, { pinned: !v.pinned })
    setVisuals((vs) => vs.map((x) => (x.n === v.n ? r.visual : x)))
    setOrder(r.order)
  }

  // After saving: with another OAC instance (or on first run) start again from login; otherwise back to the
  // chat as it was (the new model applies from the next chat).
  const settingsDone = (saved?: SettingsView) => {
    if (saved && (firstRun || saved.oac_url !== oacUrl)) window.location.assign('/')
    else {
      if (saved && me) setMe({ ...me, model: saved.model })
      setShowSettings(false)
    }
  }
  const openSettings = async () => {
    setOacUrl((await api.settings()).oac_url)
    setShowSettings(true)
  }

  if (firstRun) return <Settings firstRun onDone={settingsDone} />
  if (needsLogin) return <Login />
  return (
    <div className="app">
      <header className="top">
        <Logo />
        <span className="spacer" />
        {cost.value !== null && <span className="meta mono">{t('top.session', { cost: money(cost.value, cost.currency) })}</span>}
        {me && <span className="user">{me.name}</span>}
        {me?.model && <span className="pill mono">{me.model}</span>}
        <LanguagePicker />
        <button className="secondary" onClick={openSettings} disabled={showSettings}>{t('top.settings')}</button>
      </header>
      {error ? <p className="error banner">{err(error)}</p> : null}

      {showSettings ? <Settings firstRun={false} onDone={settingsDone} /> : <main className="panes">
        <aside className="sessions">
          <label className="field-ds">
            <span className="eyebrow">{t('sidebar.dataset')}</span>
            <span className="ds-select">
              <span className={`dot ${dataset ? 'on' : ''}`} />
              <select value={dataset?.xsa ?? ''} onChange={(e) => chooseDataset(e.target.value)} aria-label={t('sidebar.dataset')}>
                <option value="" disabled>{t('sidebar.choose')}</option>
                {datasets.map((d) => <option key={d.xsa} value={d.xsa}>{d.name} · {d.folder}</option>)}
              </select>
            </span>
            <span className="hint small">{t('sidebar.hint')}</span>
          </label>
          {/* TODO: session history. Sessions live only in backend memory today: needs an endpoint that lists them. */}
          <Todo title={t('sidebar.historyTitle')}>{t('sidebar.historyNote')}</Todo>
        </aside>

        <section className="chat">
          <div className="thread">
            <div className="thread-inner">
              {!dataset && <p className="hint center">{t('chat.pickDataset')}</p>}
              {dataset && chat.length === 0 && (
                <p className="hint center">{tn('chat.empty', { name: <b key="name">{dataset.name}</b> })}</p>
              )}
              {chat.map((m, i) => m.role === 'user' ? (
                <div key={i} className="msg user">{m.text}</div>
              ) : (
                <div key={i} className="msg assistant">
                  {m.activity && m.activity.length > 0 && (
                    <ul className="activity">
                      {m.activity.map((a, j) => <li key={j} className={a.error ? 'error' : a.visual ? 'visual' : ''}>{t(a.key, a.params)}</li>)}
                    </ul>
                  )}
                  {m.visuals?.map((n) => {
                    const v = visuals.find((x) => x.n === n)
                    return v && <VisualCard key={n} v={v} onPin={pin} />
                  })}
                  {!m.done && <p className="thinking">{t('chat.thinking')}</p>}
                  {m.text && <div className="text"><Markdown>{m.text}</Markdown></div>}
                  {m.notice && <p className="text">{t(`chat.notice.${m.notice}`)}</p>}
                  {m.failed && <p className="text">{t('chat.failed', { error: m.failed })}</p>}
                  {m.error ? <p className="text">{err(m.error)}</p> : null}
                  {m.followups && m.followups.length > 0 && (
                    <div className="followups">
                      {m.followups.map((f) => (
                        <button key={f} className="followup" disabled={asking || !sid} onClick={() => ask(f)}><span>↳</span>{f}</button>
                      ))}
                    </div>
                  )}
                  {m.meta && (
                    <div className="msg-meta">
                      <span className="meta mono">
                        {t('chat.meta', { seconds: m.meta.seconds, count: m.meta.steps, cost: money(m.meta.cost, m.meta.currency) })}
                      </span>
                      {/* TODO: generated SQL (the text is already in the "query" event) */}
                      <span className="todo-inline small" title={t('chat.sqlTooltip')}>{t('chat.sql')}</span>
                    </div>
                  )}
                </div>
              ))}
              <div ref={bottom} />
            </div>
          </div>
          <form className="ask" onSubmit={(e) => { e.preventDefault(); void ask() }}>
            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void ask() } }}
              placeholder={dataset ? t('ask.placeholder') : t('ask.placeholderNoDataset')}
              disabled={!sid || asking}
              rows={2}
            />
            <div className="ask-bar">
              {dataset && <span className="meta">{dataset.name}</span>}
              <span className="spacer" />
              <span className="meta mono hide-narrow">{t('ask.hint')}</span>
              <button className="primary" disabled={!sid || asking || !question.trim()}>{t('ask.send')}</button>
            </div>
          </form>
        </section>

        {sid ? <Workbook visuals={visuals} order={order} sid={sid} dvaDownload={me?.dva_download ?? false} onChange={(o, v) => {
          setOrder(o)
          if (v) setVisuals((vs) => vs.map((x) => (x.n === v.n ? v : x)))
        }} /> : <aside className="workbook" />}
      </main>}
    </div>
  )
}
