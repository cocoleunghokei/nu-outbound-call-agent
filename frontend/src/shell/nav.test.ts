import { describe, expect, it } from 'vitest'
import { routeOf } from '../router'
import { activeNav, isPlainClick, NAV_ITEMS } from './nav'

const CALL_ID = '00000000-0000-4000-8000-0000000000aa'
const click = { button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false }

describe('NAV_ITEMS', () => {
  it('opens only screens the router knows, each on its own address', () => {
    expect(NAV_ITEMS.map((item) => item.key)).toEqual(['chat', 'calls', 'profile'])
    expect(routeOf(NAV_ITEMS[0].path, '')).toEqual({ name: 'home' })
    expect(routeOf(NAV_ITEMS[1].path, '')).toEqual({ name: 'history' })
    expect(routeOf(NAV_ITEMS[2].path, '')).toEqual({ name: 'profile' })
  })
})

describe('activeNav', () => {
  it('highlights Chat on the chat home, Profile on the profile and Calls on history and a live call', () => {
    expect(activeNav({ name: 'home' })).toBe('chat')
    expect(activeNav({ name: 'profile' })).toBe('profile')
    expect(activeNav({ name: 'history' })).toBe('calls')
    expect(activeNav({ name: 'live', callId: CALL_ID })).toBe('calls')
    expect(activeNav({ name: 'live', callId: null })).toBe('calls')
  })
})

describe('isPlainClick', () => {
  it('handles a plain left click in the page', () => {
    expect(isPlainClick(click)).toBe(true)
  })

  it('leaves new-tab clicks to the browser', () => {
    expect(isPlainClick({ ...click, button: 1 })).toBe(false)
    expect(isPlainClick({ ...click, metaKey: true })).toBe(false)
    expect(isPlainClick({ ...click, ctrlKey: true })).toBe(false)
    expect(isPlainClick({ ...click, shiftKey: true })).toBe(false)
    expect(isPlainClick({ ...click, altKey: true })).toBe(false)
  })
})
