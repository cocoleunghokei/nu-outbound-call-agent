import { useState } from 'react'
import { CallHistory } from './history/CallHistory'
import { fakeHistory } from './history/fakeHistory'
import { historySource, NOT_FOUND_MESSAGE } from './postcall/api'
import { useOpenCall } from './postcall/openCallUrl'
import { useRealHistory } from './postcall/useRealHistory'
import { CallResultView } from './result/CallResultView'
import { fakeResults } from './result/fakeResults'

interface ScreenProps {
  openCallId: string | null
  /** A call id opens that call; null closes it (Back, when this screen opened it). */
  setOpenCallId: (callId: string | null) => void
}

/** The fake calls (the default until the backend is deployed). */
function FakeHistory({ openCallId, setOpenCallId }: ScreenProps) {
  // Own keys only: an id from the address like "constructor" must not hit Object.prototype.
  // (Object.hasOwn needs lib ES2022; tsconfig targets ES2020.)
  const result =
    openCallId !== null && Object.prototype.hasOwnProperty.call(fakeResults, openCallId)
      ? fakeResults[openCallId]
      : undefined
  return result ? (
    <CallResultView result={result} onBack={() => setOpenCallId(null)} />
  ) : (
    <CallHistory items={fakeHistory} onOpen={setOpenCallId} />
  )
}

/** The signed-in user's real calls, from GET /calls and GET /calls/{id}/result. */
function RealHistory({ apiBase, openCallId, setOpenCallId }: ScreenProps & { apiBase: string }) {
  const { calls, result, retry } = useRealHistory(apiBase, openCallId)
  const tryAgain = (
    <button type="button" onClick={retry}>
      Try again
    </button>
  )
  if (openCallId !== null) {
    if (result === null) return <p>Loading the call…</p>
    if (!result.ok) {
      return (
        <div>
          <p role="alert">{result.message}</p>
          {result.message === NOT_FOUND_MESSAGE ? null : tryAgain}{' '}
          <button type="button" onClick={() => setOpenCallId(null)}>
            ← All calls
          </button>
        </div>
      )
    }
    return <CallResultView result={result.data} onBack={() => setOpenCallId(null)} />
  }
  if (calls === null) return <p>Loading your calls…</p>
  if (!calls.ok) {
    return (
      <div>
        <p role="alert">{calls.message}</p>
        {tryAgain}
      </div>
    )
  }
  return <CallHistory items={calls.data} onOpen={setOpenCallId} />
}

/**
 * Temporary: the call history and result page until the app layout exists.
 * Real data with VITE_HISTORY_SOURCE=real and VITE_API_URL, else fake data.
 * ?open=<call id> opens that call's result page, and the browser's Back button
 * closes it (#49).
 */
export function HistoryDemo() {
  const [source] = useState(() =>
    historySource(import.meta.env.VITE_HISTORY_SOURCE, import.meta.env.VITE_API_URL),
  )
  const [openCallId, setOpenCallId] = useOpenCall()

  return (
    <main style={{ padding: 16 }}>
      {source.kind === 'real' ? (
        <RealHistory apiBase={source.apiBase} openCallId={openCallId} setOpenCallId={setOpenCallId} />
      ) : (
        <FakeHistory openCallId={openCallId} setOpenCallId={setOpenCallId} />
      )}
    </main>
  )
}
