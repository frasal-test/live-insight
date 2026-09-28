// Preview of a proposed visual. The spec comes from the backend (liveinsight/engine/preview.py), already with the Tufte
// theme: here we only draw. Vega-Lite for charts; table, pivot and tile in HTML.
import { useEffect, useMemo, useRef } from 'react'
import embed, { type Result } from 'vega-embed'
import type { Column, Preview as PreviewT, Row } from './api'
import { useT } from './i18n'

// Numbers inside charts follow the UI language (d3-format locale, derived from Intl so it stays consistent).
function d3Locale(lang: string) {
  const parts = new Intl.NumberFormat(lang).formatToParts(12345.6)
  const find = (type: string) => parts.find((p) => p.type === type)?.value ?? ''
  return { decimal: find('decimal') || '.', thousands: find('group') || ',', grouping: [3], currency: ['', ''] as [string, string] }
}

function formatValue(number: Intl.NumberFormat, v: Row[string], col?: Column): string {
  if (v === null || v === undefined) return '—'
  return typeof v === 'number' && col?.type === 'quantitative' ? number.format(v) : String(v)
}

function VegaChart({ spec }: { spec: Record<string, unknown> }) {
  const { lang, t } = useT()
  const el = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!el.current) return
    let result: Result | undefined
    let cancelled = false
    embed(el.current, spec as never, { actions: false, renderer: 'svg', formatLocale: d3Locale(lang) })
      .then((r) => (cancelled ? r.finalize() : (result = r)))
      .catch((e) => el.current && (el.current.textContent = t('preview.unavailable', { error: e.message })))
    return () => {
      cancelled = true
      result?.finalize()                        // vega-embed docs: releases timers and listeners
    }
  }, [spec, lang, t])
  return <div className="chart" ref={el} />
}

function Table({ columns, rows }: { columns: Column[]; rows: Row[] }) {
  const { number } = useT()
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{columns.map((c) => <th key={c.id} className={c.type === 'quantitative' ? 'num' : ''}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c.id} className={c.type === 'quantitative' ? 'num' : ''}>{formatValue(number, r[c.id], c)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Pivot({ row, col, value, rows }: { row: Column; col: Column; value: Column; rows: Row[] }) {
  const { number } = useT()
  const cols = [...new Set(rows.map((r) => String(r[col.id])))]
  const keys = [...new Set(rows.map((r) => String(r[row.id])))]
  const cell = new Map(rows.map((r) => [`${r[row.id]}|${r[col.id]}`, r[value.id]]))
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>{row.label}</th>
            {cols.map((c) => <th key={c} className="num">{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => (
            <tr key={k}>
              <td>{k}</td>
              {cols.map((c) => <td key={c} className="num">{formatValue(number, cell.get(`${k}|${c}`) ?? null, value)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="caption">{value.label}</p>
    </div>
  )
}

export default function Preview({ preview: raw }: { preview: PreviewT }) {
  const { t, number, tm, localize } = useT()
  // ⟦key⟧ tokens in titles and labels become words of the UI language; a language switch redraws with no call
  const preview = useMemo(() => localize(raw), [raw, localize])
  const note = preview.note?.map(tm).join(' ')
  return (
    <figure className="preview">
      {preview.stand_in && note && <figcaption className="stand-in">{t('preview.standIn', { note })}</figcaption>}
      {preview.renderer === 'vega' && <VegaChart spec={preview.spec} />}
      {preview.renderer === 'table' && <Table columns={preview.columns} rows={preview.rows} />}
      {preview.renderer === 'pivot' && <Pivot {...preview} />}
      {preview.renderer === 'tile' && (
        <div className="tile">
          <span className="tile-value">{number.format(preview.value)}</span>
          <span className="tile-label">{preview.label}</span>
        </div>
      )}
      {note && !preview.stand_in && <figcaption>{note}</figcaption>}
    </figure>
  )
}
