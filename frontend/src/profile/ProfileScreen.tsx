import { isOnboarded } from '../lib/profile'
import { fakeProfile } from '../precall/fakeBriefs'
import { useDataMode } from '../precall/useDataMode'
import { useProfile } from '../precall/useProfile'
import { AccountCard } from './AccountCard'
import { ProfilePage } from './ProfilePage'

/**
 * The profile screen (ON-7). In API mode it loads the signed-in user's stored profile and
 * lets them edit it. In demo mode there is nothing to save, so it shows the fake profile
 * with Save turned off.
 */
export function ProfileScreen() {
  const { mode } = useDataMode(import.meta.env.VITE_API_URL)
  const { profile, setProfile } = useProfile(mode)

  if (mode?.kind === 'demo') {
    return (
      <ProfilePage
        // Keyed so the form starts from the profile it is shown (the form copies it once).
        key="demo"
        profile={{ onboardedAt: null, fields: fakeProfile }}
        userId={null}
        onSaved={setProfile}
      />
    )
  }
  if (!mode || !profile) {
    return (
      <section className="profile-page" aria-label="Profile" aria-busy="true">
        <p className="profile-muted">Loading…</p>
      </section>
    )
  }
  return (
    <ProfilePage
      key={profile.onboardedAt ?? 'new'}
      profile={profile}
      userId={mode.account.userId}
      onSaved={setProfile}
      notOnboarded={!isOnboarded(profile)}
      accountCard={<AccountCard account={mode.account} />}
    />
  )
}
