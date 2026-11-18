import { useEffect, useRef, useState, type FormEvent } from 'react'
import { CODE_LENGTH, requestCode, verifyCode, type CodeSent, type EmailAuth } from '../lib/emailSignIn'
import type { Account } from '../lib/session'

/** The shared client's auth, loaded lazily so demo mode never touches Supabase. */
const loadSupabaseAuth = async (): Promise<EmailAuth> => (await import('../lib/supabase')).supabase.auth

interface EmailStepProps {
  /** The current session's user (anonymous when this step opens from Start). */
  account: Account
  /** Closes the step. After a verified code the screen has already re-picked its mode. */
  onClose: () => void
  /** Injected in tests. */
  loadAuth?: () => Promise<EmailAuth>
}

type Phase = { step: 'email' } | { step: 'code'; sent: CodeSent } | { step: 'done'; email: string }

/**
 * The email step (ON-1): shown over the chat when Start needs a permanent user. Email in,
 * 6-digit code back, code in. All behaviour is in `lib/emailSignIn.ts`; this component
 * only renders its results. It is a panel, not a route, so the draft brief stays put.
 */
export function EmailStep({ account, onClose, loadAuth = loadSupabaseAuth }: EmailStepProps) {
  const [phase, setPhase] = useState<Phase>({ step: 'email' })
  const [email, setEmail] = useState(account.email ?? '')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const emailInput = useRef<HTMLInputElement>(null)
  const codeInput = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (phase.step === 'email') emailInput.current?.focus()
    if (phase.step === 'code') codeInput.current?.focus()
  }, [phase.step])

  async function sendCode(event?: FormEvent) {
    event?.preventDefault()
    if (busy) return
    setBusy(true)
    setProblem(null)
    const result = await requestCode(await loadAuth(), email, account)
    setBusy(false)
    if (result.kind === 'failed') {
      setProblem(result.message)
      return
    }
    setCode('')
    setPhase({ step: 'code', sent: result })
  }

  async function verify(event: FormEvent) {
    event.preventDefault()
    if (busy || phase.step !== 'code') return
    setBusy(true)
    setProblem(null)
    const result = await verifyCode(await loadAuth(), phase.sent, code)
    setBusy(false)
    if (result.kind === 'failed') {
      setProblem(result.message)
      return
    }
    setPhase({ step: 'done', email: phase.sent.email })
  }

  return (
    <div className="precall-overlay" onKeyDown={(e) => e.key === 'Escape' && onClose()}>
      <section className="precall-dialog" role="dialog" aria-modal="true" aria-labelledby="precall-email-title">
        {phase.step === 'email' && (
          <form onSubmit={(e) => void sendCode(e)}>
            <h2 id="precall-email-title">Add your email to place this call</h2>
            <p className="precall-small">
              I'll send a 6-digit code. Your profile and calls then follow your email, so you can pick up on another
              device too.
            </p>
            <label className="precall-field">
              <span>Email</span>
              <input
                ref={emailInput}
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
              <button type="button" className="precall-btn" onClick={onClose}>
                Not now
              </button>
              <button type="submit" className="precall-btn primary" disabled={busy || email.trim() === ''}>
                {busy ? 'Sending…' : 'Send code'}
              </button>
            </div>
          </form>
        )}

        {phase.step === 'code' && (
          <form onSubmit={(e) => void verify(e)}>
            <h2 id="precall-email-title">Enter the code I sent to {phase.sent.email}</h2>
            {phase.sent.switchesUser && (
              <p className="precall-small">
                This email already has an account. Verifying switches this browser to it; what you set up here as a guest
                won't follow.
              </p>
            )}
            <label className="precall-field">
              <span>6-digit code</span>
              <input
                ref={codeInput}
                type="text"
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, CODE_LENGTH))}
                autoComplete="one-time-code"
                inputMode="numeric"
                pattern="[0-9]*"
                placeholder="123456"
                required
              />
            </label>
            {problem && <p className="precall-warn">{problem}</p>}
            <div className="precall-foot precall-actions">
              <button type="button" className="precall-btn" onClick={() => void sendCode()} disabled={busy}>
                Send a new code
              </button>
              <button type="submit" className="precall-btn primary" disabled={busy || code.length !== CODE_LENGTH}>
                {busy ? 'Checking…' : 'Verify'}
              </button>
            </div>
            <button type="button" className="precall-edit precall-link" onClick={() => setPhase({ step: 'email' })}>
              Use a different email
            </button>
          </form>
        )}

        {phase.step === 'done' && (
          <div>
            <h2 id="precall-email-title">You're in</h2>
            <p className="precall-done">Signed in as {phase.email}.</p>
            <p className="precall-small">Tap Start on your plan to place the call.</p>
            <div className="precall-foot">
              <button type="button" className="precall-btn primary" onClick={onClose}>
                Back to the plan
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
