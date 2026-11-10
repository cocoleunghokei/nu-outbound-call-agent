import { ChatHome } from './precall/ChatHome'
import { fakeProfile, fakeTemplates } from './precall/fakeBriefs'

// Temporary: shows the pre-call chat home with fake data until the app layout
// and routing exist.
const callingName = fakeProfile.find((f) => f.key === 'calling_name')?.value ?? 'there'

function App() {
  return <ChatHome context={{ callingName, templates: fakeTemplates }} />
}

export default App
