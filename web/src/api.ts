// Backend calls (liveinsight/api.py). Chat answers are streamed (Server-Sent Events): the browser's EventSource
// only does GET, so the fetch stream is read and events are split by hand.

import type { Msg } from './i18n'

export type Me = {
  name: string; dev: boolean; token_minutes_left: number
  model: string | null                                    // null: no model chosen yet, the UI opens Settings
  dva_download: boolean                                   // a skeleton .dva is configured (README)
}
export type SettingsView = {
  oac_url: string | null
  model: string | null
  oci_compartment: string | null                          // OCI compartment OCID (native Generative AI API)
  custom_base_url: string | null                          // the custom provider's OpenAI-compatible server
  oci_region: string
  keyless: string[]                                       // providers whose API key is optional
  keys_set: Record<string, boolean>                       // keys never come back: only whether they are set
  providers: { id: string; label: string; models: { id: string; label: string; note?: string }[] }[]   // note: a UI key
}
export type ModelChoice = { model: string; api_key?: string; oci_compartment?: string; custom_base_url?: string }
export type ProbeResult = { ok: boolean; detail: Msg; seconds: number; cost?: number | null; currency?: Currency; api: string }
export type Dataset = { name: string; xsa: string; folder: string }
export type Column = { id: string; label: string; type: 'quantitative' | 'nominal' | 'ordinal'; grain?: string }
export type Row = Record<string, string | number | null>

// stand_in: the preview is not the OAC visual but a stand-in (map, radar, box plot, narrative); the note says which.
// Labels and spec strings can hold ⟦key⟧ tokens: draw only what useT().localize returns.
type PreviewNote = { note?: Msg[]; stand_in?: boolean }
export type Preview = PreviewNote & (
  | { renderer: 'vega'; spec: Record<string, unknown> }
  | { renderer: 'table'; columns: Column[]; rows: Row[] }
  | { renderer: 'pivot'; row: Column; col: Column; value: Column; rows: Row[] }
  | { renderer: 'tile'; label: string; value: number }
)

export type Visual = {
  n: number
  kind: string
  title: string
  pinned: boolean
  columns: Column[]
  rows: Row[]
  preview: Preview
  filters: Filter[]                                       // visual filters, written by the UI in its language
}

export type Filter = { column: string; op: 'in' | 'between'; values: (string | number)[] }

export type Currency = 'USD' | 'EUR' | null           // currency of the model's price list (OCI in euro)
export type Message = { role: 'user' | 'assistant'; text: string; followups?: string[] }
export type SessionState = {
  dataset: string; messages: Message[]; visuals: Visual[]; order: number[]; cost: number | null; currency: Currency
}

export type AskEvent =
  | { kind: 'query'; data: { query: string; rows: number } }
  | { kind: 'visual'; data: Visual }
  | { kind: 'error'; data: { tool: string; error: string } }
  | { kind: 'followups'; data: { items: string[] } }   // follow-ups: buttons that send the request
  | { kind: 'done'; data: { text: string; seconds: number; steps: number; cost: number | null; currency: Currency;
                          followups: string[]; notice: Notice | null } }
  | { kind: 'failed'; data: { error: string } }

// Why a turn ended without a normal answer: the UI says it in its language (chat.notice.*).
export type Notice = 'empty' | 'tooManySteps' | 'refused' | 'truncated'

// detail: the backend's message ({key, params}) when it sends one; the UI writes it with useT().err(e).
export class ApiError extends Error {
  status: number
  detail?: Msg
  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : JSON.stringify(detail))
    this.status = status
    if (typeof detail === 'object' && detail !== null && 'key' in detail) this.detail = detail as Msg
  }
}

const detailOf = (r: Response) => r.json().then((j) => j.detail, () => r.statusText)

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!r.ok) throw new ApiError(r.status, await detailOf(r))
  return r.json() as Promise<T>
}

export const api = {
  me: () => call<Me>('GET', '/api/me'),                    // 503: no OAC URL yet, Settings open
  settings: () => call<SettingsView>('GET', '/api/settings'),
  saveSettings: (s: ModelChoice & { oac_url: string }) => call<SettingsView>('PUT', '/api/settings', s),
  // a test call with a tool, without saving (a few tokens)
  verifyModel: (s: ModelChoice) => call<ProbeResult>('POST', '/api/settings/verify', s),
  datasets: () => call<Dataset[]>('GET', '/api/datasets'),
  newSession: (xsa: string) => call<{ id: string; dataset: Dataset }>('POST', '/api/sessions', { xsa }),
  session: (sid: string) => call<SessionState>('GET', `/api/sessions/${sid}`),
  patchVisual: (sid: string, n: number, patch: { pinned?: boolean; title?: string }) =>
    call<{ visual: Visual; order: number[] }>('PATCH', `/api/sessions/${sid}/visuals/${n}`, patch),
  logout: () => call<{ ok: boolean }>('POST', '/api/auth/logout'),
  // Writes to OAC (test folder only); the name can come back with " (2)" if it was taken
  saveToCatalog: (sid: string, name: string) =>
    call<{ name: string; folder: string }>('POST', `/api/sessions/${sid}/catalog`, { name }),

  async ask(sid: string, question: string, onEvent: (e: AskEvent) => void): Promise<void> {
    const r = await fetch(`/api/sessions/${sid}/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    })
    if (!r.ok || !r.body) throw new ApiError(r.status, await detailOf(r))
    const reader = r.body.pipeThrough(new TextDecoderStream()).getReader()
    let buffer = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += value
      let end
      while ((end = buffer.indexOf('\n\n')) >= 0) {            // an SSE event ends with an empty line
        const block = buffer.slice(0, end)
        buffer = buffer.slice(end + 2)
        const kind = block.match(/^event: (.*)$/m)?.[1]
        const data = block.match(/^data: (.*)$/m)?.[1]
        if (kind && data) onEvent({ kind, data: JSON.parse(data) } as AskEvent)
      }
    }
  },

  // The .dva comes as a file: downloaded through a temporary link.
  downloadWorkbook: (sid: string, name: string) => download(`/api/sessions/${sid}/workbook`, name, `${name}.dva`),
  // The WorkbookSpec JSON: what the engine hands to a platform adapter (docs/ARCHITETTURA.md, §1).
  downloadSpec: (sid: string, name: string) =>
    download(`/api/sessions/${sid}/spec`, name, `${name}.workbook-spec.json`),
}

async function download(path: string, name: string, filename: string): Promise<void> {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  })
  if (!r.ok) throw new ApiError(r.status, await detailOf(r))
  const url = URL.createObjectURL(await r.blob())
  const a = Object.assign(document.createElement('a'), { href: url, download: filename })
  a.click()
  URL.revokeObjectURL(url)
}
