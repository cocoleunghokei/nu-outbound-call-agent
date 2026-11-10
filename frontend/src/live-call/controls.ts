/**
 * Pause / Resume / End (DC-11) and the labels for the live feed (DC-4, DC-5).
 */
import type { CallStatus } from './types'

/** The commands this screen sends. `instruction` (DC-10) belongs to the pre-call owner's box. */
export type ControlCommand = 'pause' | 'resume' | 'end'

/** The 2 min pause cap (DC-11, to be tuned from test calls). Only used for the on-screen hint. */
export const PAUSE_CAP_MS = 120_000

/**
 * What happened to a command. "Accepted" means the backend took it, not that
 * the call already changed: the status update arrives as an event.
 */
export type CommandResult = { kind: 'accepted' } | { kind: 'error'; message: string }

/** Sends a command for the call. The mock call and, later, the backend API implement this. */
export type SendCallCommand = (callId: string, command: ControlCommand) => Promise<CommandResult>

/** Which controls the call allows right now. */
export function availableControls(
  status: CallStatus,
  ended: boolean,
): { pause: boolean; resume: boolean; end: boolean } {
  if (ended || status === 'ended' || status === 'failed') {
    return { pause: false, resume: false, end: false }
  }
  const connected = status === 'talking' || status === 'waiting_on_you' || status === 'in_phone_menu'
  return {
    pause: connected,
    resume: status === 'on_hold',
    // You can hang up at any point before the call is over, even while it rings.
    end: true,
  }
}

const DECISION_LABELS: Record<string, string> = {
  accept: 'Accepted',
  counter: 'Countered',
  decline: 'Declined',
  share_field: 'Shared',
  take_fallback: 'Used your fallback',
  end_call: 'Ended',
}

/** Badge text for a decision. An action this client doesn't know shows as "Decided". */
export function decisionLabel(action: string): string {
  return DECISION_LABELS[action] ?? 'Decided'
}

const COMMAND_TEXT: Record<string, string> = {
  pause: 'You paused the call. The agent asked them to hold.',
  resume: 'You resumed the call.',
  end: 'You ended the call. The agent said goodbye.',
}

/** Feed text for a command you sent. */
export function commandText(command: string): string {
  return COMMAND_TEXT[command] ?? 'You sent a command.'
}
