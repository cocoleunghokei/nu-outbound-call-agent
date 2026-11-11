import { describe, expect, it } from 'vitest'
import { openCallFrom, openedHere, withOpenCall } from './openCallUrl'

describe('openCallFrom', () => {
  it('reads the call id from ?open=', () => {
    expect(openCallFrom('?screen=history&open=abc-123')).toBe('abc-123')
  })

  it('is null when there is no open call', () => {
    expect(openCallFrom('?screen=history')).toBeNull()
    expect(openCallFrom('?open=')).toBeNull()
    expect(openCallFrom('?open=%20%20')).toBeNull()
  })
})

describe('withOpenCall', () => {
  const base = 'http://localhost:5173/?screen=history'

  it('adds the call and keeps the other params', () => {
    expect(withOpenCall(base, 'abc-123')).toBe('/?screen=history&open=abc-123')
  })

  it('replaces a call that was already open', () => {
    expect(withOpenCall(`${base}&open=old`, 'new')).toBe('/?screen=history&open=new')
  })

  it('removes the call', () => {
    expect(withOpenCall(`${base}&open=abc-123`, null)).toBe('/?screen=history')
  })

  it('escapes the call id', () => {
    expect(withOpenCall(base, 'a&b')).toBe('/?screen=history&open=a%26b')
  })
})

describe('openedHere', () => {
  it('recognises only entries this screen added', () => {
    expect(openedHere({ from: 'postcall-open-call' })).toBe(true)
    expect(openedHere(null)).toBe(false)
    expect(openedHere({ from: 'elsewhere' })).toBe(false)
    expect(openedHere('postcall-open-call')).toBe(false)
  })
})
