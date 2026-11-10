import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { passkeyError, passkeysAvailable, registerPasskey } from "../../api/passkeys";
import { BottomSheet } from "../../components/ui";
import { useAuth, useToasts } from "../../stores";

type Key = { id: number; name: string; rp_id: string };
export function PasskeySettings() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("この端末");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const user = useAuth((s) => s.user);
  const show = useToasts((s) => s.show);
  const qc = useQueryClient();
  const { data: keys = [] } = useQuery({ queryKey: ["passkeys", user?.id],
    queryFn: () => api<Key[]>("/auth/passkeys"), enabled: open });
  const close = () => { if (!busy) { setOpen(false); setPassword(""); setCode(""); } };
  const add = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true);
    try {
      await registerPasskey(name, password, code);
      setPassword(""); setCode("");
      await qc.invalidateQueries({ queryKey: ["passkeys"] });
      show("パスキーを登録しました。次回から顔認証などでログインできます");
    } catch (error) { show(passkeyError(error), "error"); }
    finally { setBusy(false); }
  };
  const remove = async (key: Key) => {
    if (!window.confirm(`「${key.name}」のパスキーを削除しますか？`)) return;
    setBusy(true);
    try {
      await api(`/auth/passkeys/${key.id}`, { method: "DELETE" });
      await qc.invalidateQueries({ queryKey: ["passkeys"] });
      show("パスキーを削除しました");
    } catch (error) { show(passkeyError(error), "error"); }
    finally { setBusy(false); }
  };
  const input = "mt-1 w-full rounded-xl border border-zinc-300 bg-transparent px-3 py-2 text-sm dark:border-zinc-700";
  return <div className="flex items-center justify-between gap-4">
    <div><p className="text-sm">顔認証・パスキー</p>
      <p className="mt-0.5 text-xs text-zinc-400">端末の顔認証・指紋・PINでログイン</p></div>
    <button type="button" onClick={() => setOpen(true)} className="shrink-0 rounded-lg bg-accent-50 px-3 py-1.5 text-xs text-accent-700 dark:bg-accent-600/15 dark:text-accent-400">管理</button>
    {open && <BottomSheet title="顔認証・パスキー" onClose={close}>
      <p className="mb-4 text-sm text-zinc-500">Face IDなどの本人確認は端末で行います。顔画像は保存しません。登録時と同じ接続先でログインしてください。</p>
      <ul className="mb-4 space-y-2">{keys.map((key) => <li key={key.id} className="flex items-center justify-between gap-2 text-sm">
        <span className="min-w-0 break-all">{key.name}<small className="block text-zinc-500">{key.rp_id}</small></span>
        <button type="button" disabled={busy} onClick={() => void remove(key)} className="shrink-0 text-red-600">削除</button>
      </li>)}</ul>
      {passkeysAvailable() ? <form onSubmit={add} className="space-y-3">
        <label className="block text-xs">パスキーの名前<input className={input} value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required /></label>
        <label className="block text-xs">現在のパスワード<input className={input} type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
        {user?.totp_enabled && <label className="block text-xs">認証コード<input className={input} autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} required /></label>}
        <button disabled={busy} className="w-full rounded-xl bg-accent-600 py-2.5 text-sm text-white disabled:opacity-50">{busy ? "確認中…" : "パスキーを登録"}</button>
      </form> : <p role="status" className="text-sm text-zinc-500">登録にはHTTPS接続とパスキー対応ブラウザが必要です（localhostは利用可能）。</p>}
    </BottomSheet>}
  </div>;
}
