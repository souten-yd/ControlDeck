import { useState } from "react";
import { api } from "../../api/client";
import { useAuth, useToasts } from "../../stores";
import { BottomSheet, ConfirmDialog } from "../../components/ui";

interface SetupResponse {
  secret: string;
  qr_data_uri: string;
  provisioning_uri: string;
}

export function TotpSettings() {
  const user = useAuth((s) => s.user);
  const setUser = useAuth((s) => s.setUser);
  const show = useToasts((s) => s.show);
  const [setup, setSetup] = useState<SetupResponse | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [recovery, setRecovery] = useState<string[] | null>(null);
  const [disabling, setDisabling] = useState(false);

  const refreshUser = async () => {
    try {
      setUser(await api("/auth/me"));
    } catch {
      /* ignore */
    }
  };

  const startSetup = async () => {
    setBusy(true);
    try {
      setSetup(await api<SetupResponse>("/auth/totp/setup", { method: "POST" }));
    } catch (e) {
      show(e instanceof Error ? e.message : "開始に失敗しました", "error");
    } finally {
      setBusy(false);
    }
  };

  const verify = async () => {
    setBusy(true);
    try {
      const res = await api<{ recovery_codes: string[] }>("/auth/totp/verify", {
        method: "POST",
        json: { code },
      });
      setRecovery(res.recovery_codes);
      setSetup(null);
      setCode("");
      await refreshUser();
      show("二要素認証を有効化しました");
    } catch (e) {
      show(e instanceof Error ? e.message : "コードが正しくありません", "error");
    } finally {
      setBusy(false);
    }
  };

  const disable = async (disableCode: string) => {
    setBusy(true);
    try {
      await api("/auth/totp/disable", { method: "POST", json: { code: disableCode } });
      await refreshUser();
      show("二要素認証を無効化しました");
      setDisabling(false);
    } catch (e) {
      show(e instanceof Error ? e.message : "コードが正しくありません", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex items-center justify-between gap-4">
      <div>
        <p className="text-sm">
          二要素認証（TOTP）
          {user?.totp_enabled ? (
            <span className="ml-2 rounded bg-emerald-50 px-1.5 py-0.5 text-[10px] font-medium text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-400">有効</span>
          ) : user?.totp_required ? (
            <span className="ml-2 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700 dark:bg-amber-950/40 dark:text-amber-400">推奨</span>
          ) : null}
        </p>
        {user?.totp_enabled && (
          <p className="mt-0.5 text-xs text-zinc-400">リカバリーコード残り {user.recovery_codes_remaining} 個</p>
        )}
      </div>
      {user?.totp_enabled ? (
        <button onClick={() => setDisabling(true)} className="shrink-0 rounded-lg px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 dark:text-red-400 dark:hover:bg-red-950/40">
          無効化
        </button>
      ) : (
        <button onClick={startSetup} disabled={busy} className="shrink-0 rounded-lg bg-accent-50 px-3 py-1.5 text-xs font-medium text-accent-700 hover:bg-accent-100 disabled:opacity-40 dark:bg-accent-600/15 dark:text-accent-400">
          有効化
        </button>
      )}

      {/* セットアップ（QR + コード確認） */}
      {setup && (
        <BottomSheet title="二要素認証の設定" onClose={() => setSetup(null)}>
          <ol className="mb-4 space-y-1 text-sm text-zinc-500">
            <li>1. 認証アプリ（Google Authenticator 等）で QR を読み取る</li>
            <li>2. 表示された 6 桁コードを入力する</li>
          </ol>
          <div className="mb-4 flex justify-center">
            <img src={setup.qr_data_uri} alt="QR コード" className="h-48 w-48 rounded-lg bg-white p-2" />
          </div>
          <p className="mb-3 break-all rounded-lg bg-zinc-50 p-2 text-center font-mono text-xs text-zinc-500 dark:bg-zinc-800">
            {setup.secret}
          </p>
          <input
            value={code}
            onChange={(e) => setCode(e.target.value)}
            inputMode="numeric"
            placeholder="000000"
            className="num mb-3 w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 text-center text-lg tracking-widest dark:border-zinc-700 dark:bg-zinc-900"
          />
          <button onClick={verify} disabled={busy || code.length < 6} className="w-full rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-40">
            確認して有効化
          </button>
        </BottomSheet>
      )}

      {/* リカバリーコード表示（1 回だけ） */}
      {recovery && (
        <BottomSheet title="リカバリーコード" onClose={() => setRecovery(null)}>
          <p className="mb-3 text-sm text-zinc-500">
            認証アプリを使えないときに 1 回ずつ使えます。<strong className="text-red-600 dark:text-red-400">今だけ表示されます</strong>。安全な場所に保管してください。
          </p>
          <div className="mb-4 grid grid-cols-2 gap-2 rounded-xl bg-zinc-50 p-3 dark:bg-zinc-800">
            {recovery.map((c) => (
              <code key={c} className="text-center font-mono text-sm">{c}</code>
            ))}
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => { navigator.clipboard.writeText(recovery.join("\n")); show("コピーしました", "info"); }}
              className="flex-1 rounded-xl bg-zinc-100 py-2.5 text-sm font-medium text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-700 dark:text-zinc-200"
            >
              コピー
            </button>
            <button onClick={() => setRecovery(null)} className="flex-1 rounded-xl bg-accent-600 py-2.5 text-sm font-medium text-white hover:bg-accent-700">
              保管しました
            </button>
          </div>
        </BottomSheet>
      )}

      {/* 無効化確認（コード要求） */}
      {disabling && (
        <DisableDialog busy={busy} onConfirm={disable} onClose={() => setDisabling(false)} />
      )}
    </div>
  );
}

function DisableDialog({ busy, onConfirm, onClose }: { busy: boolean; onConfirm: (code: string) => void; onClose: () => void }) {
  const [code, setCode] = useState("");
  return (
    <ConfirmDialog
      title="二要素認証を無効化しますか？"
      message="確認のため現在の認証コードまたはリカバリーコードを入力してください。"
      confirmLabel="無効化する"
      busy={busy}
      onConfirm={() => onConfirm(code)}
      onClose={onClose}
    >
      <input
        value={code}
        onChange={(e) => setCode(e.target.value)}
        inputMode="numeric"
        placeholder="000000"
        className="num mt-3 w-full rounded-xl border border-zinc-300 bg-white px-3.5 py-2.5 text-center tracking-widest dark:border-zinc-700 dark:bg-zinc-900"
      />
    </ConfirmDialog>
  );
}
