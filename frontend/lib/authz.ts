import { useSession } from './session'

export type CapabilityCheck = string | string[]

export function useCan(): {
  can: (capability: CapabilityCheck) => boolean
} {
  const user = useSession()
  const caps = new Set(user.capabilities ?? [])

  function can(capability: CapabilityCheck): boolean {
    if (Array.isArray(capability)) {
      return capability.length === 0 || capability.every((c) => caps.has(c))
    }
    return caps.has(capability)
  }

  return { can }
}

export function can(userCapabilities: string[], capability: CapabilityCheck): boolean {
  const caps = new Set(userCapabilities ?? [])
  if (Array.isArray(capability)) {
    return capability.length === 0 || capability.every((c) => caps.has(c))
  }
  return caps.has(capability)
}
