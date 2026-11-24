import { useEffect, useRef, useState, type FormEvent } from 'react'
import {
  defaultCallingName,
  fieldValue,
  loadSupabaseProfileStore,
  saveOnboarding,
  type LoadedProfile,
  type ProfileStore,
} from '../lib/profile'
import { checkPhone, formatUsPhone } from './phone'
import type { ProfileField } from './types'

interface OnboardingStepProps {
  /** The session's user; the rows are written under this id (RLS: own rows only). */
  userId: string
  /** Fields already stored, if any, to prefill the form. */
  initial?: ProfileField[]
  /** Called once the profile is saved; the screen then shows the chat with it. */
  onSaved: (profile: LoadedProfile) => void
  /** Injected in tests. */
  loadStore?: () => Promise<ProfileStore>
}

/**
 * One-time onboarding (ON-2, ON-3, ON-8): shown over the chat on the first visit in API
 * mode, before anything else. Full name, calling name, phone and email in; saving is also
 * the acknowledgment of how the agent works. All behaviour is in `lib/profile.ts`; this
 * component only renders its results. There is no "not now": a call needs the profile.
 */
export function OnboardingStep({ userId, initial = [], onSaved, loadStore = loadSupabaseProfileStore }: OnboardingStepProps) {
  const [fullName, setFullName] = useState(fieldValue(initial, 'full_name') ?? '')
  // The calling name follows the full name until the user edits it.
  const [callingName, setCallingName] = useState<string | null>(fieldValue(initial, 'calling_name'))
  const [phone, setPhone] = useState(fieldValue(initial, 'phone') ?? '')
  const [email, setEmail] = useState(fieldValue(initial, 'email') ?? '')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const nameInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    nameInput.current?.focus()
  }, [])

  const shownCallingName = callingName ?? defaultCallingName(fullName)
  const phoneCheck = checkPhone(phone)

  async function save(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setProblem(null)
    const result = await saveOnboarding(await loadStore(), userId, { fullName, callingName: shownCallingName, phone, email })
    setBusy(false)
    if (result.kind === 'failed') {
      setProblem(result.message)
      return
    }
    onSaved(result.profile)
  }

  return (
    <div className="precall-overlay">
      <section className="precall-dialog" role="dialog" aria-modal="true" aria-labelledby="precall-onboarding-title">
        <form onSubmit={(e) => void save(e)}>
          <h2 id="precall-onboarding-title">Set up your profile</h2>
          <p className="precall-small">
            On every call the agent says it's an AI calling for you, says the call is transcribed, calls from our number,
            and pauses to ask you when it isn't sure. Saving means that's fine with you.
          </p>
          <label className="precall-field">
            <span>Your name</span>
            <input
              ref={nameInput}
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              autoComplete="name"
              placeholder="Alex Chen"
              required
            />
          </label>
          <label className="precall-field">
            <span>Name the agent uses for you</span>
            <input
              type="text"
              value={shownCallingName}
              onChange={(e) => setCallingName(e.target.value)}
              autoComplete="given-name"
              placeholder="Alex"
              required
            />
            <small>Said on every call: "calling on behalf of {shownCallingName || '…'}".</small>
          </label>
          <label className="precall-field">
            <span>Your phone</span>
            <input
              type="tel"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              autoComplete="tel"
              inputMode="tel"
              placeholder="(617) 555-0142"
              required
            />
            {phoneCheck.ok && <small>{formatUsPhone(phoneCheck.e164)}</small>}
          </label>
          <label className="precall-field">
            <span>Email</span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              inputMode="email"
              placeholder="you@example.com"
              required
            />
          </label>
          {problem && <p className="precall-warn">{problem}</p>}
          <div className="precall-foot precall-actions">
            <button type="submit" className="precall-btn primary" disabled={busy}>
              {busy ? 'Saving…' : 'Save and continue'}
            </button>
          </div>
        </form>
      </section>
    </div>
  )
}
