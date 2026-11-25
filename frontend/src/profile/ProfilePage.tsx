import { useState, type FormEvent, type ReactNode } from 'react'
import {
  defaultCallingName,
  editInputFrom,
  loadSupabaseProfileStore,
  saveProfileEdit,
  type LoadedProfile,
  type ProfileEditInput,
  type ProfileStore,
  type ShareableKey,
} from '../lib/profile'
import { checkPhone, formatUsPhone } from '../precall/phone'
import type { Sharing } from '../precall/types'
import './ProfilePage.css'

interface ProfilePageProps {
  profile: LoadedProfile
  /** The session's user. Null in demo mode, where nothing can be saved. */
  userId: string | null
  /** Called with the stored profile after a successful save. */
  onSaved: (profile: LoadedProfile) => void
  /** True when onboarding hasn't been done yet: saving here also completes the profile. */
  notOnboarded?: boolean
  /** Shown under the heading, above the form: the account and its sign-in. */
  accountCard?: ReactNode
  /** Injected in tests. */
  loadStore?: () => Promise<ProfileStore>
}

const SHARE_LABELS: Record<ShareableKey, string> = {
  full_name: 'Full name',
  phone: 'Phone',
  email: 'Email',
  date_of_birth: 'Date of birth',
  address: 'Home address',
}

/** One "Share freely / Ask me first" switch (ON-5). */
function SharingChoice({ label, value, onChange }: { label: string; value: Sharing; onChange: (next: Sharing) => void }) {
  return (
    <div className="profile-share" role="radiogroup" aria-label={`Sharing for ${label}`}>
      <button type="button" role="radio" aria-checked={value === 'share_freely'} className={value === 'share_freely' ? 'on' : ''} onClick={() => onChange('share_freely')}>
        Share freely
      </button>
      <button type="button" role="radio" aria-checked={value === 'ask_first'} className={value === 'ask_first' ? 'on ask' : ''} onClick={() => onChange('ask_first')}>
        Ask me first
      </button>
    </div>
  )
}

/**
 * The editable profile (ON-7): the basic fields (ON-3) and, for each, whether the agent may
 * say it freely or must ask first (ON-5). Changes apply to future calls. All checking and
 * saving is in `lib/profile.ts`; this component renders it.
 */
export function ProfilePage({ profile, userId, onSaved, notOnboarded = false, accountCard, loadStore = loadSupabaseProfileStore }: ProfilePageProps) {
  const [form, setForm] = useState<ProfileEditInput>(() => {
    const initial = editInputFrom(profile.fields)
    // Stored as E.164; shown the way people write it.
    return { ...initial, phone: initial.phone.startsWith('+') ? formatUsPhone(initial.phone) : initial.phone }
  })
  // The calling name follows the full name until the user edits it.
  const [callingNameEdited, setCallingNameEdited] = useState(form.callingName !== '')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const readOnly = userId === null
  const shownCallingName = callingNameEdited ? form.callingName : defaultCallingName(form.fullName)
  const phoneCheck = checkPhone(form.phone)

  function change(patch: Partial<ProfileEditInput>) {
    setForm((prev) => ({ ...prev, ...patch }))
    setSaved(false)
  }

  function setSharing(key: ShareableKey, value: Sharing) {
    setForm((prev) => ({ ...prev, sharing: { ...prev.sharing, [key]: value } }))
    setSaved(false)
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    if (busy || userId === null) return
    setBusy(true)
    setProblem(null)
    const outcome = await saveProfileEdit(await loadStore(), userId, profile, { ...form, callingName: shownCallingName })
    setBusy(false)
    if (outcome.kind === 'failed') {
      setProblem(outcome.message)
      return
    }
    setSaved(true)
    onSaved(outcome.profile)
  }

  return (
    <section className="profile-page" aria-label="Profile">
      <header className="profile-head">
        <h1>Profile</h1>
        <p className="profile-muted">What the agent knows about you, and what it may say on a call. Changes apply to your next call.</p>
      </header>
      {accountCard}
      {readOnly && <p className="profile-note">Demo mode: this is sample data, and nothing can be saved.</p>}
      {notOnboarded && !readOnly && (
        <p className="profile-note">You haven't set up your profile yet. Fill in the first four fields and save to place a call.</p>
      )}
      <form onSubmit={(e) => void save(e)} className="profile-form">
        <div className="profile-row">
          <label htmlFor="profile-full-name">Full name</label>
          <input id="profile-full-name" type="text" value={form.fullName} onChange={(e) => change({ fullName: e.target.value })} autoComplete="name" required disabled={readOnly} />
          <SharingChoice label={SHARE_LABELS.full_name} value={form.sharing.full_name} onChange={(v) => setSharing('full_name', v)} />
        </div>
        <div className="profile-row">
          <label htmlFor="profile-calling-name">Name the agent uses for you</label>
          <input
            id="profile-calling-name"
            type="text"
            value={shownCallingName}
            onChange={(e) => {
              setCallingNameEdited(true)
              change({ callingName: e.target.value })
            }}
            autoComplete="given-name"
            required
            disabled={readOnly}
            aria-describedby="profile-calling-hint"
          />
          <small id="profile-calling-hint">Always said on calls: "calling on behalf of {shownCallingName || '…'}". It can't be set to Ask me first.</small>
        </div>
        <div className="profile-row">
          <label htmlFor="profile-phone">Phone</label>
          <input id="profile-phone" type="tel" value={form.phone} onChange={(e) => change({ phone: e.target.value })} autoComplete="tel" inputMode="tel" required disabled={readOnly} />
          {phoneCheck.ok && <small>{formatUsPhone(phoneCheck.e164)}</small>}
          <SharingChoice label={SHARE_LABELS.phone} value={form.sharing.phone} onChange={(v) => setSharing('phone', v)} />
        </div>
        <div className="profile-row">
          <label htmlFor="profile-email">Email</label>
          <input id="profile-email" type="email" value={form.email} onChange={(e) => change({ email: e.target.value })} autoComplete="email" inputMode="email" required disabled={readOnly} />
          <SharingChoice label={SHARE_LABELS.email} value={form.sharing.email} onChange={(v) => setSharing('email', v)} />
        </div>
        <div className="profile-row">
          <label htmlFor="profile-dob">Date of birth (optional)</label>
          <input id="profile-dob" type="date" value={form.dateOfBirth} onChange={(e) => change({ dateOfBirth: e.target.value })} autoComplete="bday" disabled={readOnly} />
          <SharingChoice label={SHARE_LABELS.date_of_birth} value={form.sharing.date_of_birth} onChange={(v) => setSharing('date_of_birth', v)} />
        </div>
        <div className="profile-row">
          <label htmlFor="profile-address">Home address (optional)</label>
          <input id="profile-address" type="text" value={form.address} onChange={(e) => change({ address: e.target.value })} autoComplete="street-address" disabled={readOnly} />
          <SharingChoice label={SHARE_LABELS.address} value={form.sharing.address} onChange={(v) => setSharing('address', v)} />
        </div>
        <p className="profile-muted profile-hint">"Ask me first" means the agent always pauses the call and pings you before saying that detail, however sure it is.</p>
        {problem && <p className="profile-warn" role="alert">{problem}</p>}
        {saved && <p className="profile-ok" role="status">Saved. It applies to your next call.</p>}
        <div className="profile-actions">
          <button type="submit" className="profile-btn" disabled={busy || readOnly}>
            {busy ? 'Saving…' : 'Save changes'}
          </button>
        </div>
      </form>
    </section>
  )
}
