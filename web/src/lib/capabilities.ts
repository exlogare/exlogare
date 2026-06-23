import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { PlanCapabilities } from "./types";

export function useCapabilities() {
  return useQuery({
    queryKey: ["plan", "capabilities"],
    queryFn: () => api<PlanCapabilities>("/api/plan/capabilities"),
    staleTime: 30_000,
  });
}
