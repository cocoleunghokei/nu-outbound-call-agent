import { describe, expect, it } from 'vitest'
import { SKIP_LABEL, answerItems, answerLabel, parseClarifications, type Clarification } from './clarify'

const AT_7PM: Clarification = {
  item: 'at 7pm',
  list: 'constraints',
  question: 'You wrote "at 7pm". Is 7pm the latest you would accept, or the only time that works?',
  answers: [
    { label: '7pm or earlier', rules: [{ kind: 'time', latest: '19:00' }], text: null },
    { label: 'Only 7pm', rules: [{ kind: 'time', earliest: '19:00', latest: '19:00' }], text: null },
  ],
}

const BEFORE_3: Clarification = {
  item: 'before 3',
  list: 'fallbacks',
  question: 'You wrote "before 3". Did you mean before 3 in the morning or in the evening?',
  answers: [
    { label: 'Morning (am)', rules: null, text: 'before 3am' },
    { label: 'Afternoon or evening (pm)', rules: null, text: 'before 3pm' },
  ],
}

describe('parseClarifications', () => {
  it('keeps well-formed questions, both kinds of answer', () => {
    expect(parseClarifications([AT_7PM, BEFORE_3])).toEqual([AT_7PM, BEFORE_3])
  })

  it('is empty for anything that is not a list', () => {
    for (const raw of [undefined, null, 'oops', 5, {}, { item: 'x' }]) expect(parseClarifications(raw)).toEqual([])
  })

  it('drops a question that is malformed, never-agree, or has no usable answer', () => {
    const bad = [
      null,
      { ...AT_7PM, item: '' },
      { ...AT_7PM, list: 'never_agree' },
      { ...AT_7PM, question: '' },
      { ...AT_7PM, answers: 'oops' },
      { ...AT_7PM, answers: [] },
      { ...AT_7PM, answers: [{ label: '', rules: [], text: null }] },
    ]
    expect(parseClarifications([...bad, BEFORE_3])).toEqual([BEFORE_3])
  })

  it('drops only the bad answers of a question', () => {
    const mixed = {
      ...AT_7PM,
      answers: [
        { label: 'Both', rules: [{ kind: 'time' }], text: 'also text' }, // rules and text together
        { label: 'Unknown rule', rules: [{ kind: 'weather' }], text: null },
        { label: 'Too many', rules: Array(5).fill({ kind: 'time' }), text: null },
        { label: 'Neither', rules: null, text: null },
        AT_7PM.answers[0],
      ],
    }
    expect(parseClarifications([mixed])).toEqual([{ ...AT_7PM, answers: [AT_7PM.answers[0]] }])
  })

  it('treats a missing text or rules as null', () => {
    const lean = { ...AT_7PM, answers: [{ label: 'Only 7pm', rules: [{ kind: 'time', latest: '19:00' }] }] }
    expect(parseClarifications([lean])[0].answers[0]).toMatchObject({ text: null })
    const rewrite = { ...BEFORE_3, answers: [{ label: 'Morning', text: 'before 3am' }] }
    expect(parseClarifications([rewrite])[0].answers[0]).toEqual({ label: 'Morning', rules: null, text: 'before 3am' })
  })
})

describe('answerItems', () => {
  const items = [{ text: 'by 10am', rules: [{ kind: 'time', latest: '10:00' }] as never }, { text: 'at 7pm' }]

  it('settles the item with the answer rules, leaving the others alone', () => {
    const next = answerItems(items, AT_7PM, 1)
    expect(next).toEqual([items[0], { text: 'at 7pm', rules: [{ kind: 'time', earliest: '19:00', latest: '19:00' }] }])
    expect(items[1]).toEqual({ text: 'at 7pm' }) // not changed in place
  })

  it('rewrites the text and drops old rules, so the server reads the new words', () => {
    const old = [{ text: 'before 3', rules: [] as never }]
    expect(answerItems(old, BEFORE_3, 1)).toEqual([{ text: 'before 3pm' }])
  })

  it('"not sure" leaves the words alone and marks them decided with no rules', () => {
    expect(answerItems(items, AT_7PM, 'skip')).toEqual([items[0], { text: 'at 7pm', rules: [] }])
  })

  it('returns null when the item is gone or the choice is not an answer', () => {
    expect(answerItems([{ text: 'something else' }], AT_7PM, 0)).toBeNull()
    expect(answerItems(items, AT_7PM, 5)).toBeNull()
    expect(answerItems(items, AT_7PM, -1)).toBeNull()
  })
})

describe('answerLabel', () => {
  it('is what the user tapped', () => {
    expect(answerLabel(AT_7PM, 0)).toBe('7pm or earlier')
    expect(answerLabel(AT_7PM, 'skip')).toBe(SKIP_LABEL)
    expect(answerLabel(AT_7PM, 9)).toBe(SKIP_LABEL)
  })
})
