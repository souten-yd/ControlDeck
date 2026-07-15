import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { api } from "./client";
import type { HostInfo, ManagedApp, Meta, MetricsSnapshot, UserInfo } from "../types";
import { useToasts } from "../stores";

export function useMeta() {
  return useQuery({
    queryKey: ["meta"],
    queryFn: () => api<Meta>("/meta"),
    staleTime: Infinity,
  });
}

export function useMe(enabled = true) {
  return useQuery({
    queryKey: ["me"],
    queryFn: () => api<UserInfo>("/auth/me"),
    retry: false,
    enabled,
  });
}

export function useApps() {
  return useQuery({
    queryKey: ["apps"],
    queryFn: () => api<ManagedApp[]>("/apps"),
    // systemd/プロセスツリー/待受ポートを走査するため、常時の再取得は抑える。
    // 操作時はuseAppActionが楽観更新し、完了後に明示invalidateする。
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
  });
}

export function useOverview() {
  return useQuery({
    queryKey: ["overview"],
    queryFn: () =>
      api<{ metrics: MetricsSnapshot | Record<string, never>; host: HostInfo }>(
        "/system/overview",
      ),
    staleTime: 10_000,
  });
}

/** アプリ操作（start/stop/restart/kill）。楽観的に状態を切り替える。 */
export function useAppAction() {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  return useMutation({
    mutationFn: ({ id, action }: { id: number; action: string }) =>
      api<ManagedApp>(`/apps/${id}/${action}`, { method: "POST" }),
    onMutate: async ({ id, action }) => {
      await qc.cancelQueries({ queryKey: ["apps"] });
      const prev = qc.getQueryData<ManagedApp[]>(["apps"]);
      const optimistic: Record<string, string> = {
        start: "STARTING",
        stop: "STOPPING",
        restart: "RESTARTING",
        kill: "STOPPING",
      };
      qc.setQueryData<ManagedApp[]>(["apps"], (old) =>
        old?.map((a) =>
          a.id === id
            ? { ...a, runtime: { ...a.runtime, status: optimistic[action] as never } }
            : a,
        ),
      );
      return { prev };
    },
    onError: (e, _vars, ctx) => {
      if (ctx?.prev) qc.setQueryData(["apps"], ctx.prev);
      show(e instanceof Error ? e.message : "操作に失敗しました", "error");
    },
    onSuccess: (updated) => {
      qc.setQueryData<ManagedApp[]>(["apps"], (old) =>
        old?.map((a) => (a.id === updated.id ? updated : a)),
      );
    },
    onSettled: () => {
      setTimeout(() => qc.invalidateQueries({ queryKey: ["apps"] }), 1500);
    },
  });
}

export function useDeleteApp() {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  return useMutation({
    mutationFn: (id: number) => api(`/apps/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      show("アプリを削除しました");
      qc.invalidateQueries({ queryKey: ["apps"] });
    },
    onError: (e) => show(e instanceof Error ? e.message : "削除に失敗しました", "error"),
  });
}
