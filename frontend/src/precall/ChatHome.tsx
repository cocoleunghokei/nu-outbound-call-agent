import { Fragment, useEffect, useReducer, useRef, useState, type FormEvent } from 'react'
import './ChatHome.css'
import type { Brief } from './types'
import { ReviewCard } from './ReviewCard'
import { chatReducer, completedDraft, initialChatState, reviewOf, type ChatContext } from './chat'

interface ChatHomeProps {
  context: ChatContext
  /** Called once with the reviewed brief when the user taps Start (PC-6). */
  onStart: (brief: Brief) => void
}

/**
 * The chat home (PC-1): the screen an onboarded user lands on, and where every
 * call starts. All behaviour lives in `chat.ts`; this component only renders
 * the state and sends user actions. Visual design follows Harper's prototype
 * (`public/prototype/`).
 */
export function ChatHome({ context, onStart }: ChatHomeProps) {
  const [reducer] = useState(() => chatReducer(context))
  const [state, dispatch] = useReducer(reducer, context, initialChatState)
  const [text, setText] = useState('')
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

  const draft = completedDraft(state)
  const review = reviewOf(state, context)

  function start() {
    if (!review || review.missing_fields.length > 0 || state.stage !== 'draft_ready') return
    onStart(review.brief)
    dispatch({ type: 'start' })
  }

  return (
    <section className="precall-chat" aria-label="Chat">
      <header className="precall-topbar">
        <h1>Chat</h1>
        <button type="button" className="precall-pill" onClick={() => dispatch({ type: 'new_call' })}>
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
                  onEdit={(target) => dispatch({ type: 'edit', target })}
                  onStart={start}
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
              onClick={() => dispatch(chip.action)}
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
