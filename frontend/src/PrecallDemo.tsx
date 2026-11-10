import { ChatHome } from './precall/ChatHome'
import { fakeProfile, fakeTemplates } from './precall/fakeBriefs'

// Temporary: shows the pre-call chat home with fake data until the app layout
// and routing exist.
export function PrecallDemo() {
  return (
    <ChatHome
      context={{ profile: fakeProfile, templates: fakeTemplates }}
      // Coco's POST /calls takes the brief from here once the backend exists.
      onStart={(brief) => console.info('start call with brief', brief)}
    />
  )
}
