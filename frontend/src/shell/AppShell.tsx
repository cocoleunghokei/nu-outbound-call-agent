import type { ReactNode } from 'react'
import { navigate, type Route } from '../router'
import { activeNav, isPlainClick, NAV_ITEMS, type NavItem } from './nav'
import './AppShell.css'

/** Icon paths from the design prototype (public/prototype/index.html), 24×24, stroked. */
const ICONS: Record<NavItem['key'], ReactNode> = {
  chat: <path d="M21 12a8.5 8.5 0 0 1-12.6 7.4L3 21l1.6-5.4A8.5 8.5 0 1 1 21 12z" />,
  calls: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
}

/**
 * The layout around every screen: the sidebar from the design prototype on the left
 * (a bar along the bottom on a phone), and the current screen beside it.
 */
export function AppShell({ route, children }: { route: Route; children: ReactNode }) {
  const active = activeNav(route)
  return (
    <div className="app-shell">
      <nav className="app-rail" aria-label="Main">
        <img className="app-logo" src="/app-icon.svg" alt="" width={46} height={46} />
        {NAV_ITEMS.map((item) => (
          <a
            key={item.key}
            href={item.path}
            className={item.key === active ? 'app-rail-btn on' : 'app-rail-btn'}
            aria-current={item.key === active ? 'page' : undefined}
            onClick={(event) => {
              if (!isPlainClick(event)) return
              event.preventDefault()
              navigate(item.path)
            }}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              {ICONS[item.key]}
            </svg>
            <span className="app-rail-label">{item.label}</span>
          </a>
        ))}
      </nav>
      <div className="app-main">{children}</div>
    </div>
  )
}
