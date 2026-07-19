/** API クライアント。CSRF ヘッダー付与と 401 ハンドリングを一元化する。 */

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

export async function api<T = unknown>(
  path: string,
  options: RequestInit & { json?: unknown } = {},
): Promise<T> {
  const { json, ...init } = options;
  const headers: Record<string, string> = {
    "X-Requested-With": "ControlDeck",
    ...(init.headers as Record<string, string>),
  };
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(json);
  }
  const res = await fetch(`/api/v1${path}`, {
    credentials: "same-origin",
    ...init,
    headers,
  });
  if (!res.ok) {
    let detail = res.status === 401 ? "認証が必要です" : `エラー (${res.status})`;
    let responseDetail: unknown;
    try {
      const body = await res.json();
      responseDetail = body.detail;
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) {
        const issues = body.detail.flatMap((issue: unknown) => {
          if (!issue || typeof issue !== "object") return [];
          const item = issue as { loc?: unknown; msg?: unknown };
          if (typeof item.msg !== "string") return [];
          const location = Array.isArray(item.loc)
            ? item.loc.filter((part) => part !== "body").map(String).join(".")
            : "";
          return [location ? `${location}: ${item.msg}` : item.msg];
        });
        if (issues.length > 0) detail = issues.join(" / ");
      } else if (body.detail && typeof body.detail === "object") {
        const structured = body.detail as { blocking?: unknown; warnings?: unknown };
        if (Array.isArray(structured.blocking) && structured.blocking.every((item) => typeof item === "string")) {
          detail = `公開できません: ${structured.blocking.join(" / ")}`;
        }
      }
    } catch {
      /* JSON でないレスポンス */
    }
    // ログイン系以外の 401 はセッション失効としてグローバル処理する
    if (res.status === 401 && !path.startsWith("/auth/login")) {
      onUnauthorized?.();
    }
    throw new ApiError(res.status, detail, responseDetail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function wsUrl(path: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}/api/v1${path}`;
}
