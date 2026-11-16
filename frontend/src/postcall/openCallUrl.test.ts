import { describe, expect, it, vi } from 'vitest'
import { createOpenCallNavigator, openCallFrom, openedHere, withOpenCall, type BrowserHistory } from './openCallUrl'

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

/** A fake browser history: entries are hrefs, `back` pops one and reports the new search. */
function fakeBrowser(startHref: string) {
  const entries: { state: unknown; href: string }[] = [{ state: null, href: startHref }]
  let onPop: (search: string) => void = () => {}
  const history: BrowserHistory = {
    get state() {
      return entries[entries.length - 1].state
    },
    back: vi.fn(() => {
      if (entries.length > 1) entries.pop()
      onPop(new URL(entries[entries.length - 1].href).search)
    }),
    pushState: (state, _unused, url) => {
      entries.push({ state, href: new URL(url, startHref).href })
    },
    replaceState: (state, _unused, url) => {
      entries[entries.length - 1] = { state, href: new URL(url, startHref).href }
    },
  }
  const href = () => entries[entries.length - 1].href
  return { history, href, entries, setOnPop: (fn: (search: string) => void) => (onPop = fn) }
}

describe('createOpenCallNavigator', () => {
  const start = 'http://localhost:5173/?screen=history'

  it('opens a call with a history entry and closes it by going Back', () => {
    const browser = fakeBrowser(start)
    const changes: (string | null)[] = []
    const nav = createOpenCallNavigator(browser.history, browser.href, (id) => changes.push(id))
    browser.setOnPop((search) => nav.popped(search))

    nav.open('abc')
    expect(browser.href()).toBe('http://localhost:5173/?screen=history&open=abc')
    nav.close()
    expect(browser.history.back).toHaveBeenCalledTimes(1)
    expect(browser.href()).toBe(start)
    expect(changes).toEqual(['abc', null, null])
  })

  it('goes Back only once when "All calls" is clicked twice before Back finishes', () => {
    const browser = fakeBrowser(start)
    const nav = createOpenCallNavigator(browser.history, browser.href, () => {})
    nav.open('abc')
    // Back is asynchronous in a browser: popstate has not fired yet between the clicks.
    nav.close()
    nav.close()
    expect(browser.history.back).toHaveBeenCalledTimes(1)
    expect(browser.entries).toHaveLength(1)
  })

  it('closes a call opened from a link without leaving the page', () => {
    const browser = fakeBrowser(`${start}&open=abc`)
    const changes: (string | null)[] = []
    const nav = createOpenCallNavigator(browser.history, browser.href, (id) => changes.push(id))
    nav.close()
    expect(browser.history.back).not.toHaveBeenCalled()
    expect(browser.href()).toBe(start)
    expect(changes).toEqual([null])
  })

  it('can close again after a Back has finished', () => {
    const browser = fakeBrowser(start)
    const nav = createOpenCallNavigator(browser.history, browser.href, () => {})
    browser.setOnPop((search) => nav.popped(search))
    nav.open('a')
    nav.close()
    nav.open('b')
    nav.close()
    expect(browser.history.back).toHaveBeenCalledTimes(2)
  })
})
