import { useState } from 'react'
import { CallHistory } from './history/CallHistory'
import { fakeHistory } from './history/fakeHistory'
import { CallResultView } from './result/CallResultView'
import { fakeResults } from './result/fakeResults'

export function HistoryDemo() {
  // Temporary: shows the call history and result page with fake data until the
  // app layout and the backend history endpoints exist.
  // ?open=<call id> opens that call's result page (the PO-5 notification links here).
  const [openCallId, setOpenCallId] = useState<string | null>(() =>
    new URLSearchParams(window.location.search).get('open'),
  )
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
