import { describe, expect, it } from 'vitest'

import { parseRunEvent } from './events'

describe('run event stream', () => {
  it('parses the normalized SSE payload', () => {
    const event = parseRunEvent(
      JSON.stringify({
        id: 'event-1',
        run_id: 'run-1',
        sequence: 7,
        event_type: 'model.delta',
        payload: { delta: '你好' },
        trace_id: 'trace-1',
        created_at: '2026-08-05T08:00:00Z',
      }),
    )

    expect(event.sequence).toBe(7)
    expect(event.payload.delta).toBe('你好')
  })
})
