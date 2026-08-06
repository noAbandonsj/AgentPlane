import type { components } from './schema'

export type Agent = components['schemas']['AgentRead']
export type AgentCreate = components['schemas']['AgentCreate']
export type AgentPatch = components['schemas']['AgentPatch']
export type AgentVersion = components['schemas']['AgentVersionRead']
export type AuthLogin = components['schemas']['AuthLogin']
export type AuthRegister = components['schemas']['AuthRegister']
export type BootstrapStatus = components['schemas']['BootstrapStatus']
export type Capability = components['schemas']['CapabilityResponse']
export type Message = components['schemas']['MessageRead']
export type Run = components['schemas']['RunRead']
export type Session = components['schemas']['SessionRead']
export type ToolMetadata = components['schemas']['ToolMetadata']
export type User = components['schemas']['UserRead']
export type UserPermissions = components['schemas']['UserPermissionsRead']
export type UserStatus = components['schemas']['UserStatus']

export interface RunEvent {
  id: string
  run_id: string
  sequence: number
  event_type: string
  payload: Record<string, unknown>
  trace_id: string
  created_at: string
}
