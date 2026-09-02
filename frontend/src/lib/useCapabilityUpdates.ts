import { useEffect, useMemo, useRef } from 'react'
import { useQueries } from '@tanstack/react-query'

import { capabilitiesApi } from './registryApi'
import { apiErrorMessage } from './api'
import { collectOrigins, compareUpdates, type CapabilityDetailLike, type UpdateStatus } from './capabilityUpdates'
import type { WorkflowDoc } from './workflowTypes'

export interface CapabilityUpdatesState {
  statuses: UpdateStatus[]
  checking: boolean
  error: string | null
  check: () => void
}

export function useCapabilityUpdates(wf: WorkflowDoc | null): CapabilityUpdatesState {
  const origins = useMemo(() => (wf ? collectOrigins(wf) : []), [wf])
  const names = useMemo(() => [...new Set(origins.map((o) => o.capabilityName))], [origins])

  const queries = useQueries({
    queries: names.map((name) => ({
      queryKey: ['capability', name],
      queryFn: () => capabilitiesApi.detail(name),
    })),
  })

  // One automatic check per editor session: refetch anything already cached
  // the first time stamped origins appear (fresh mounts fetch on their own).
  const didAutoCheck = useRef(false)
  useEffect(() => {
    if (didAutoCheck.current || origins.length === 0) return
    didAutoCheck.current = true
    for (const q of queries) {
      if (q.status === 'success') q.refetch()
    }
  })

  const detailByName = new Map<string, CapabilityDetailLike>()
  let error: string | null = null
  queries.forEach((q, i) => {
    if (q.data) detailByName.set(names[i], q.data)
    else if (q.error) error = apiErrorMessage(q.error)
  })

  const statuses = compareUpdates(origins, detailByName)
  const checking = queries.some((q) => q.isPending || q.isFetching)

  return {
    statuses,
    checking,
    error,
    check: () => {
      for (const q of queries) q.refetch()
    },
  }
}
