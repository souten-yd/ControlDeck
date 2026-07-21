import { useEffect, useState } from "react";
import type { ApplicationEntity, ApplicationEntityFieldType, ApplicationQuery } from "../../api/applicationBuilder";

const identifier = /^[A-Za-z][A-Za-z0-9_-]{0,127}$/;
type Endpoint = Record<string, unknown>;
type QueryField = { id: string; type: ApplicationEntityFieldType | "string" | "datetime"; nullable: boolean };

export function QueryEditor({ queries, entities, apiEndpoints, onChange }: {
  queries: ApplicationQuery[];
  entities: ApplicationEntity[];
  apiEndpoints: Endpoint[];
  onChange: (queries: ApplicationQuery[]) => void;
}) {
  const [selectedId, setSelectedId] = useState(queries[0]?.id ?? "");
  const [newId, setNewId] = useState("");
  useEffect(() => setSelectedId((current) => queries.some((query) => query.id === current) ? current : queries[0]?.id ?? ""), [queries]);
  const selectedIndex = queries.findIndex((query) => query.id === selectedId);
  const selected = queries[selectedIndex];
  const eligibleEntities = entities.filter((entity) => entity.crud?.enabled && entity.crud.operations.includes("list"));
  const eligibleEndpoints = apiEndpoints.filter((endpoint) => String(endpoint.mode ?? "sync") === "sync" && !String(endpoint.path ?? "").includes("{"));
  const canAdd = Boolean(eligibleEntities.length || eligibleEndpoints.length);
  const update = (patch: Partial<ApplicationQuery>) => {
    if (selectedIndex < 0 || !selected) return;
    onChange(queries.map((query, index) => index === selectedIndex ? { ...selected, ...patch } : query));
  };
  const add = () => {
    const id = newId.trim();
    if (!identifier.test(id) || queries.some((query) => query.id === id) || !canAdd) return;
    const entity = eligibleEntities[0]; const endpoint = eligibleEndpoints[0];
    const query: ApplicationQuery = entity
      ? { id, source: "entity", entityId: entity.id, filters: [], sort: [], pagination: "offset", limit: 20, autoLoad: true, cachePolicy: "memory", staleTimeSeconds: 30 }
      : { id, source: "api", endpointId: String(endpoint.id ?? ""), input: {}, resultPath: defaultResultPath(endpoint), filters: [], sort: [], pagination: "none", limit: 20, autoLoad: true, cachePolicy: "memory", staleTimeSeconds: 30 };
    onChange([...queries, query]); setSelectedId(id); setNewId("");
  };
  const switchSource = (source: ApplicationQuery["source"]) => {
    if (source === "entity") {
      const entity = eligibleEntities[0]; if (!entity) return;
      update({ source, entityId: entity.id, endpointId: null, input: {}, resultPath: "", filters: [], sort: [], pagination: "offset" });
    } else {
      const endpoint = eligibleEndpoints[0]; if (!endpoint) return;
      update({ source, entityId: null, endpointId: String(endpoint.id ?? ""), input: {}, resultPath: defaultResultPath(endpoint), filters: [], sort: [], pagination: "none" });
    }
  };
  const entity = entities.find((item) => item.id === selected?.entityId);
  const fields = entityFields(entity);

  return <section aria-label="Query Editor" className="overflow-hidden rounded-2xl border border-zinc-200 bg-white dark:border-zinc-800 dark:bg-zinc-900">
    <div className="flex flex-wrap items-start gap-3 border-b border-zinc-200 p-4 dark:border-zinc-800"><div className="mr-auto"><h2 className="text-sm font-semibold">Queries</h2><p className="mt-1 max-w-2xl text-xs leading-relaxed text-zinc-500">画面が読むcollectionと取得方針を一度定義し、Tableへ接続します。読み込み、空、失敗、再読込を生成アプリが一貫して扱います。</p></div><span className="rounded-full bg-zinc-100 px-2 py-1 text-[10px] text-zinc-500 dark:bg-zinc-800">{queries.length}/100</span></div>
    {!canAdd && queries.length === 0 ? <div className="p-4 text-xs text-zinc-500">EntityのCRUD list、またはroute parameterなしの同期API endpointを用意するとQueryを追加できます。</div> : <div className="grid gap-4 p-3 lg:grid-cols-[220px_minmax(0,1fr)]">
      <aside><div className="flex gap-2"><input aria-label="New Query ID" value={newId} onChange={(event) => setNewId(event.target.value)} placeholder="recentItems" className="min-h-11 min-w-0 flex-1 rounded-xl border border-zinc-300 bg-transparent px-3 text-xs dark:border-zinc-700" /><button type="button" onClick={add} disabled={!identifier.test(newId.trim()) || queries.some((query) => query.id === newId.trim()) || queries.length >= 100 || !canAdd} className="min-h-11 rounded-xl border border-zinc-300 px-3 text-xs disabled:opacity-40 dark:border-zinc-700">Add</button></div><div className="mt-2 grid gap-1">{queries.map((query) => <button type="button" key={query.id} onClick={() => setSelectedId(query.id)} aria-pressed={selectedId === query.id} className={`min-h-11 rounded-xl px-3 text-left text-xs ${selectedId === query.id ? "bg-zinc-900 text-white dark:bg-white dark:text-zinc-900" : "bg-zinc-50 dark:bg-zinc-800"}`}><strong className="block truncate">{query.id}</strong><span className="text-[9px] opacity-60">{query.source === "api" ? query.endpointId : query.entityId}</span></button>)}</div></aside>
      {selected ? <div className="space-y-3">
        <div className="grid gap-3 sm:grid-cols-2"><label className="text-[10px] text-zinc-400">Query ID · stable<input aria-label="Query ID" value={selected.id} readOnly className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-zinc-50 px-2 font-mono text-xs dark:border-zinc-700 dark:bg-zinc-950" /></label><label className="text-[10px] text-zinc-400">Source<select aria-label="Query source" value={selected.source} onChange={(event) => switchSource(event.target.value as ApplicationQuery["source"])} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700"><option value="entity" disabled={!eligibleEntities.length}>Entity collection</option><option value="api" disabled={!eligibleEndpoints.length}>Synchronous API</option></select></label></div>
        {selected.source === "entity" ? <EntityQueryFields query={selected} fields={fields} eligibleEntities={eligibleEntities} onChange={update} /> : <ApiQueryFields query={selected} endpoints={eligibleEndpoints} onChange={update} />}
        <div className="grid gap-3 sm:grid-cols-2"><label className="text-[10px] text-zinc-400">{selected.source === "entity" && selected.pagination !== "none" ? "Page size" : "Maximum rows"}<input aria-label="Query maximum rows" type="number" min={1} max={100} value={selected.limit} onChange={(event) => update({ limit: Number(event.target.value) })} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700" /></label><label className="text-[10px] text-zinc-400">Cache<select aria-label="Query cache policy" value={selected.cachePolicy} onChange={(event) => update({ cachePolicy: event.target.value as ApplicationQuery["cachePolicy"] })} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700"><option value="memory">Reuse recent result</option><option value="network-only">Always fetch</option></select></label></div>
        {selected.cachePolicy === "memory" && <label className="block text-[10px] text-zinc-400">Fresh for seconds<input aria-label="Query stale time seconds" type="number" min={0} max={3600} value={selected.staleTimeSeconds} onChange={(event) => update({ staleTimeSeconds: Number(event.target.value) })} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700" /></label>}
        <label className="flex min-h-11 items-center gap-2 rounded-lg bg-zinc-50 px-3 text-xs dark:bg-zinc-800"><input aria-label="Query load automatically" type="checkbox" checked={selected.autoLoad} onChange={(event) => update({ autoLoad: event.target.checked })} className="h-5 w-5" />Load when the page opens</label>
        <button type="button" onClick={() => onChange(queries.filter((_query, index) => index !== selectedIndex))} className="min-h-11 w-full rounded-xl border border-red-200 text-xs text-red-600 dark:border-red-900">Delete Query</button>
      </div> : <div className="grid min-h-32 place-items-center text-xs text-zinc-400">Queryを追加してください。</div>}
    </div>}
  </section>;
}

function EntityQueryFields({ query, fields, eligibleEntities, onChange }: { query: ApplicationQuery; fields: QueryField[]; eligibleEntities: ApplicationEntity[]; onChange: (patch: Partial<ApplicationQuery>) => void }) {
  const filters = query.filters ?? []; const sort = query.sort ?? [];
  const selectedAvailable = eligibleEntities.some((entity) => entity.id === query.entityId);
  const addFilter = () => { const field = fields.find((item) => item.type !== "json"); if (field) onChange({ filters: [...filters, { field: field.id, operator: "eq", value: defaultFilterValue(field.type) }] }); };
  const addSort = () => { const field = fields.find((item) => !sort.some((entry) => entry.field === item.id)); if (field) onChange({ sort: [...sort, { field: field.id, direction: "asc" }] }); };
  return <div className="space-y-3"><label className="block text-[10px] text-zinc-400">Entity source<select aria-label="Query Entity source" value={query.entityId ?? ""} onChange={(event) => onChange({ entityId: event.target.value, filters: [], sort: [] })} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700">{!selectedAvailable && <option value={query.entityId ?? ""}>{query.entityId} · list unavailable</option>}{eligibleEntities.map((entity) => <option key={entity.id} value={entity.id}>{entity.displayName || entity.id}</option>)}</select></label>
    {!selectedAvailable && <p role="alert" className="text-xs text-red-600">選択したEntityのCRUD list operationを有効にしてください。</p>}
    <fieldset className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700"><legend className="px-1 text-xs font-semibold">Filters</legend><div className="space-y-2">{filters.map((filter, index) => { const field = fields.find((item) => item.id === filter.field) ?? fields[0]; const operators = queryOperators(field); return <div key={`${filter.field}-${index}`} className="grid gap-2 rounded-lg bg-zinc-50 p-2 sm:grid-cols-[1fr_130px_1fr_auto] dark:bg-zinc-950"><select aria-label={`Filter ${index + 1} field`} value={filter.field} onChange={(event) => { const nextField = fields.find((item) => item.id === event.target.value)!; const next = [...filters]; next[index] = { field: nextField.id, operator: "eq", value: defaultFilterValue(nextField.type) }; onChange({ filters: next }); }} className="min-h-11 rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700">{fields.filter((item) => item.type !== "json").map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}</select><select aria-label={`Filter ${index + 1} operator`} value={filter.operator} onChange={(event) => { const operator = event.target.value as NonNullable<ApplicationQuery["filters"]>[number]["operator"]; const next = [...filters]; next[index] = { ...filter, operator, ...(operator === "is-null" ? { value: undefined } : filter.value === undefined ? { value: defaultFilterValue(field.type) } : {}) }; onChange({ filters: next }); }} className="min-h-11 rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700">{operators.map((operator) => <option key={operator} value={operator}>{operator}</option>)}</select>{filter.operator === "is-null" ? <span className="grid min-h-11 place-items-center text-xs text-zinc-400">Null values</span> : <FilterValue index={index} field={field} value={filter.value} onChange={(value) => { const next = [...filters]; next[index] = { ...filter, value }; onChange({ filters: next }); }} />}<button type="button" aria-label={`Remove filter ${index + 1}`} onClick={() => onChange({ filters: filters.filter((_item, itemIndex) => itemIndex !== index) })} className="min-h-11 px-3 text-xs text-red-600">Remove</button></div>; })}</div><button type="button" onClick={addFilter} disabled={filters.length >= 20 || !fields.some((item) => item.type !== "json")} className="mt-2 min-h-11 w-full rounded-lg border border-zinc-300 text-xs disabled:opacity-40 dark:border-zinc-700">Add filter</button></fieldset>
    <fieldset className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700"><legend className="px-1 text-xs font-semibold">Sort & pagination</legend><div className="space-y-2">{sort.map((entry, index) => <div key={`${entry.field}-${index}`} className="grid grid-cols-[1fr_110px_auto] gap-2"><select aria-label={`Sort ${index + 1} field`} value={entry.field} onChange={(event) => { const next = [...sort]; next[index] = { ...entry, field: event.target.value }; onChange({ sort: next }); }} className="min-h-11 rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700">{fields.map((field) => <option key={field.id} value={field.id}>{field.id}</option>)}</select><select aria-label={`Sort ${index + 1} direction`} value={entry.direction} onChange={(event) => { const next = [...sort]; next[index] = { ...entry, direction: event.target.value as "asc" | "desc" }; onChange({ sort: next }); }} className="min-h-11 rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700"><option value="asc">Ascending</option><option value="desc">Descending</option></select><button type="button" aria-label={`Remove sort ${index + 1}`} onClick={() => onChange({ sort: sort.filter((_item, itemIndex) => itemIndex !== index) })} className="min-h-11 px-3 text-xs text-red-600">Remove</button></div>)}</div><button type="button" onClick={addSort} disabled={sort.length >= 3 || sort.length >= fields.length} className="mt-2 min-h-11 w-full rounded-lg border border-zinc-300 text-xs disabled:opacity-40 dark:border-zinc-700">Add sort</button><label className="mt-2 flex min-h-11 items-center gap-2 rounded-lg bg-zinc-50 px-3 text-xs dark:bg-zinc-800"><input aria-label="Query offset pagination" type="checkbox" checked={query.pagination !== "none"} onChange={(event) => onChange({ pagination: event.target.checked ? "offset" : "none" })} className="h-5 w-5" />Show Previous / Next controls</label></fieldset>
  </div>;
}

function ApiQueryFields({ query, endpoints, onChange }: { query: ApplicationQuery; endpoints: Endpoint[]; onChange: (patch: Partial<ApplicationQuery>) => void }) {
  const selected = endpoints.find((endpoint) => String(endpoint.id ?? "") === query.endpointId);
  return <div className="space-y-3"><label className="block text-[10px] text-zinc-400">Synchronous endpoint<select aria-label="Query API endpoint" value={query.endpointId ?? ""} onChange={(event) => { const endpoint = endpoints.find((item) => String(item.id ?? "") === event.target.value); onChange({ endpointId: event.target.value, input: {}, resultPath: defaultResultPath(endpoint ?? {}) }); }} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700">{!selected && <option value={query.endpointId ?? ""}>{query.endpointId} · unavailable</option>}{endpoints.map((endpoint) => <option key={String(endpoint.id)} value={String(endpoint.id)}>{String(endpoint.id)} · {String(endpoint.path ?? "")}</option>)}</select></label><label className="block text-[10px] text-zinc-400">Collection result path<input aria-label="Query result path" value={query.resultPath ?? ""} placeholder="Blank when response is an array" onChange={(event) => onChange({ resultPath: event.target.value })} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 font-mono text-xs dark:border-zinc-700" /></label><JsonObjectField value={query.input ?? {}} onChange={(input) => onChange({ input })} /><p className="text-[10px] leading-relaxed text-zinc-400">API queryは保存済みの固定inputだけをPOSTします。filter、sort、paginationが必要な場合はendpointのrequest schemaで明示します。</p></div>;
}

function JsonObjectField({ value, onChange }: { value: Record<string, unknown>; onChange: (value: Record<string, unknown>) => void }) {
  const [text, setText] = useState(() => JSON.stringify(value, null, 2)); const [error, setError] = useState("");
  useEffect(() => { setText(JSON.stringify(value, null, 2)); setError(""); }, [value]);
  const commit = () => { try { const parsed = JSON.parse(text); if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error(); onChange(parsed); setError(""); } catch { setError("API inputはJSON objectにしてください。"); } };
  return <label className="block text-[10px] text-zinc-400">Request input<textarea aria-label="Query API input" rows={6} value={text} onChange={(event) => setText(event.target.value)} onBlur={commit} className="mt-1 w-full rounded-lg border border-zinc-300 bg-transparent p-2 font-mono text-xs dark:border-zinc-700" />{error && <span role="alert" className="mt-1 block text-red-600">{error}</span>}</label>;
}

function FilterValue({ index, field, value, onChange }: { index: number; field: QueryField; value: unknown; onChange: (value: unknown) => void }) {
  const className = "min-h-11 min-w-0 rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700";
  if (field.type === "boolean") return <select aria-label={`Filter ${index + 1} value`} value={String(value ?? false)} onChange={(event) => onChange(event.target.value === "true")} className={className}><option value="true">True</option><option value="false">False</option></select>;
  if (field.type === "integer" || field.type === "number") return <input aria-label={`Filter ${index + 1} value`} type="number" step={field.type === "integer" ? 1 : "any"} value={typeof value === "number" ? value : 0} onChange={(event) => onChange(Number(event.target.value))} className={className} />;
  return <input aria-label={`Filter ${index + 1} value`} value={String(value ?? "")} onChange={(event) => onChange(event.target.value)} className={className} />;
}

function entityFields(entity?: ApplicationEntity): QueryField[] {
  if (!entity) return [];
  return [{ id: "id", type: "string", nullable: false }, { id: "createdAt", type: "datetime", nullable: false }, { id: "updatedAt", type: "datetime", nullable: false }, ...entity.fields.map((field) => ({ id: field.id, type: field.type, nullable: Boolean(field.nullable) }))];
}
function queryOperators(field?: QueryField) {
  if (!field) return ["eq"];
  const result = field.type === "string" ? ["eq", "ne", "contains", "starts-with"] : ["integer", "number", "datetime"].includes(field.type) ? ["eq", "ne", "gt", "gte", "lt", "lte"] : ["eq", "ne"];
  if (field.nullable) result.push("is-null");
  return result;
}
function defaultFilterValue(type: QueryField["type"]): unknown { return type === "boolean" ? false : type === "integer" || type === "number" ? 0 : ""; }
function defaultResultPath(endpoint: Endpoint): string {
  const schema = endpoint.responseSchema && typeof endpoint.responseSchema === "object" && !Array.isArray(endpoint.responseSchema) ? endpoint.responseSchema as Record<string, unknown> : {};
  if (schema.type === "array") return "";
  const properties = schema.properties && typeof schema.properties === "object" && !Array.isArray(schema.properties) ? schema.properties as Record<string, unknown> : {};
  return Object.entries(properties).find(([, value]) => value && typeof value === "object" && !Array.isArray(value) && (value as Record<string, unknown>).type === "array")?.[0] ?? "items";
}
