import { useEffect, useState } from "react";
import type { ApplicationEntity, ApplicationEntityField, ApplicationEntityFieldType } from "../../api/applicationBuilder";

const operations = ["create", "read", "list", "update", "delete"] as const;
const fieldTypes: ApplicationEntityFieldType[] = ["string", "integer", "number", "boolean", "datetime", "json"];
const identifier = /^[A-Za-z][A-Za-z0-9_]{0,127}$/;

export function EntityEditor({ entities, dirty, saving, onChange, onSave, showSave = true }: { entities: ApplicationEntity[]; dirty: boolean; saving: boolean; onChange: (entities: ApplicationEntity[]) => void; onSave: () => void; showSave?: boolean }) {
  const [selectedId, setSelectedId] = useState(entities[0]?.id ?? "");
  const [newId, setNewId] = useState("");
  useEffect(() => {
    setSelectedId((current) => entities.some((item) => item.id === current) ? current : entities[0]?.id ?? "");
  }, [entities]);
  const selected = entities.find((item) => item.id === selectedId);
  const localError = entityEditorError(entities);
  const update = (next: ApplicationEntity) => onChange(entities.map((item) => item.id === next.id ? next : item));
  const add = () => {
    const id = newId.trim(); if (!identifier.test(id) || entities.some((item) => item.id === id)) return;
    const entity: ApplicationEntity = { id, displayName: id, fields: [{ id: "name", type: "string", maxLength: 120 }], crud: { enabled: false, operations: [...operations] } };
    onChange([...entities, entity]); setSelectedId(id); setNewId("");
  };
  const remove = () => {
    if (!selected || !window.confirm(`Entity ${selected.id} と生成先tableの定義を削除しますか？`)) return;
    const next = entities.filter((item) => item.id !== selected.id); onChange(next); setSelectedId(next[0]?.id ?? "");
  };
  return <section aria-label="Entity Editor" className="rounded-2xl border border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900">
    <div className="flex flex-wrap items-start gap-3"><div className="mr-auto"><h2 className="text-sm font-semibold">Entity／SQLite</h2><p className="mt-1 max-w-2xl text-xs leading-relaxed text-zinc-500">型、relation、index、CRUD公開範囲を同じApplication Specで管理します。非互換変更は生成アプリの起動時に停止します。</p></div>{showSave && <button type="button" onClick={onSave} disabled={!dirty || Boolean(localError) || saving} className="min-h-11 rounded-xl bg-accent-600 px-4 text-xs font-semibold text-white disabled:opacity-40">{saving ? "Saving…" : "Save Entities"}</button>}</div>
    <div className="mt-4 grid gap-4 lg:grid-cols-[220px_minmax(0,1fr)]">
      <aside><div className="flex gap-2"><input aria-label="New Entity ID" value={newId} onChange={(event) => setNewId(event.target.value)} placeholder="Project" className="min-h-11 min-w-0 flex-1 rounded-xl border border-zinc-300 bg-transparent px-3 text-xs dark:border-zinc-700" /><button type="button" onClick={add} disabled={!identifier.test(newId.trim()) || entities.some((item) => item.id === newId.trim())} className="min-h-11 rounded-xl border border-zinc-300 px-3 text-xs disabled:opacity-40 dark:border-zinc-700">Add</button></div><div className="mt-2 grid gap-1">{entities.map((item) => <button type="button" key={item.id} onClick={() => setSelectedId(item.id)} aria-pressed={selectedId === item.id} className={`min-h-11 rounded-xl px-3 text-left text-xs ${selectedId === item.id ? "bg-zinc-900 text-white dark:bg-white dark:text-zinc-900" : "bg-zinc-50 dark:bg-zinc-800"}`}><strong className="block truncate">{item.displayName || item.id}</strong><code className="text-[9px] opacity-60">{item.id}</code></button>)}</div></aside>
      {selected ? <EntityForm entity={selected} entities={entities} onChange={update} onRemove={remove} /> : <div className="grid min-h-40 place-items-center rounded-xl bg-zinc-50 p-4 text-center text-xs text-zinc-400 dark:bg-zinc-950">Entity IDを追加してください。</div>}
    </div>
    {localError && <p role="alert" className="mt-3 rounded-xl bg-red-50 p-3 text-xs text-red-700 dark:bg-red-950/30 dark:text-red-300">{localError}</p>}
  </section>;
}

function EntityForm({ entity, entities, onChange, onRemove }: { entity: ApplicationEntity; entities: ApplicationEntity[]; onChange: (entity: ApplicationEntity) => void; onRemove: () => void }) {
  const crud = {
    enabled: Boolean(entity.crud?.enabled),
    operations: Array.isArray(entity.crud?.operations) ? entity.crud.operations : [...operations],
    basePath: entity.crud?.basePath ?? null,
  };
  const setField = (index: number, next: ApplicationEntityField) => onChange({ ...entity, fields: entity.fields.map((item, itemIndex) => itemIndex === index ? next : item) });
  const addField = () => { let index = entity.fields.length + 1; while (entity.fields.some((item) => item.id === `field${index}`)) index += 1; onChange({ ...entity, fields: [...entity.fields, { id: `field${index}`, type: "string", maxLength: 120 }] }); };
  return <div className="min-w-0 space-y-4"><div className="grid gap-3 sm:grid-cols-2"><TextField label="Display name" value={entity.displayName ?? ""} onChange={(displayName) => onChange({ ...entity, displayName })} /><TextField label="SQLite table name" value={entity.tableName ?? ""} placeholder={snakeCase(entity.id)} onChange={(tableName) => onChange({ ...entity, tableName: tableName || null })} /></div>
    <fieldset className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700"><legend className="px-1 text-xs font-semibold">Fields</legend><div className="space-y-3">{entity.fields.map((field, index) => <FieldForm key={`${field.id}-${index}`} field={field} entities={entities} currentEntity={entity.id} onChange={(next) => setField(index, next)} onRemove={() => onChange({ ...entity, fields: entity.fields.filter((_, itemIndex) => itemIndex !== index) })} />)}</div><button type="button" onClick={addField} disabled={entity.fields.length >= 100} className="mt-3 min-h-11 w-full rounded-xl border border-zinc-300 text-xs disabled:opacity-40 dark:border-zinc-700">Add field</button></fieldset>
    <fieldset className="rounded-xl border border-zinc-200 p-3 dark:border-zinc-700"><legend className="px-1 text-xs font-semibold">CRUD API</legend><label className="flex min-h-11 items-center gap-2 text-xs"><input aria-label="Enable Entity CRUD" type="checkbox" checked={Boolean(crud.enabled)} onChange={(event) => onChange({ ...entity, crud: { ...crud, enabled: event.target.checked } })} className="h-5 w-5" />Expose authenticated CRUD routes</label>{crud.enabled && <><TextField label="Base path" value={crud.basePath ?? ""} placeholder={`/api/entities/${kebabCase(entity.id)}`} onChange={(basePath) => onChange({ ...entity, crud: { ...crud, basePath: basePath || null } })} /><div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-5">{operations.map((operation) => <label key={operation} className="flex min-h-11 items-center gap-2 rounded-lg bg-zinc-50 px-2 text-xs dark:bg-zinc-800"><input type="checkbox" aria-label={`CRUD ${operation}`} checked={crud.operations.includes(operation)} onChange={(event) => onChange({ ...entity, crud: { ...crud, operations: event.target.checked ? [...crud.operations, operation] : crud.operations.filter((item) => item !== operation) } })} className="h-4 w-4" />{operation}</label>)}</div></>}</fieldset>
    <button type="button" onClick={onRemove} className="min-h-11 w-full rounded-xl border border-red-200 text-xs text-red-600 dark:border-red-900">Delete Entity definition</button></div>;
}

function FieldForm({ field, entities, currentEntity: _currentEntity, onChange, onRemove }: { field: ApplicationEntityField; entities: ApplicationEntity[]; currentEntity: string; onChange: (field: ApplicationEntityField) => void; onRemove: () => void }) {
  const [defaultText, setDefaultText] = useState(() => formatDefault(field.default));
  useEffect(() => setDefaultText(formatDefault(field.default)), [field.default, field.type]);
  const reference = field.reference ?? null;
  const commitDefault = () => { try { onChange({ ...field, default: parseDefault(defaultText, field.type) }); } catch { /* backend/local validation presents the invalid draft after a typed change */ } };
  return <article className="rounded-xl bg-zinc-50 p-3 dark:bg-zinc-950"><div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_150px_auto]"><TextField label="Field ID" value={field.id} onChange={(id) => onChange({ ...field, id })} /><label className="block text-[10px] text-zinc-400">Type<select aria-label={`Field ${field.id} type`} value={field.type} onChange={(event) => onChange({ ...field, type: event.target.value as ApplicationEntityFieldType, maxLength: event.target.value === "string" ? field.maxLength ?? 120 : null, reference: event.target.value === "string" ? field.reference : null })} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700">{fieldTypes.map((item) => <option key={item}>{item}</option>)}</select></label><button type="button" onClick={onRemove} disabled={false} className="min-h-11 self-end rounded-lg px-3 text-xs text-red-600">Remove</button></div><div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4"><Check label="Nullable" checked={Boolean(field.nullable)} onChange={(nullable) => onChange({ ...field, nullable, ...(field.reference?.onDelete === "set-null" && !nullable ? { reference: { ...field.reference, onDelete: "restrict" } } : {}) })} /><Check label="Unique" checked={Boolean(field.unique)} onChange={(unique) => onChange({ ...field, unique })} /><Check label="Indexed" checked={Boolean(field.indexed)} onChange={(indexed) => onChange({ ...field, indexed })} /><Check label="Has default" checked={Boolean(field.hasDefault)} onChange={(hasDefault) => onChange({ ...field, hasDefault, ...(hasDefault ? { default: defaultFor(field.type, Boolean(field.nullable)) } : {}) })} /></div>{field.type === "string" && <label className="mt-2 block text-[10px] text-zinc-400">Maximum length<input aria-label={`Field ${field.id} maximum length`} type="number" min={1} max={1000000} value={field.maxLength ?? ""} onChange={(event) => onChange({ ...field, maxLength: event.target.value ? Number(event.target.value) : null })} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700" /></label>}{field.hasDefault && <label className="mt-2 block text-[10px] text-zinc-400">Default value<input aria-label={`Field ${field.id} default`} value={defaultText} onChange={(event) => setDefaultText(event.target.value)} onBlur={commitDefault} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 font-mono text-xs dark:border-zinc-700" /></label>}{field.type === "string" && <div className="mt-2 grid gap-2 sm:grid-cols-2"><label className="block text-[10px] text-zinc-400">Relation<select aria-label={`Field ${field.id} relation`} value={reference?.entityId ?? ""} onChange={(event) => onChange({ ...field, reference: event.target.value ? { entityId: event.target.value, onDelete: "restrict" } : null })} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700"><option value="">No relation</option>{entities.map((item) => <option key={item.id} value={item.id}>{item.id}.id</option>)}</select></label>{reference && <label className="block text-[10px] text-zinc-400">On delete<select aria-label={`Field ${field.id} on delete`} value={reference.onDelete} onChange={(event) => onChange({ ...field, nullable: event.target.value === "set-null" ? true : field.nullable, reference: { ...reference, onDelete: event.target.value as "restrict" | "cascade" | "set-null" } })} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700"><option value="restrict">restrict</option><option value="cascade">cascade</option><option value="set-null">set-null</option></select></label>}</div>}</article>;
}

function TextField({ label, value, placeholder, onChange }: { label: string; value: string; placeholder?: string; onChange: (value: string) => void }) { return <label className="block text-[10px] text-zinc-400">{label}<input aria-label={label} value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} className="mt-1 min-h-11 w-full rounded-lg border border-zinc-300 bg-transparent px-2 text-xs dark:border-zinc-700" /></label>; }
function Check({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) { return <label className="flex min-h-11 items-center gap-2 rounded-lg bg-white px-2 text-xs dark:bg-zinc-800"><input aria-label={label} type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="h-4 w-4" />{label}</label>; }
function formatDefault(value: unknown) { return typeof value === "string" ? value : JSON.stringify(value ?? null); }
function parseDefault(value: string, type: ApplicationEntityFieldType): unknown { if (type === "string" || type === "datetime") return value; if (type === "integer") { const parsed = Number(value); if (!Number.isSafeInteger(parsed)) throw new Error(); return parsed; } if (type === "number") { const parsed = Number(value); if (!Number.isFinite(parsed)) throw new Error(); return parsed; } if (type === "boolean") { if (!/^(true|false)$/.test(value)) throw new Error(); return value === "true"; } return JSON.parse(value); }
function defaultFor(type: ApplicationEntityFieldType, nullable: boolean): unknown { if (nullable) return null; if (type === "string") return ""; if (type === "integer" || type === "number") return 0; if (type === "boolean") return false; if (type === "datetime") return new Date().toISOString(); return {}; }
function snakeCase(value: string) { return value.replace(/([a-z0-9])([A-Z])/g, "$1_$2").replace(/[^A-Za-z0-9_]/g, "_").toLowerCase(); }
function kebabCase(value: string) { return value.replace(/([a-z0-9])([A-Z])/g, "$1-$2").replace(/[^A-Za-z0-9_-]/g, "-").toLowerCase(); }
function entityEditorError(entities: ApplicationEntity[]): string {
  if (entities.length > 100) return "Entityは100件以下にしてください。";
  const allIds = new Set(entities.map((item) => item.id));
  const ids = new Set<string>(); const tables = new Set<string>(); const paths = new Set<string>();
  for (const entity of entities) {
    if (!identifier.test(entity.id)) return `Entity ID '${entity.id}' は英字始まりのidentifierにしてください。`;
    if (ids.has(entity.id)) return `Entity ID '${entity.id}' が重複しています。`; ids.add(entity.id);
    const table = entity.tableName || snakeCase(entity.id); if (!/^[a-z][a-z0-9_]{0,127}$/.test(table) || tables.has(table)) return `SQLite table '${table}' が不正または重複しています。`; tables.add(table);
    if (!entity.fields.length) return `${entity.id}にはfieldが1件以上必要です。`;
    const fields = new Set<string>();
    for (const field of entity.fields) {
      if (!identifier.test(field.id) || ["id", "createdAt", "updatedAt"].includes(field.id) || fields.has(field.id)) return `${entity.id}.${field.id} は不正、予約済み、または重複しています。`; fields.add(field.id);
      if (field.maxLength != null && (field.type !== "string" || !Number.isInteger(field.maxLength) || field.maxLength < 1 || field.maxLength > 1000000)) return `${entity.id}.${field.id}のmaxLengthが不正です。`;
      if (field.reference && (!allIds.has(field.reference.entityId) || field.type !== "string" || (field.reference.onDelete === "set-null" && !field.nullable))) return `${entity.id}.${field.id}のrelationが不正です。`;
      if (field.hasDefault) { try { const normalized = parseDefault(formatDefault(field.default), field.type); if (field.type === "string" && field.maxLength && String(normalized).length > field.maxLength) return `${entity.id}.${field.id}のdefaultがmaxLengthを超えています。`; } catch { return `${entity.id}.${field.id}のdefault型が一致しません。`; } }
    }
    if (entity.crud?.enabled) { if (!entity.crud.operations.length) return `${entity.id}のCRUD operationを1件以上選択してください。`; const path = entity.crud.basePath || `/api/entities/${kebabCase(entity.id)}`; if (!/^\/api\/[A-Za-z][A-Za-z0-9_-]*(?:\/[A-Za-z][A-Za-z0-9_-]*)*$/.test(path) || paths.has(path)) return `CRUD path '${path}' が不正または重複しています。`; paths.add(path); }
  }
  return "";
}
