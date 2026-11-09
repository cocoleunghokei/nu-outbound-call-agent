import { CallHistory } from './history/CallHistory'
import { fakeHistory } from './history/fakeHistory'

function App() {
  // Temporary: shows the call history with fake data until the app layout and
  // the backend history endpoint exist.
  return (
    <main style={{ padding: 16 }}>
      <CallHistory items={fakeHistory} onOpen={(id) => console.info('open call', id)} />
    </main>
  )
}

export default App
