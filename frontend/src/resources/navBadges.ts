import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/api/client";
import { useAuth } from "@/hooks/useAuth";

export interface PortalOverview {
  pending_advance_requests: number;
  open_issues: number;
}

/** Counts of employee requests waiting for HR (GET /hr/portal-overview/). Quietly empty without HR access. */
export function usePortalOverview() {
  const { user } = useAuth();
  return useQuery({
    queryKey: ["hr", "portal-overview"],
    queryFn: async () => (await apiClient.get<PortalOverview>("/hr/portal-overview/")).data,
    enabled: !!user,
    retry: false,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

/** Live count badges for nav links, keyed by NavLink.badge. */
export function useNavBadges(): Record<string, number> {
  const { data } = usePortalOverview();
  return useMemo(
    () => ({ "hr-inbox": (data?.pending_advance_requests ?? 0) + (data?.open_issues ?? 0) }),
    [data]
  );
}

/** Small count pill. */
export const badgeClass =
  "ms-auto shrink-0 min-w-[1.25rem] rounded-full bg-indigo-600 px-1.5 text-center text-[11px] font-semibold leading-5 text-white";
