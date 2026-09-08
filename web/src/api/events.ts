import type { RunEvent } from './types'

export const runEventTypes = [
  'run.started',
  'model.delta',
  'tool.started',
  'tool.completed',
  'tool.failed',
  'run.completed',
  'run.failed',
  'run.cancelled',
] as const

const terminalEvents = new Set(['run.completed', 'run.failed', 'run.cancelled'])

export function parseRunEvent(data: string): RunEvent {
  return JSON.parse(data) as RunEvent
}

export function subscribeToRun(
  runId: string,
  onEvent: (event: RunEvent) => void,
  onError: () => void,
): () => void {
  const source = new EventSource(`/api/v1/runs/${runId}/events`)
  for (const eventType of runEventTypes) {
    source.addEventListener(eventType, (rawEvent) => {
      const event = parseRunEvent((rawEvent as MessageEvent<string>).data)
      onEvent(event)
      if (terminalEvents.has(event.event_type)) source.close()
    })
  }
  source.onerror = () => onError()
  return () => source.close()
}
