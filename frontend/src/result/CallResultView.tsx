import { formatCallDate, outcomeDisplay } from '../history/format'
import { actionLabel, agreedRows, disclosureLabel, fieldLabel } from './format'
import { TranscriptView } from './TranscriptView'
import type { CallResult } from './types'
import '../history/CallHistory.css'
import './CallResultView.css'

export interface CallResultViewProps {
  result: CallResult
  /** Called when the user goes back to the list. Omit to hide the back button. */
  onBack?: () => void
}

/** Outcomes that claim something was arranged, so they need the business's confirmation. */
const NEEDS_CONFIRMATION = new Set(['booked', 'rescheduled', 'cancelled'])

/**
 * End-of-call result page: outcome (PO-1), what was agreed, open items and
 * decisions (PO-2), what was shared (PO-3) and the transcript (PO-4).
 * Wide screens show the summary and the transcript side by side.
 */
export function CallResultView({ result, onBack }: CallResultViewProps) {
  const { call } = result
  const outcome = outcomeDisplay(call)
  const agreed = agreedRows(result.details)
  const unconfirmed = call.outcome !== null && NEEDS_CONFIRMATION.has(call.outcome) && !result.businessConfirmed

  return (
    <article className="cr" aria-labelledby="cr-title">
      {onBack && (
        <button type="button" className="cr-back" onClick={onBack}>
          ← All calls
        </button>
      )}

      <div className="cr-layout">
        <div className="cr-main">
          <header className="cr-head">
            <span className={`ch-badge ch-badge-${outcome.tone}`}>{outcome.label}</span>
            <h2 id="cr-title" className="cr-title">
              {call.businessName}
            </h2>
            <p className="cr-sub">
              <time dateTime={call.createdAt}>{formatCallDate(call.createdAt)}</time>
            </p>
            <p className="cr-brief">
              <span className="cr-brief-label">Your brief</span>
              {call.goal}
            </p>
            {unconfirmed && (
              <p className="cr-warn" role="note">
                The business didn’t clearly confirm this. Check with them before you rely on it.
              </p>
            )}
          </header>

          {agreed.length > 0 && (
            <section className="cr-group" aria-labelledby="cr-agreed">
              <h3 id="cr-agreed">What was agreed</h3>
              <dl className="cr-agreed">
                {agreed.map(([label, value]) => (
                  <div key={label}>
                    <dt>{label}</dt>
                    <dd>{value}</dd>
                  </div>
                ))}
              </dl>
            </section>
          )}

          {result.openItems.length > 0 && (
            <section className="cr-group cr-open" aria-labelledby="cr-open">
              <h3 id="cr-open">Open items</h3>
              <ul className="cr-plain">
                {result.openItems.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </section>
          )}

          <section className="cr-group" aria-labelledby="cr-decisions">
            <h3 id="cr-decisions">
              Decisions and reasons
              {result.decisions.length > 0 && <span className="cr-count">{result.decisions.length}</span>}
            </h3>
            {result.decisions.length === 0 ? (
              <p className="cr-muted">The agent didn’t make any decisions on its own.</p>
            ) : (
              <ul className="cr-decisions">
                {result.decisions.map((d, i) => (
                  <li key={i}>
                    <details>
                      <summary>
                        <span className="cr-action">{actionLabel(d.action)}</span>
                        <span className="cr-situation">{d.situation}</span>
                        {d.missedPreference && <span className="cr-missed-dot" title="Missed a preference" />}
                      </summary>
                      <div className="cr-why">
                        <p>{d.reason}</p>
                        {d.missedPreference && <p className="cr-missed">Couldn’t meet: {d.missedPreference}</p>}
                      </div>
                    </details>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {result.sharedFields.length > 0 && (
            <section className="cr-group" aria-labelledby="cr-shared">
              <h3 id="cr-shared">What was shared</h3>
              <ul className="cr-shared">
                {result.sharedFields.map((f) => (
                  <li key={f.fieldKey}>
                    <b>{fieldLabel(f.fieldKey)}</b>
                    <span>{disclosureLabel(f.state)}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>

        <aside className="cr-side">
          <TranscriptView lines={result.transcript} businessName={call.businessName} />
        </aside>
      </div>
    </article>
  )
}
