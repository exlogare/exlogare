import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { Capabilities } from "./types";

export function useCapabilities() {
  return useQuery({
    queryKey: ["capabilities"],
    queryFn: () => api<Capabilities>("/api/capabilities"),
    staleTime: 30_000,
  });
}
