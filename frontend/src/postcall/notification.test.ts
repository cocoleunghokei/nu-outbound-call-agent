import { describe, expect, it, vi } from 'vitest'
import { callEndedNotice, notifyCallEnded, resultPageUrl, type NotifyEnv } from './notification'

describe('callEndedNotice', () => {
  it('uses the business name as the title and the outcome in the body', () => {
    expect(callEndedNotice('c1', 'Juniper Ramen', 'booked')).toEqual({
      callId: 'c1',
      title: 'Juniper Ramen',
      body: 'Booked. Tap to see the summary.',
    })
  })

  it('names the failure reason', () => {
    expect(callEndedNotice('c1', 'Back Bay Dental', 'refused_ai').body).toBe(
      'Failed: Refused AI. Tap to see the summary.',
    )
  })

  it('says the summary is coming when there is no outcome yet', () => {
    expect(callEndedNotice('c1', 'Tatte', null).body).toBe('The call ended. The summary will be ready soon.')
  })

  it('shows "Unknown" for an outcome this version does not know', () => {
    expect(callEndedNotice('c1', 'Tatte', 'voicemail_left').body).toBe('Unknown. Tap to see the summary.')
  })

  it('falls back to "Your call" when the business name is blank', () => {
    expect(callEndedNotice('c1', '  ', 'booked').title).toBe('Your call')
  })
})

describe('resultPageUrl', () => {
  it('points at the history screen with the call open', () => {
    expect(resultPageUrl('abc-123')).toBe('/?screen=history&open=abc-123')
  })

  it('escapes the call id', () => {
    expect(resultPageUrl('a&b')).toBe('/?screen=history&open=a%26b')
  })
})

describe('notifyCallEnded', () => {
  const notice = { callId: 'c1', title: 'Juniper Ramen', body: 'Booked. Tap to see the summary.' }

  function fakeEnv(permission: NotifyEnv['permission']) {
    let click: (() => void) | undefined
    const env = {
      permission,
      show: vi.fn((_title: string, _options: NotificationOptions, onClick: () => void) => {
        click = onClick
      }),
      openResult: vi.fn(),
    } satisfies NotifyEnv
    return { env, clickIt: () => click?.() }
  }

  it('shows the notification when permission is granted', () => {
    const { env } = fakeEnv('granted')
    expect(notifyCallEnded(notice, env)).toBe(true)
    expect(env.show).toHaveBeenCalledWith(
      'Juniper Ramen',
      { body: 'Booked. Tap to see the summary.', tag: 'call-ended-c1' },
      expect.any(Function),
    )
  })

  it('opens the result page when the notification is clicked', () => {
    const { env, clickIt } = fakeEnv('granted')
    notifyCallEnded(notice, env)
    expect(env.openResult).not.toHaveBeenCalled()
    clickIt()
    expect(env.openResult).toHaveBeenCalledWith('/?screen=history&open=c1')
  })

  it.each(['default', 'denied', 'unsupported'] as const)('shows nothing when permission is %s', (permission) => {
    const { env } = fakeEnv(permission)
    expect(notifyCallEnded(notice, env)).toBe(false)
    expect(env.show).not.toHaveBeenCalled()
  })
})
