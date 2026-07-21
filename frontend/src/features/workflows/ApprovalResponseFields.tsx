import { useEffect, useMemo } from "react";
import { initialRuntimeValues, RuntimeField } from "./RuntimeComponents";
import type { TriggerInputDef } from "./nodeTypes";

export interface ApprovalFormSchema {
  type?: string;
  properties?: Record<string, {
    type?: string;
    title?: string;
    description?: string;
    enum?: unknown[];
    default?: unknown;
  }>;
  required?: string[];
  "x-control-deck-fields"?: TriggerInputDef[];
}

export function ApprovalResponseFields({
  schema, value, onChange,
  idPrefix = "interaction",
}: {
  schema?: ApprovalFormSchema;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  idPrefix?: string;
}) {
  const fields = useMemo<TriggerInputDef[]>(() => {
    const declared = schema?.["x-control-deck-fields"];
    if (Array.isArray(declared)) return declared.slice(0, 20);
    const required = new Set(schema?.required ?? []);
    return Object.entries(schema?.properties ?? {}).slice(0, 20).map(([key, field]) => ({
      key,
      label: field.title || key,
      description: field.description,
      required: required.has(key),
      default: field.default,
      type: field.enum ? "select" : field.type === "integer" ? "number" : field.type === "number" || field.type === "boolean" ? field.type : "text",
      options: field.enum?.map(String).join(","),
    } as TriggerInputDef));
  }, [schema]);
  useEffect(() => {
    if (fields.length === 0) return;
    const initial = initialRuntimeValues(fields);
    const missing = Object.keys(initial).some((key) => value[key] === undefined);
    if (missing) onChange({ ...initial, ...value });
  }, [fields, onChange, value]);
  if (fields.length === 0) return null;
  return (
    <fieldset className="mt-3 space-y-2 rounded-xl border border-amber-300/70 bg-white/70 p-2.5 dark:border-amber-700 dark:bg-zinc-900/80">
      <legend className="px-1 text-[10px] font-semibold text-amber-800 dark:text-amber-200">入力項目</legend>
      {fields.map((field) => <RuntimeField
        key={field.key}
        input={field}
        value={value[field.key]}
        onChange={(next) => onChange({ ...value, [field.key]: next })}
        idPrefix={idPrefix}
      />)}
    </fieldset>
  );
}

export function missingRequiredFormFields(schema: ApprovalFormSchema | undefined, value: Record<string, unknown>): string[] {
  const fields = schema?.["x-control-deck-fields"] ?? [];
  return fields.filter((field) => field.required && (
    value[field.key] === "" || value[field.key] == null ||
    (Array.isArray(value[field.key]) && (value[field.key] as unknown[]).length === 0)
  )).map((field) => field.label || field.key);
}
