import { useState } from 'react'
import { CallHistory } from './history/CallHistory'
import { fakeHistory } from './history/fakeHistory'
import { CallResultView } from './result/CallResultView'
import { fakeResults } from './result/fakeResults'

function App() {
  // Temporary: shows the call history and result page with fake data until the
  // app layout and the backend history endpoints exist.
  const [openCallId, setOpenCallId] = useState<string | null>(null)
  const result = openCallId ? fakeResults[openCallId] : undefined

  return (
    <main style={{ padding: 16 }}>
      {result ? (
        <CallResultView result={result} onBack={() => setOpenCallId(null)} />
      ) : (
        <CallHistory items={fakeHistory} onOpen={setOpenCallId} />
      )}
    </main>
  )
}

export default App
