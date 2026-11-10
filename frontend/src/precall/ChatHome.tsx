import { Fragment, useEffect, useReducer, useRef, useState, type FormEvent } from 'react'
import './ChatHome.css'
import { ReviewCard } from './ReviewCard'
import { chatReducer, completedDraft, initialChatState, reviewOf, type ChatAction, type ChatContext } from './chat'
import { emptySession, reviewView, singleFlight, type SaveSession, type ServerCheck, type Starter } from './start'

interface ChatHomeProps {
  context: ChatContext
  /**
   * Runs Start for the reviewed brief (PC-6): `demoStarter` logs it, the API
   * starter saves it and hands it to `POST /calls`. See `start.ts`.
   */
  starter: Starter
  /** Small top-bar note, e.g. "Demo mode (fake data)". Hidden when null. */
  modeNote?: { text: string; title: string } | null
}

/**
 * The chat home (PC-1): the screen an onboarded user lands on, and where every
 * call starts. All behaviour lives in `chat.ts`; this component only renders
 * the state and sends user actions. Visual design follows Harper's prototype
 * (`public/prototype/`).
 */
export function ChatHome({ context, starter, modeNote = null }: ChatHomeProps) {
  const [reducer] = useState(() => chatReducer(context))
  const [state, dispatch] = useReducer(reducer, context, initialChatState)
  const [text, setText] = useState('')
  // Start in flight (no double submit), and the server's check of the saved brief.
  const [busy, setBusy] = useState(false)
  const [server, setServer] = useState<ServerCheck | null>(null)
  const [runStarter] = useState(() =>
    singleFlight((run: Starter, ...args: Parameters<Starter>) => run(...args)),
  )
  // What has been saved for this draft, and which draft it is; both reset on "new call".
  const session = useRef<SaveSession>(emptySession)
  const draftGeneration = useRef(0)
  const feedEnd = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    feedEnd.current?.scrollIntoView?.({ block: 'end' })
  }, [state.messages.length, state.stage])

  useEffect(() => {
    if (state.awaiting) input.current?.focus()
  }, [state.awaiting])

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    if (text.trim() === '') return
    dispatch({ type: 'send', text })
    setText('')
  }

  /** Dispatch a chat action; a new call also forgets what was saved for the old draft. */
  function act(action: ChatAction) {
    if (action.type === 'new_call') {
      draftGeneration.current += 1
      session.current = emptySession
      setServer(null)
    }
    dispatch(action)
  }

  const draft = completedDraft(state)
  const review = reviewOf(state, context)

  async function start() {
    if (!review || state.stage !== 'draft_ready') return
    const local = { missing_fields: review.missing_fields, opening_line: null }
    if (reviewView(review.brief, local, server).missing_fields.length > 0) return
    const generation = draftGeneration.current
    setBusy(true)
    const out = await runStarter(starter, review.brief, session.current)
    if (out === null) return // a Start is already in flight; it clears `busy` itself
    setBusy(false)
    if (generation !== draftGeneration.current) return // the user moved on to a new call
    session.current = out.session
    const result = out.result
    if (result.server) setServer(result.server)
    switch (result.kind) {
      case 'started':
        dispatch({ type: 'start' })
        break
      case 'saved_not_connected':
        dispatch({ type: 'saved_not_connected' })
        break
      case 'blocked':
      case 'error':
        dispatch({ type: 'note', text: result.message })
        break
    }
  }

  return (
    <section className="precall-chat" aria-label="Chat">
      <header className="precall-topbar">
        <div className="precall-title">
          <h1>Chat</h1>
          {modeNote && (
            <span className="precall-mode" title={modeNote.title}>
              {modeNote.text}
            </span>
          )}
        </div>
        <button type="button" className="precall-pill" onClick={() => act({ type: 'new_call' })}>
          + New call
        </button>
      </header>

      <div className="precall-feed" role="log" aria-live="polite" aria-label="Conversation">
        <div className="precall-feed-in">
          {state.messages.map((m) => (
            <Fragment key={m.id}>
              <p className={`precall-msg ${m.from}`}>{m.text}</p>
              {/* The review card sits under "Here's the plan", and stays there after Start. */}
              {m.id === state.reviewAfterId && draft && review && (
                <ReviewCard
                  review={review}
                  draft={draft}
                  profile={context.profile}
                  started={state.stage === 'started'}
                  outcome={state.startOutcome}
                  server={server}
                  busy={busy}
                  onEdit={(target) => act({ type: 'edit', target })}
                  onStart={() => void start()}
                />
              )}
            </Fragment>
          ))}
          <div ref={feedEnd} />
        </div>
      </div>

      {state.chips.length > 0 && (
        <div className="precall-chips" role="group" aria-label="Quick replies">
          {state.chips.map((chip) => (
            <button
              key={chip.label}
              type="button"
              className={`precall-chip${chip.quiet ? ' quiet' : ''}`}
              onClick={() => act(chip.action)}
            >
              {chip.label}
            </button>
          ))}
        </div>
      )}

      <form className="precall-composer-wrap" onSubmit={onSubmit}>
        <div className={`precall-composer${state.awaiting ? ' await' : ''}`}>
          <input
            ref={input}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={state.placeholder}
            aria-label={state.placeholder}
            autoComplete="off"
            inputMode={state.awaiting === 'phone' ? 'tel' : 'text'}
          />
          <button type="submit" className="precall-send" aria-label="Send" disabled={text.trim() === ''}>
            ↑
          </button>
        </div>
      </form>
    </section>
  )
}
