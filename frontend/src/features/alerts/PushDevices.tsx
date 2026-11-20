/** この端末で ControlDeck の通知（Web Push）を受け取る。
 *
 * 2026-11-21 利用者の依頼。通知先が登録されていないと何も届かず、本番には 1 件も無かった。
 * 端末で「受け取る」を押すだけで、Project Run の終わり（起動した人へ）と、通知チャンネル「この
 * ControlDeck の通知」を選んだアラート・自動更新が届く。通知をタップすると、その画面を開く。
 *
 * iPhone・iPad は iOS 16.4 以降で、ホーム画面に追加した ControlDeck の中からだけ受け取れる
 * （Safari のタブからは押せない）。通知への返信はできない。 */
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useToasts } from "../../stores";

interface Device { id: number; label: string; created_at: string | null; last_success_at: string | null }

const supported = () => typeof window !== "undefined" && "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
const isIos = () => /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const standalone = () => window.matchMedia?.("(display-mode: standalone)").matches || (navigator as Navigator & { standalone?: boolean }).standalone === true;

function deviceLabel(): string {
  const ua = navigator.userAgent;
  const os = /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) || isIos() ? "iPad" : /Android/.test(ua) ? "Android"
    : /Mac OS X/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "端末";
  const browser = /Edg\//.test(ua) ? "Edge" : /Firefox\//.test(ua) ? "Firefox" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : "";
  return browser ? `${os}・${browser}` : os;
}

function keyBytes(base64url: string): Uint8Array {
  const padded = (base64url + "=".repeat((4 - (base64url.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
}

async function currentSubscription(): Promise<PushSubscription | null> {
  if (!supported()) return null;
  const registration = await navigator.serviceWorker.ready;
  return registration.pushManager.getSubscription();
}

const when = (iso: string | null) => iso ? new Date(iso).toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : null;

export function PushDevices() {
  const qc = useQueryClient();
  const show = useToasts((s) => s.show);
  const [here, setHere] = useState<string | null | undefined>(undefined);   // この端末の endpoint（undefined は確認中）
  const [busy, setBusy] = useState(false);
  const devices = useQuery({ queryKey: ["push-devices"], queryFn: () => api<{ items: Device[] }>("/push/subscriptions") });

  useEffect(() => {
    let alive = true;
    currentSubscription().then((sub) => { if (alive) setHere(sub?.endpoint ?? null); }).catch(() => { if (alive) setHere(null); });
    return () => { alive = false; };
  }, []);

  const enable = async () => {
    setBusy(true);
    try {
      // 押した操作の中で許可を求める（iOS は操作の外では聞けない）
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        show("通知が許可されていません。端末の設定で ControlDeck の通知を許可してください", "error");
        return;
      }
      const { public_key } = await api<{ public_key: string }>("/push/public-key");
      const registration = await navigator.serviceWorker.ready;
      let sub = await registration.pushManager.getSubscription();
      if (!sub) {
        sub = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(public_key) as BufferSource });
      }
      const json = sub.toJSON();
      await api("/push/subscriptions", { method: "POST", json: { endpoint: sub.endpoint, keys: json.keys, label: deviceLabel() } });
      setHere(sub.endpoint);
      void qc.invalidateQueries({ queryKey: ["push-devices"] });
      show("この端末で通知を受け取ります", "success");
    } catch (error) {
      show(error instanceof Error ? error.message : "通知を受け取る設定にできませんでした", "error");
    } finally {
      setBusy(false);
    }
  };

  const disable = async () => {
    setBusy(true);
    try {
      const sub = await currentSubscription();
      if (sub) {
        await api("/push/subscriptions", { method: "DELETE", json: { endpoint: sub.endpoint } });
        await sub.unsubscribe();
      }
      setHere(null);
      void qc.invalidateQueries({ queryKey: ["push-devices"] });
      show("この端末では受け取りません");
    } catch (error) {
      show(error instanceof Error ? error.message : "止められませんでした", "error");
    } finally {
      setBusy(false);
    }
  };

  const test = useMutation({
    mutationFn: () => api<{ sent: number; removed: number; failed: number }>("/push/test", { method: "POST" }),
    onSuccess: (r) => {
      if (r.sent) show(`${r.sent} 台へ送りました`, "success");
      else show(r.removed ? "端末の登録が切れていました。もう一度「受け取る」を押してください" : "送れませんでした", "error");
      void qc.invalidateQueries({ queryKey: ["push-devices"] });
    },
    onError: () => show("送れませんでした", "error"),
  });
  const remove = useMutation({
    mutationFn: (id: number) => api(`/push/subscriptions/${id}`, { method: "DELETE" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["push-devices"] }),
  });

  const items = devices.data?.items ?? [];
  return (
    <section data-push-devices className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900 md:p-5">
      <h2 className="text-sm font-semibold text-zinc-500">この端末への通知</h2>
      <p className="mt-1 text-xs text-zinc-500">
        Project の実行が終わったとき（起動した人へ）と、通知チャンネル「この ControlDeck の通知」を選んだアラートが届きます。通知を押すと、その画面を開きます。
      </p>
      {isIos() && !standalone() ? (
        <p className="mt-3 rounded-xl bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
          iPhone・iPad では、共有ボタンの「ホーム画面に追加」で置いた ControlDeck から開くと受け取れます（iOS 16.4 以降）。
        </p>
      ) : !supported() ? (
        <p className="mt-3 text-xs text-zinc-500">このブラウザは通知に対応していません。</p>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {here ? (
            <>
              <span className="text-xs font-medium text-emerald-600 dark:text-emerald-400" data-push-state="on">この端末で受け取っています</span>
              <button type="button" onClick={() => test.mutate()} disabled={test.isPending} className="min-h-9 rounded-lg border border-zinc-300 px-3 text-xs dark:border-zinc-700">試しに送る</button>
              <button type="button" onClick={() => void disable()} disabled={busy} className="min-h-9 rounded-lg px-3 text-xs text-zinc-500">止める</button>
            </>
          ) : (
            <button type="button" onClick={() => void enable()} disabled={busy || here === undefined} data-push-state="off"
              className="min-h-10 rounded-xl bg-accent-600 px-4 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40">
              この端末で受け取る
            </button>
          )}
        </div>
      )}
      {items.length > 0 && (
        <ul className="mt-3 divide-y divide-zinc-100 text-xs dark:divide-zinc-800" data-push-device-list>
          {items.map((item) => (
            <li key={item.id} className="flex items-center gap-2 py-1.5">
              <span className="min-w-0 flex-1 truncate">{item.label || "端末"}</span>
              <span className="num shrink-0 text-zinc-500">{when(item.last_success_at) ? `最後に届いた ${when(item.last_success_at)}` : `登録 ${when(item.created_at) ?? ""}`}</span>
              <button type="button" onClick={() => remove.mutate(item.id)} className="min-h-8 shrink-0 rounded px-2 text-zinc-500 hover:text-red-600" aria-label={`${item.label || "端末"}を外す`}>外す</button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
