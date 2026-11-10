import type { ReactNode } from 'react'
import { openingLine, profileWithAdditions, type BuiltBrief } from './brief'
import { canEdit, type CallDraft, type EditTarget, type ListItem } from './chat'
import { profileLabel } from './missing'
import { formatUsPhone } from './phone'
import { reviewView, type ServerCheck } from './start'
import type { ProfileField } from './types'

interface ReviewCardProps {
  review: BuiltBrief
  draft: CallDraft
  profile: ProfileField[]
  started: boolean
  /** How the review ended: a call placed, or the brief only saved (calling not connected yet). */
  outcome?: 'calling' | 'saved' | null
  /** The server's check of this brief, if any. It wins over the local preview while the brief is unchanged. */
  server?: ServerCheck | null
  /** A save or start request is in flight: Start and Edit are disabled. */
  busy?: boolean
  onEdit: (target: EditTarget) => void
  onStart: () => void
}

/**
 * The pre-call review (PC-6): the last checkpoint before dialing. Shows the
 * goal, the number, the exact opening line, what the agent may share (freely
 * vs. ask first), limits, preferences, ranked backups and never-agree items.
 * Built to be read in about 10 seconds. Layout follows the "Check before I
 * dial" card in Harper's prototype.
 */
export function ReviewCard({
  review,
  draft,
  profile,
  started,
  outcome = 'calling',
  server = null,
  busy = false,
  onEdit,
  onStart,
}: ReviewCardProps) {
  const { brief } = review
  const local = { missing_fields: review.missing_fields, opening_line: openingLine(brief, profileWithAdditions(draft, profile)) }
  const { missing_fields, opening_line: opening } = reviewView(brief, local, server)
  const details = Object.values(draft.answers).filter((v) => v.trim() !== '')
  const canStart = !started && !busy && missing_fields.length === 0 && opening !== null

  function editButton(target: EditTarget, label: string) {
    if (started || !canEdit(draft, target)) return null
    return (
      <button
        type="button"
        className="precall-edit"
        onClick={() => onEdit(target)}
        disabled={busy}
        aria-label={`Edit ${label}`}
      >
        Edit
      </button>
    )
  }

  return (
    <section className="precall-review" aria-label="Check before I dial">
      <h2>Check before I dial</h2>
      <dl>
        <Row label="Goal" edit={editButton(draft.template_key === null ? 'goal' : 'details', 'goal')}>
          <b>{brief.goal}</b>
          {details.length > 0 && <div className="precall-small">{details.join(' · ')}</div>}
        </Row>
        <Row label="Calling" edit={editButton('calling', 'who to call')}>
          {brief.business_name}, {formatUsPhone(brief.phone_e164)}
        </Row>
        <Row label="I'll open with">
          {opening ? <div className="precall-say">“{opening}”</div> : <span className="precall-muted">Not ready yet</span>}
        </Row>
        <Row label="I may share">
          <Tags items={brief.share_freely_keys.map(profileLabel)} kind="free" empty="Nothing" />
        </Row>
        <Row label="I'll ask first">
          <Tags items={brief.ask_first_keys.map(profileLabel)} kind="ask" empty="Nothing" />
        </Row>
        <Row label="Must" edit={editButton('constraints', 'limits')}>
          <Tags items={texts(brief.constraints)} empty="No limits" />
        </Row>
        <Row label="Would like" edit={editButton('preferences', 'preferences')}>
          <Tags items={texts(brief.preferences)} empty="No preferences" />
        </Row>
        <Row label="Backups" edit={editButton('fallbacks', 'backups')}>
          <Tags items={texts(brief.fallbacks)} numbered empty="None, I'll ask you" />
        </Row>
        <Row label="Never agree to" edit={editButton('never_agree', 'what not to agree to')}>
          <Tags items={texts(brief.never_agree)} empty="Nothing listed" />
          <div className="precall-small">I'll always check with you before any deposit or fee.</div>
        </Row>
      </dl>

      {missing_fields.length > 0 && !started && (
        <p className="precall-warn" role="alert">
          Still needed before I can call: {missing_fields.map(profileLabel).join(', ')}.
        </p>
      )}

      {started ? (
        <p className="precall-done">{outcome === 'saved' ? '✓ Brief saved' : '✓ Call started'}</p>
      ) : (
        <div className="precall-foot">
          <button type="button" className="precall-btn primary" onClick={onStart} disabled={!canStart} aria-busy={busy}>
            {busy ? 'Saving…' : 'Start call'}
          </button>
        </div>
      )}
    </section>
  )
}

function texts(items: ListItem[]): string[] {
  return items.map((i) => i.text)
}

function Row({ label, edit, children }: { label: string; edit?: ReactNode; children: ReactNode }) {
  return (
    <div className="precall-rv">
      <dt>{label}</dt>
      <dd>{children}</dd>
      <div className="precall-rv-edit">{edit}</div>
    </div>
  )
}

function Tags({
  items,
  kind,
  numbered,
  empty,
}: {
  items: string[]
  kind?: 'free' | 'ask'
  numbered?: boolean
  empty: string
}) {
  if (items.length === 0) return <span className="precall-muted">{empty}</span>
  return (
    <div className="precall-tags">
      {items.map((item, i) => (
        <span key={`${i}-${item}`} className={`precall-tg${kind ? ` ${kind}` : ''}`}>
          {numbered && <b>{i + 1}</b>}
          {item}
        </span>
      ))}
    </div>
  )
}
