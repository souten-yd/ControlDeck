import { api } from "./client";

/** Add-on の agent tool（MCP）実行。job の kind は
 *  `addon.agent_tool.{addon_id}.{contribution_id}` という形で作られる。 */
export const AGENT_TOOL_KIND_PREFIX = "addon.agent_tool.";

export interface AgentToolJob {
  id: string;
  kind: string;
  /** `{addon_id}: {contribution_id}` の形で作られる。 */
  title: string;
  status: string;
  error: string;
  created_at?: number | null;
  finished_at?: number | null;
}

/** kind は前方一致なので、この prefix だけで add-on ツールの実行を絞り込める。 */
export const listAgentToolJobs = (limit = 20) =>
  api<AgentToolJob[]>(
    `/jobs?kind=${encodeURIComponent(AGENT_TOOL_KIND_PREFIX)}&limit=${limit}`,
  );

/** job の kind から add-on とツールを取り出す。
 *
 *  title でも同じ情報は取れるが、add-on 側が自由に付けられる文字列なので、
 *  表示の分解には kind を使う。addon_id には `.` を含められないので、
 *  prefix を外した後の最初の `.` までが add-on になる。 */
export function splitAgentToolKind(kind: string): { addon: string; tool: string } {
  const rest = kind.startsWith(AGENT_TOOL_KIND_PREFIX)
    ? kind.slice(AGENT_TOOL_KIND_PREFIX.length)
    : kind;
  const cut = rest.indexOf(".");
  return cut < 0
    ? { addon: rest, tool: "" }
    : { addon: rest.slice(0, cut), tool: rest.slice(cut + 1) };
}
