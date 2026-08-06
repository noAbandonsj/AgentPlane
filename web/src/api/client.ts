import type {
  Agent,
  AgentCreate,
  AgentPatch,
  AgentVersion,
  AuthLogin,
  AuthRegister,
  BootstrapStatus,
  Capability,
  Message,
  Run,
  Session,
  ToolMetadata,
  User,
  UserPermissions,
  UserStatus,
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
    credentials: 'include',
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
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const api = {
  bootstrapStatus: () => request<BootstrapStatus>('/auth/bootstrap-status'),
  bootstrapAdmin: (payload: AuthRegister) =>
    request<User>('/auth/bootstrap-admin', { method: 'POST', body: JSON.stringify(payload) }),
  register: (payload: AuthRegister) =>
    request<User>('/auth/register', { method: 'POST', body: JSON.stringify(payload) }),
  login: (payload: AuthLogin) =>
    request<User>('/auth/login', { method: 'POST', body: JSON.stringify(payload) }),
  logout: () => request<void>('/auth/logout', { method: 'POST' }),
  me: () => request<User>('/auth/me'),
  listAgents: () => request<Agent[]>('/agents'),
  listAdminAgents: () => request<Agent[]>('/admin/agents'),
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
  listUsers: () => request<User[]>('/admin/users'),
  updateUserStatus: (userId: string, status: UserStatus) =>
    request<User>(`/admin/users/${userId}/status`, {
      method: 'PATCH',
      body: JSON.stringify({ status }),
    }),
  getUserPermissions: (userId: string) =>
    request<UserPermissions>(`/admin/users/${userId}/permissions`),
  replaceUserAgentGrants: (userId: string, agentIds: string[]) =>
    request<UserPermissions>(`/admin/users/${userId}/agent-grants`, {
      method: 'PUT',
      body: JSON.stringify({ agent_ids: agentIds }),
    }),
  replaceUserToolGrants: (userId: string, toolKeys: string[]) =>
    request<UserPermissions>(`/admin/users/${userId}/tool-grants`, {
      method: 'PUT',
      body: JSON.stringify({ tool_keys: toolKeys }),
    }),
  replaceUserPermissions: (userId: string, agentIds: string[], toolKeys: string[]) =>
    request<UserPermissions>(`/admin/users/${userId}/permissions`, {
      method: 'PUT',
      body: JSON.stringify({ agent_ids: agentIds, tool_keys: toolKeys }),
    }),
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '发生未知错误'
}
