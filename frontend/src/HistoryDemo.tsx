import { CallHistory } from './history/CallHistory'
import { fakeHistory } from './history/fakeHistory'
import { useOpenCall } from './postcall/openCallUrl'
import { CallResultView } from './result/CallResultView'
import { fakeResults } from './result/fakeResults'

/**
 * Temporary: shows the call history and result page with fake data until the
 * app layout and the backend history endpoints exist.
 * ?open=<call id> opens that call's result page, and the browser's Back button
 * closes it (#49).
 */
export function HistoryDemo() {
  const [openCallId, setOpenCallId] = useOpenCall()
  // Own keys only: an id from the address like "constructor" must not hit Object.prototype.
  // (Object.hasOwn needs lib ES2022; tsconfig targets ES2020.)
  const result =
    openCallId !== null && Object.prototype.hasOwnProperty.call(fakeResults, openCallId)
      ? fakeResults[openCallId]
      : undefined

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
