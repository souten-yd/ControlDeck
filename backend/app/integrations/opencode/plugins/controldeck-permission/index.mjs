// ControlDeck の権限モード（OpenCode v2 プラグイン、依存なし）。
//
// ControlDeck は判断させたい操作（shell・edit・write・webfetch・execute）を設定で ask にし、
// ここの evaluate で許可・確認を決める。禁止リスト（systemctl・shutdown・deck.sh の管理など）は
// 設定の deny がこのフックより先に決まるので、ここでは触らない（緩めることもできない）。
//
// モードは CONTROLDECK_PERMISSION_FILE（JSON {"mode": "ask" | "auto"}）から呼ばれるたびに読む。
// 画面で切り替えた結果が、サーバーを起こし直さずに次の操作から効く。
//   ask  … 判断させる操作はすべて確認（Claude Code の既定に近い）
//   auto … 編集・読むだけのコマンド・テスト/ビルドは自動、危ない操作と判断できないものは確認
import { readFileSync, statSync } from "node:fs";

const FILE = process.env.CONTROLDECK_PERMISSION_FILE || "";
// 箱の外（host）で動くときは境界が無いので、Auto でもコマンドは自動にしない
const HOST = process.env.CONTROLDECK_ISOLATION === "host";
let cache = { mtime: -1, mode: "auto" };

export function currentMode() {
  if (!FILE) return "auto";
  try {
    const mtime = statSync(FILE).mtimeMs;
    if (mtime !== cache.mtime) {
      const mode = JSON.parse(readFileSync(FILE, "utf8")).mode;
      cache = { mtime, mode: mode === "ask" ? "ask" : "auto" };
    }
  } catch {
    // 読めなければ安全側（確認）に倒す
    return "ask";
  }
  return cache.mode;
}

// 危ない形（どれか 1 つでも含めば確認）。消す・上書きする・外へ出す・権限や状態を変える。
const DANGEROUS = [
  /(^|[\s;&|(])(rm|rmdir|shred|truncate|dd|mkfs\w*|wipefs)(\s|$)/,
  /(^|[\s;&|(])(mv|chmod|chown|chgrp|ln)\s/,
  /(^|[\s;&|(])(sudo|su|doas|pkexec)(\s|$)/,
  /(^|[\s;&|(])(curl|wget|scp|rsync|ssh|sftp|ftp|nc|ncat|telnet)(\s|$)/,
  /(^|[\s;&|(])(kill|pkill|killall)(\s|$)/,
  /(^|[\s;&|(])(docker|podman|kubectl)(\s|$)/,
  /(^|[\s;&|(])git\s+(push|reset|clean|rebase|checkout\s+--|restore|stash\s+(drop|clear)|branch\s+-[dD]|tag\s+-d|filter-branch|gc)/,
  /(^|[\s;&|(])(npm|pnpm|yarn|bun)\s+(i|install|add|remove|uninstall|publish|update|upgrade|ci)(\s|$)/,
  /(^|[\s;&|(])(pip|pip3|uv|poetry|pipx)\s+(install|uninstall|add|remove|sync)(\s|$)/,
  /(^|[\s;&|(])python3?\s+-m\s+pip\s/,
  /(^|[\s;&|(])(apt|apt-get|dnf|yum|snap|brew)(\s|$)/,
  /(^|[\s;&|(])find\s.*\s-(delete|exec|execdir|ok)(\s|$)/,
  /(^|[\s;&|(])(sed|perl)\s+(-[a-zA-Z]*i)/,
  /(^|[^<])>\s*[^&\s]/, // 上書きのリダイレクト（>&2 などは除く）
  /\|\s*(sh|bash|zsh|python3?|node)(\s|$)/,
  /(^|[\s;&|(])(eval|exec|source)\s/,
  // 1 つのコマンドの中で別のコマンドを走らせる形（判定を素通りする）
  /`|\$\(|<\(|>\(|\n|\r/,
  /(^|[\s;&|(])(sh|bash|zsh|dash|fish|python3?|node|deno|bun|perl|ruby|php)\s+(-[a-zA-Z]*[ce]\b|<<)/,
  /(^|[\s;&|(])(xargs|nohup|setsid|timeout|nice|ionice|stdbuf|watch)(\s|$)/,
  /(^|[\s;&|(])find\s+(.*\s)?-(fprint|fprintf|fls|fprint0)(\s|$)/,
  /(^|[\s;&|(])sort\s+(.*\s)?(-[a-zA-Z]*o|--output)(\s|=|$)/,
  /--output(=|\s)/,
  /(^|[\s;&|(])git\s+(switch|checkout)\s.*(-f\b|--force|--discard-changes)/,
  /(^|[\s;&|(])git\s+branch\s.*(-f\b|--force|-M\b|-C\b)/,
  /(^|[\s;&|(])git\s+-c\s/,
];

// 自動でよい形（全体がこれだけで出来ていること）。読むだけ・テスト・ビルド・整形。
const SAFE_HEAD = new Set([
  "ls", "cat", "head", "tail", "wc", "pwd", "echo", "printf", "grep", "rg", "ag", "find", "fd", "tree",
  "file", "stat", "du", "df", "which", "date", "diff", "cmp", "sort", "uniq", "cut", "tr",
  "jq", "basename", "dirname", "realpath", "readlink", "sha256sum", "md5sum", "true", "test", "[",
  "mkdir", "touch", "cd",
]);
const SAFE_COMMANDS = [
  /^git\s+(status|diff|log|show|branch|rev-parse|ls-files|blame|describe|remote\s+-v|add|commit|switch|fetch)(\s|$)/,
  /^(npm|pnpm|yarn|bun)\s+(run\s+)?(test|build|lint|typecheck|check|format)(\s|$)/,
  /^npx\s+(tsc|vitest|jest|eslint|prettier|playwright\s+test)(\s|$)/,
  /^(python3?|\.?\.?\/?\.?venv\/bin\/python3?)\s+-m\s+(pytest|unittest|compileall|py_compile|mypy|ruff|black)(\s|$)/,
  /^(pytest|ruff|mypy|black|tsc|eslint|prettier|cargo\s+(build|test|check|fmt|clippy)|go\s+(build|test|vet|fmt)|make(\s+(test|build|check))?)(\s|$)/,
  /^node\s+--check\s/,
];

function splitCommands(command) {
  return command.split(/&&|\|\||;|\|/).map((part) => part.trim()).filter(Boolean);
}

export function judgeShell(command) {
  const text = String(command || "").trim();
  if (!text) return { effect: "ask", message: "空のコマンド" };
  for (const pattern of DANGEROUS) {
    if (pattern.test(text)) return { effect: "ask", message: "危ない可能性のある操作のため確認します" };
  }
  const parts = splitCommands(text);
  const safe = parts.every((part) => {
    const head = part.split(/\s+/)[0];
    return SAFE_HEAD.has(head) || SAFE_COMMANDS.some((pattern) => pattern.test(part));
  });
  if (safe) return { effect: "allow", message: "Auto: 読むだけ・テスト・ビルドのコマンド" };
  return { effect: "ask", message: "Auto: 自動で判断できないため確認します" };
}

// プラン（OpenCode の plan エージェント）で通してよいコマンド: 読むだけ（何も変えない）
const PLAN_READ_HEAD = new Set([
  "ls", "cat", "head", "tail", "wc", "pwd", "echo", "printf", "grep", "rg", "ag", "find", "fd", "tree",
  "file", "stat", "du", "df", "which", "date", "diff", "cmp", "sort", "uniq", "cut", "tr",
  "jq", "basename", "dirname", "realpath", "readlink", "sha256sum", "md5sum", "true", "test", "[", "cd",
]);
const PLAN_READ_COMMANDS = [/^git\s+(status|diff|log|show|rev-parse|ls-files|blame|describe|remote\s+-v)(\s|$)/];

// プランでは何も変えない（2026-11-18 利用者「プランの際はビルドモードではないことが明確にわかること」）。
// 編集・書き込みは止め、シェルは読むだけを通し、ほかは止める。ControlDeck の道具（MCP）は確認する。
// 権限の確認モード（ask）でも同じ（プランで編集を許可させない）。
export function decidePlan(action, resources) {
  switch (action) {
    case "edit":
    case "write":
      return { effect: "deny", message: "プラン: 編集しません。直すにはビルドに切り替えてください" };
    case "shell": {
      const text = (resources || []).map((r) => String(r || "").trim());
      const readOnly = text.length > 0 && text.every((command) => judgeShell(command).effect === "allow"
        && splitCommands(command).every((part) => PLAN_READ_HEAD.has(part.split(/\s+/)[0]) || PLAN_READ_COMMANDS.some((p) => p.test(part))));
      return readOnly ? { effect: "allow", message: "プラン: 読むだけのコマンド" }
        : { effect: "deny", message: "プラン: 変更を伴うコマンドは実行しません。ビルドに切り替えてください" };
    }
    case "execute":
      return { effect: "ask", message: "プラン: ControlDeck の道具は確認します" };
    default:
      return null;
  }
}

export function decide(mode, action, resources, agent) {
  if (agent === "plan") return decidePlan(action, resources);
  if (mode !== "auto") return null; // 確認モード: 設定どおり（ask）
  switch (action) {
    case "edit":
    case "write":
      return { effect: "allow", message: "Auto: プロジェクト内の編集" };
    case "execute":
      return { effect: "allow", message: "Auto: ControlDeck の道具（MCP）" };
    case "shell": {
      if (HOST) return { effect: "ask", message: "Auto: 箱の外（host）で動いているため確認します" };
      const verdicts = (resources || []).map(judgeShell);
      const asked = verdicts.find((verdict) => verdict.effect !== "allow");
      return asked || verdicts[0] || { effect: "ask", message: "空のコマンド" };
    }
    default:
      return null; // webfetch など外へ出るものは確認のまま
  }
}

export default {
  id: "controldeck.permission",
  async setup(ctx) {
    await ctx.permission.hook("evaluate", (event) => {
      if (event.effect !== "ask") return; // allow / deny の規則で決まったものは触らない
      const verdict = decide(currentMode(), event.action, event.resources, event.agent);
      if (!verdict) return;
      event.effect = verdict.effect;
      event.message = verdict.message;
    });
  },
};
