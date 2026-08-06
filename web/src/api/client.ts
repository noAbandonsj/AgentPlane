import type {
  Agent,
  AgentCreate,
  AgentPatch,
  AgentVersion,
  Capability,
  Message,
  Run,
  Session,
  ToolMetadata,
} from './types'

interface ErrorEnvelope {
  error?: {
    code?: string
    message?: string
    details?: Record<string, unknown>
  }
}

export class ApiClientError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message)
    this.name = 'ApiClientError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    let envelope: ErrorEnvelope = {}
    try {
      envelope = (await response.json()) as ErrorEnvelope
    } catch {
      // A stable fallback is more useful than leaking an HTML proxy error.
    }
    throw new ApiClientError(
      response.status,
      envelope.error?.code ?? 'HTTP_ERROR',
      envelope.error?.message ?? `请求失败（HTTP ${response.status}）`,
      envelope.error?.details,
    )
  }
  return (await response.json()) as T
}

export const api = {
  listAgents: () => request<Agent[]>('/agents'),
  createAgent: (payload: AgentCreate) =>
    request<Agent>('/agents', { method: 'POST', body: JSON.stringify(payload) }),
  patchAgent: (id: string, payload: AgentPatch) =>
    request<Agent>(`/agents/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),
  publishAgent: (id: string) =>
    request<AgentVersion>(`/agents/${id}/publish`, { method: 'POST' }),
  listVersions: (id: string) => request<AgentVersion[]>(`/agents/${id}/versions`),
  listTools: () => request<ToolMetadata[]>('/tools'),
  capabilities: () => request<Capability>('/capabilities'),
  listSessions: () => request<Session[]>('/sessions'),
  createSession: (agentId: string, title: string) =>
    request<Session>('/sessions', {
      method: 'POST',
      body: JSON.stringify({ agent_id: agentId, title }),
    }),
  listMessages: (sessionId: string) => request<Message[]>(`/sessions/${sessionId}/messages`),
  createRun: (sessionId: string, input: string) =>
    request<Run>(`/sessions/${sessionId}/runs`, {
      method: 'POST',
      body: JSON.stringify({ input }),
    }),
  getRun: (runId: string) => request<Run>(`/runs/${runId}`),
  cancelRun: (runId: string) => request<Run>(`/runs/${runId}/cancel`, { method: 'POST' }),
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '发生未知错误'
}
