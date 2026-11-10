import { describe, expect, it } from 'vitest'
import { availableControls, commandText, decisionLabel } from './controls'

describe('availableControls', () => {
  it('allows Pause while connected, not while dialing', () => {
    expect(availableControls('dialing', false).pause).toBe(false)
    expect(availableControls('creating', false).pause).toBe(false)
    expect(availableControls('talking', false).pause).toBe(true)
    expect(availableControls('waiting_on_you', false).pause).toBe(true)
  })

  it('offers Resume only while on hold', () => {
    expect(availableControls('on_hold', false)).toEqual({ pause: false, resume: true, end: true })
    expect(availableControls('talking', false).resume).toBe(false)
  })

  it('allows End at any point before the call is over', () => {
    expect(availableControls('dialing', false).end).toBe(true)
    expect(availableControls('transferring', false).end).toBe(true)
  })

  it('allows nothing once the call is over', () => {
    const none = { pause: false, resume: false, end: false }
    expect(availableControls('talking', true)).toEqual(none)
    expect(availableControls('ended', false)).toEqual(none)
    expect(availableControls('failed', false)).toEqual(none)
  })
})

describe('decisionLabel', () => {
  it('labels each known action', () => {
    expect(decisionLabel('accept')).toBe('Accepted')
    expect(decisionLabel('take_fallback')).toBe('Used your fallback')
    expect(decisionLabel('share_field')).toBe('Shared')
  })

  it('shows an action it does not know as Decided', () => {
    expect(decisionLabel('negotiate')).toBe('Decided')
  })
})

describe('commandText', () => {
  it('describes each command and has a fallback', () => {
    expect(commandText('pause')).toMatch(/paused/)
    expect(commandText('resume')).toMatch(/resumed/)
    expect(commandText('end')).toMatch(/ended/)
    expect(commandText('mystery')).toBe('You sent a command.')
  })
})
