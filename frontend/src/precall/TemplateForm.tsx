import { useId, useState, type FormEvent, type KeyboardEvent } from 'react'
import { LIST_MAX_ITEMS, type CallDraft, type FormSubmission, type ListKey } from './chat'
import {
  STEPPER_DEFAULT,
  STEPPER_MAX,
  STEPPER_MIN,
  formatAnswer,
  parseAnswer,
  suggestionsFor,
  widgetFor,
} from './formFields'
import { describeProfileKeys, knownProfileFields, missingTemplateFields } from './missing'
import { formatUsPhone } from './phone'
import type { CallTemplate, ProfileField, TemplateField } from './types'

interface TemplateFormProps {
  template: CallTemplate
  profile: ProfileField[]
  /** What the chat already knows: empty for a new call, filled in when the user edits the plan. */
  draft: Partial<CallDraft>
  /** Why the last "Review plan" did not go through. */
  error: string | null
  onSubmit: (form: FormSubmission) => void
}

type Lists = Record<ListKey, string[]>

/** The form's list sections, in the user's words. `hint` is the line under the title. */
const LIST_SECTIONS: { key: ListKey; title: string; hint: string; placeholder: string }[] = [
  { key: 'constraints', title: 'Must-haves', hint: 'It only works for me if…', placeholder: 'e.g. vegetarian options' },
  { key: 'preferences', title: 'Nice to have', hint: 'Great if possible, not required.', placeholder: 'e.g. a window seat' },
  {
    key: 'fallbacks',
    title: "If that's not available, I'm OK with",
    hint: "I'll take one of these without asking you.",
    placeholder: 'e.g. within an hour of that time',
  },
  {
    key: 'never_agree',
    title: 'Never agree to',
    hint: "I'll check with you before agreeing to any of these.",
    placeholder: 'Add something else',
  },
]

function texts(items: { text: string }[] | undefined): string[] {
  return (items ?? []).map((i) => i.text)
}

/** Each call field's input value: refilled from the plan being edited, or the field's starting value. */
function initialAnswers(fields: TemplateField[], saved: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {}
  for (const field of fields) {
    const widget = widgetFor(field.key)
    const text = saved[field.key]
    out[field.key] = text ? parseAnswer(widget, text) : widget === 'stepper' ? STEPPER_DEFAULT : ''
  }
  return out
}

/**
 * The template form (PC-2, PC-4, PC-5): one card in the chat instead of a question for each
 * detail. Shows only what the profile cannot answer, then the three lists in plain words.
 * Everything is checked by the chat when the user taps Review plan (`submit_form`).
 */
export function TemplateForm({ template, profile, draft, error, onSubmit }: TemplateFormProps) {
  const uid = useId()
  const callFields = template.required_fields.filter((f) => f.source === 'call')
  const profileGaps = missingTemplateFields(template, profile, {}).filter((f) => f.source === 'profile')
  const known = knownProfileFields(template, profile)
  const suggestions = suggestionsFor(template.key)

  const [businessName, setBusinessName] = useState(draft.business_name ?? '')
  const [phone, setPhone] = useState(draft.phone_e164 ? formatUsPhone(draft.phone_e164) : '')
  const [answers, setAnswers] = useState(() => initialAnswers(callFields, draft.answers ?? {}))
  const [profileValues, setProfileValues] = useState<Record<string, string>>(() =>
    Object.fromEntries((draft.profile_additions ?? []).map((f) => [f.key, f.value])),
  )
  const [lists, setLists] = useState<Lists>(() => ({
    constraints: texts(draft.constraints),
    preferences: texts(draft.preferences),
    fallbacks: texts(draft.fallbacks),
    never_agree: texts(draft.never_agree),
  }))
  const [pending, setPending] = useState<Record<ListKey, string>>({
    constraints: '',
    preferences: '',
    fallbacks: '',
    never_agree: '',
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    // Text typed in a list box but not added yet still counts.
    const withPending = (key: ListKey) => (pending[key].trim() === '' ? lists[key] : [...lists[key], pending[key]])
    onSubmit({
      business_name: businessName,
      phone,
      answers: Object.fromEntries(callFields.map((f) => [f.key, formatAnswer(widgetFor(f.key), answers[f.key] ?? '')])),
      profile_values: profileValues,
      constraints: withPending('constraints'),
      preferences: withPending('preferences'),
      fallbacks: withPending('fallbacks'),
      never_agree: withPending('never_agree'),
    })
  }

  function setAnswer(key: string, value: string) {
    setAnswers((prev) => ({ ...prev, [key]: value }))
  }

  return (
    <form className="precall-form" aria-label={template.label} onSubmit={submit} noValidate>
      <h2>{template.label}</h2>

      {profileGaps.length > 0 && (
        <fieldset className="precall-fs">
          <legend>About you</legend>
          {profileGaps.map((field) => (
            <label key={field.key} className="precall-field">
              <span>{field.question}</span>
              <input
                type="text"
                value={profileValues[field.key] ?? ''}
                onChange={(e) => setProfileValues((prev) => ({ ...prev, [field.key]: e.target.value }))}
                autoComplete="off"
              />
            </label>
          ))}
          <span className="precall-muted">Missing from your profile. Saved so you don't type it again.</span>
        </fieldset>
      )}

      <fieldset className="precall-fs">
        <legend>Who to call</legend>
        <div className="precall-fgrid">
          <label className="precall-field">
            <span>Name of the place</span>
            <input type="text" value={businessName} onChange={(e) => setBusinessName(e.target.value)} autoComplete="off" />
          </label>
          <label className="precall-field">
            <span>Phone number</span>
            <input
              type="tel"
              inputMode="tel"
              value={phone}
              placeholder="(617) 555-0123"
              onChange={(e) => setPhone(e.target.value)}
              autoComplete="off"
            />
          </label>
        </div>
      </fieldset>

      <fieldset className="precall-fs">
        <legend>The details</legend>
        <div className="precall-fgrid">
          {callFields.map((field) => (
            <CallField
              key={field.key}
              field={field}
              value={answers[field.key] ?? ''}
              onChange={(value) => setAnswer(field.key, value)}
            />
          ))}
        </div>
        {known.length > 0 && (
          <span className="precall-known">✓ Using {describeProfileKeys(known)} from your profile</span>
        )}
      </fieldset>

      {LIST_SECTIONS.map((section) => (
        <ListEditor
          key={section.key}
          id={`${uid}-${section.key}`}
          title={section.title}
          hint={section.hint}
          placeholder={section.placeholder}
          items={lists[section.key]}
          pending={pending[section.key]}
          suggestions={section.key === 'never_agree' ? [] : suggestions[section.key]}
          onItems={(items) => setLists((prev) => ({ ...prev, [section.key]: items }))}
          onPending={(text) => setPending((prev) => ({ ...prev, [section.key]: text }))}
        />
      ))}

      {error && (
        <p className="precall-warn" role="alert">
          {error}
        </p>
      )}
      <div className="precall-foot">
        <button type="submit" className="precall-btn primary">
          Review plan
        </button>
      </div>
    </form>
  )
}

function CallField({
  field,
  value,
  onChange,
}: {
  field: TemplateField
  value: string
  onChange: (value: string) => void
}) {
  const widget = widgetFor(field.key)
  if (widget === 'stepper') {
    const n = Number(value) || Number(STEPPER_DEFAULT)
    const set = (next: number) => onChange(String(Math.min(STEPPER_MAX, Math.max(STEPPER_MIN, next))))
    return (
      <div className="precall-field" role="group" aria-label={field.question}>
        <span>{field.question}</span>
        <div className="precall-stepper">
          <button type="button" aria-label="One fewer" onClick={() => set(n - 1)} disabled={n <= STEPPER_MIN}>
            −
          </button>
          <output aria-live="polite">{n}</output>
          <button type="button" aria-label="One more" onClick={() => set(n + 1)} disabled={n >= STEPPER_MAX}>
            +
          </button>
        </div>
      </div>
    )
  }
  return (
    <label className="precall-field">
      <span>{field.question}</span>
      <input
        type={widget === 'text' ? 'text' : widget}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete="off"
      />
      {widget === 'text' && (field.suggestions?.length ?? 0) > 0 && (
        <span className="precall-sugg">
          {field.suggestions?.map((text) => (
            <button type="button" key={text} className="precall-chip quiet" onClick={() => onChange(text)}>
              {text}
            </button>
          ))}
        </span>
      )}
    </label>
  )
}

function ListEditor({
  id,
  title,
  hint,
  placeholder,
  items,
  pending,
  suggestions,
  onItems,
  onPending,
}: {
  id: string
  title: string
  hint: string
  placeholder: string
  items: string[]
  pending: string
  suggestions: string[]
  onItems: (items: string[]) => void
  onPending: (text: string) => void
}) {
  const full = items.length >= LIST_MAX_ITEMS

  function add(text: string) {
    const value = text.trim()
    if (value === '' || full || items.some((i) => i.toLowerCase() === value.toLowerCase())) return
    onItems([...items, value])
    onPending('')
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== 'Enter') return
    event.preventDefault() // Enter adds the item; it does not review the plan.
    add(pending)
  }

  const shown = suggestions.filter((s) => !items.some((i) => i.toLowerCase() === s.toLowerCase()))
  return (
    <fieldset className="precall-fs">
      <legend>{title}</legend>
      <span className="precall-muted">{hint}</span>
      {items.length > 0 && (
        <div className="precall-tags">
          {items.map((item, i) => (
            <span key={`${i}-${item}`} className="precall-tg">
              {item}
              <button
                type="button"
                className="precall-x"
                aria-label={`Remove ${item}`}
                onClick={() => onItems(items.filter((_, index) => index !== i))}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="precall-addrow">
        <input
          id={`${id}-in`}
          type="text"
          value={pending}
          placeholder={placeholder}
          aria-label={`Add to ${title}`}
          disabled={full}
          onChange={(e) => onPending(e.target.value)}
          onKeyDown={onKeyDown}
          autoComplete="off"
        />
        <button type="button" className="precall-btn" onClick={() => add(pending)} disabled={full || pending.trim() === ''}>
          Add
        </button>
      </div>
      {shown.length > 0 && !full && (
        <span className="precall-sugg">
          {shown.map((text) => (
            <button type="button" key={text} className="precall-chip quiet" onClick={() => add(text)}>
              {text}
            </button>
          ))}
        </span>
      )}
    </fieldset>
  )
}
