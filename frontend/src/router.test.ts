import { afterEach, describe, expect, it, vi } from 'vitest'
import { livePath, navigate, routeOf } from './router'

const CALL_ID = '00000000-0000-4000-8000-0000000000aa'

describe('routeOf', () => {
  it('maps the chat home, history and unknown paths', () => {
    expect(routeOf('/', '')).toEqual({ name: 'home' })
    expect(routeOf('/history', '')).toEqual({ name: 'history' })
    expect(routeOf('/history/', '')).toEqual({ name: 'history' })
    expect(routeOf('/no-such-page', '?x=1')).toEqual({ name: 'home' })
  })

  it('reads the call id of the live screen, and treats a missing one as the demo call', () => {
    expect(routeOf('/live', `?call=${CALL_ID}`)).toEqual({ name: 'live', callId: CALL_ID })
    expect(routeOf('/live', '?call=%20')).toEqual({ name: 'live', callId: null })
    expect(routeOf('/live', '')).toEqual({ name: 'live', callId: null })
  })

  it('ignores the old ?screen= switcher', () => {
    expect(routeOf('/', '?screen=history')).toEqual({ name: 'home' })
  })
})

describe('livePath', () => {
  it("builds the address that routeOf reads back, from POST /calls' call_id", () => {
    expect(livePath(CALL_ID)).toBe(`/live?call=${CALL_ID}`)
    const [path, query] = livePath(CALL_ID).split('?')
    expect(routeOf(path, `?${query}`)).toEqual({ name: 'live', callId: CALL_ID })
  })

  it('opens the demo call when there is no call id (demo mode)', () => {
    expect(livePath(null)).toBe('/live')
  })
})

describe('navigate', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  function fakeWindow(address: string) {
    const location = { pathname: address, search: '' }
    const set = (_state: unknown, _title: string, to: string) => {
      const at = to.indexOf('?')
      location.pathname = at < 0 ? to : to.slice(0, at)
      location.search = at < 0 ? '' : to.slice(at)
    }
    const win = {
      location,
      history: { pushState: vi.fn(set), replaceState: vi.fn(set) },
      dispatchEvent: vi.fn(),
    }
    vi.stubGlobal('window', win)
    return win
  }

  it('pushes a new history entry, so Back returns to the chat', () => {
    const win = fakeWindow('/')
    navigate(livePath(CALL_ID))
    expect(win.history.pushState).toHaveBeenCalledWith(null, '', `/live?call=${CALL_ID}`)
    expect(win.history.replaceState).not.toHaveBeenCalled()
    expect(win.dispatchEvent).toHaveBeenCalledTimes(1)
  })

  it('can replace the current entry instead', () => {
    const win = fakeWindow('/')
    navigate('/history', { replace: true })
    expect(win.history.replaceState).toHaveBeenCalledWith(null, '', '/history')
    expect(win.history.pushState).not.toHaveBeenCalled()
  })

  it('does nothing when already there', () => {
    const win = fakeWindow('/history')
    navigate('/history')
    expect(win.history.pushState).not.toHaveBeenCalled()
    expect(win.dispatchEvent).not.toHaveBeenCalled()
  })
})
