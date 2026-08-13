import type { components } from './schema'

export type Agent = components['schemas']['AgentRead']
export type AgentCreate = components['schemas']['AgentCreate']
export type AgentPatch = components['schemas']['AgentPatch']
export type AgentVersion = components['schemas']['AgentVersionRead']
export type ApplicationCredential = components['schemas']['ApplicationCredentialRead']
export type ApplicationCredentialIssued = components['schemas']['ApplicationCredentialIssued']
export type ApplicationCredentialRotate = components['schemas']['ApplicationCredentialRotate']
export type ApplicationPermissions = components['schemas']['ApplicationPermissionsRead']
export type ApplicationPermissionsReplace = components['schemas']['ApplicationPermissionsReplace']
export type AuthLogin = components['schemas']['AuthLogin']
export type AuthRegister = components['schemas']['AuthRegister']
export type BootstrapStatus = components['schemas']['BootstrapStatus']
export type Capability = components['schemas']['CapabilityResponse']
export type CallingApplication = components['schemas']['CallingApplicationRead']
export type CallingApplicationCreate = components['schemas']['CallingApplicationCreate']
export type CallingApplicationCreated = components['schemas']['CallingApplicationCreated']
export type CallingApplicationPatch = components['schemas']['CallingApplicationPatch']
export type ExternalUserMapping = components['schemas']['ExternalUserMappingRead']
export type ExternalUserMappingCreate = components['schemas']['ExternalUserMappingCreate']
export type ExternalUserMappingPatch = components['schemas']['ExternalUserMappingPatch']
export type Invocation = components['schemas']['InvocationRead']
export type InvocationDecision = components['schemas']['InvocationDecision']
export type Message = components['schemas']['MessageRead']
export type Run = components['schemas']['RunRead']
export type Session = components['schemas']['SessionRead']
export type ToolMetadata = components['schemas']['ToolMetadata']
export type User = components['schemas']['UserRead']
export type UserPermissions = components['schemas']['UserPermissionsRead']
export type UserStatus = components['schemas']['UserStatus']

export type InvocationAdminStatus =
  | 'QUEUED'
  | 'RUNNING'
  | 'WAITING_APPROVAL'
  | 'SUCCEEDED'
  | 'FAILED'
  | 'CANCELLED'
  | 'REJECTED'

export interface InvocationFilters {
  applicationId?: string | undefined
  externalRequestId?: string | undefined
  externalUserId?: string | undefined
  agentId?: string | undefined
  decision?: InvocationDecision | undefined
  status?: InvocationAdminStatus | undefined
  createdFrom?: string | undefined
  createdTo?: string | undefined
  limit?: number | undefined
  offset?: number | undefined
}

export interface RunEvent {
  id: string
  run_id: string
  sequence: number
  event_type: string
  payload: Record<string, unknown>
  trace_id: string
  created_at: string
}
