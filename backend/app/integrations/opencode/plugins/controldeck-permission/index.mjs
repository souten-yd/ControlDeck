// ControlDeck の権限モード（OpenCode v2 プラグイン、依存なし）。
//
// ControlDeck は判断させたい操作（shell・edit・write・webfetch・execute）を設定で ask にし、
// ここの evaluate で許可・確認を決める。禁止リスト（systemctl・shutdown・deck.sh の管理など）は
// 設定の deny がこのフックより先に決まるので、ここでは触らない（緩めることもできない）。
//
// モードは CONTROLDECK_PERMISSION_FILE（JSON {"mode": "ask" | "auto"}）から呼ばれるたびに読む。
// 画面で切り替えた結果が、サーバーを起こし直さずに次の操作から効く。
//   ask  … 判断させる操作はすべて確認（Claude Code の既定に近い）
//   auto … 箱の中は全部自動（設定の禁止リストを除く）。箱の外（host）は編集だけ自動
import { readFileSync, statSync } from "node:fs";

const FILE = process.env.CONTROLDECK_PERMISSION_FILE || "";
// 箱の外（host）で動くときは境界が無いので、Auto でもコマンドは自動にしない
const HOST = process.env.CONTROLDECK_ISOLATION === "host";
let cache = { stamp: "", mode: "auto", plan: new Set() };

function readState() {
  if (!FILE) return { mode: "auto", plan: new Set() };
  try {
    // 同じ時刻の刻みの中で 2 回書かれても読み直せるように、inode と大きさも見る（置き換えで inode が変わる）
    const info = statSync(FILE);
    const stamp = `${info.mtimeMs}:${info.ino}:${info.size}`;
    if (stamp !== cache.stamp) {
      const data = JSON.parse(readFileSync(FILE, "utf8"));
      cache = { stamp, mode: data.mode === "ask" ? "ask" : "auto",
        plan: new Set(Array.isArray(data.plan_sessions) ? data.plan_sessions.map(String) : []) };
    }
  } catch {
    // 読めなければ安全側（確認）に倒す
    return { mode: "ask", plan: cache.plan };
  }
  return cache;
}

export function currentMode() {
  return readState().mode;
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

// 読むだけの形（何も書かない・ほかのプログラムを動かさない）。プランで通してよいのはこれだけ。
// fd -x・rg --pre・uniq IN OUT・tree -o・file -C のように書いたり実行したりできるものは入れない（レビュー指摘 2026-11-18）
const READ_ONLY_HEAD = new Set([
  "ls", "cat", "head", "tail", "wc", "pwd", "echo", "grep", "rg", "find", "stat", "du", "df", "which", "date",
  "diff", "cmp", "cut", "tr", "jq", "basename", "dirname", "realpath", "readlink", "sha256sum", "md5sum",
  "true", "test", "[", "cd",
]);
// git は読むだけに見えても、リポジトリの設定（core.fsmonitor・diff.external）のプログラムを動かす。プランでは確認する
const GIT_READ = /^git\s+(status|diff|log|show|rev-parse|ls-files|blame|describe|remote\s+-v)(\s|$)/;
// rg --pre は別のプログラムを動かす（grep -C・find -o のような読むための引数は止めない）
const READ_ONLY_FORBIDDEN_ARGS = /(^|\s)--pre(-glob)?(\s|=|$)/;

export function isReadOnly(command) {
  const text = String(command || "").trim();
  if (!text || judgeShell(text).effect !== "allow" || READ_ONLY_FORBIDDEN_ARGS.test(text)) return false;
  return splitCommands(text).every((part) => READ_ONLY_HEAD.has(part.split(/\s+/)[0]) || GIT_READ.test(part));
}

// 自動でよい形（全体がこれだけで出来ていること）。読むだけに、テスト・ビルド・整形・作業用のフォルダ作りを足したもの
const SAFE_HEAD = new Set([
  ...READ_ONLY_HEAD, "printf", "ag", "fd", "tree", "file", "sort", "uniq", "mkdir", "touch",
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

// プランでは何も変えない（2026-11-18 利用者「プランの際はビルドモードではないことが明確にわかること」）。
// 規則で allow に決まったもの（ビルドのときに「いつも許可」と答えたものなど）も止める。止めるのは:
//   編集・書き込み、変更を伴うコマンド、子のエージェント（子はビルドの扱いになり編集できてしまう）。
// 読むだけのコマンドは、Auto かつ箱の中なら通す。確認モード・箱の外では確認する（プランで確認が緩まないように）。
// ControlDeck の道具（MCP）と外への取得は確認する。
export function decidePlan(action, resources, mode = "auto") {
  switch (action) {
    case "edit":
    case "write":
      return { effect: "deny", message: "プラン: 編集しません。直すにはビルドに切り替えてください" };
    case "subagent":
    case "task":
      return { effect: "deny", message: "プラン: 子のエージェントは使いません（ビルドに切り替えると使えます）" };
    case "shell": {
      const commands = (resources || []).map((r) => String(r || "").trim());
      if (commands.length === 0 || !commands.every(isReadOnly)) {
        return { effect: "deny", message: "プラン: 変更を伴うコマンドは実行しません。ビルドに切り替えてください" };
      }
      const usesGit = commands.some((command) => splitCommands(command).some((part) => GIT_READ.test(part)));
      return mode === "auto" && !HOST && !usesGit ? { effect: "allow", message: "プラン: 読むだけのコマンド" }
        : { effect: "ask", message: "プラン: 読むだけのコマンドです（確認モードのため確認します）" };
    }
    case "execute":
      return { effect: "ask", message: "プラン: ControlDeck の道具は確認します" };
    case "webfetch":
      return { effect: "ask", message: "プラン: 外への取得は確認します" };
    default:
      return null;
  }
}

/** このイベントがプランか: エージェント名、または ControlDeck が書いたプランのセッション一覧 */
export function isPlanEvent(event, state = readState()) {
  return event?.agent === "plan" || (event?.sessionID != null && state.plan.has(String(event.sessionID)));
}

export function decide(mode, action, resources) {
  if (mode !== "auto") return null; // 確認モード: 設定どおり（ask）
  // Auto: 箱の中は何をしても確認しない（2026-11-18 利用者「Auto モードは箱の中であればなにしても許可不要で進めて良い」）。
  // 設定の禁止リスト（deny）はこのフックより先に決まるので緩まない
  if (!HOST) return { effect: "allow", message: "Auto: 箱の中" };
  switch (action) {
    case "edit":
    case "write":
      return { effect: "allow", message: "Auto: プロジェクト内の編集" };
    case "execute":
      return { effect: "allow", message: "Auto: ControlDeck の道具（MCP）" };
    case "shell":
      return { effect: "ask", message: "Auto: 箱の外（host）で動いているため確認します" };
    default:
      return null; // webfetch など外へ出るものは確認のまま
  }
}

export default {
  id: "controldeck.permission",
  async setup(ctx) {
    await ctx.permission.hook("evaluate", (event) => {
      if (event.effect === "deny") return; // 禁止は緩めない
      const state = readState();
      if (isPlanEvent(event, state)) {
        // プランは allow で決まったものも見直す（止めるだけ。ask を allow にするのは読むだけのコマンドだけ）
        const verdict = decidePlan(event.action, event.resources, state.mode);
        if (!verdict) return;
        if (event.effect === "allow" && verdict.effect === "allow") return;
        event.effect = verdict.effect;
        event.message = verdict.message;
        return;
      }
      if (event.effect !== "ask") return; // ビルド: allow で決まったものは触らない
      const verdict = decide(state.mode, event.action, event.resources);
      if (!verdict) return;
      event.effect = verdict.effect;
      event.message = verdict.message;
    });
  },
};
