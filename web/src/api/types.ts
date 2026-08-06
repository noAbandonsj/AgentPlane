import type { components } from './schema'

export type Agent = components['schemas']['AgentRead']
export type AgentCreate = components['schemas']['AgentCreate']
export type AgentPatch = components['schemas']['AgentPatch']
export type AgentVersion = components['schemas']['AgentVersionRead']
export type Capability = components['schemas']['CapabilityResponse']
export type Message = components['schemas']['MessageRead']
export type Run = components['schemas']['RunRead']
export type Session = components['schemas']['SessionRead']
export type ToolMetadata = components['schemas']['ToolMetadata']

export interface RunEvent {
  id: string
  run_id: string
  sequence: number
  event_type: string
  payload: Record<string, unknown>
  trace_id: string
  created_at: string
}
