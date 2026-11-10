import { ChatHome } from './precall/ChatHome'
import { fakeProfile, fakeTemplates } from './precall/fakeBriefs'

// Temporary: shows the pre-call chat home with fake data until the app layout
// and routing exist.
function App() {
  return <ChatHome context={{ profile: fakeProfile, templates: fakeTemplates }} />
}

export default App
