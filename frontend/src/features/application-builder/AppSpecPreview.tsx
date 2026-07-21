import { useMemo, type CSSProperties, type MouseEvent, type Ref } from "react";
import type {
  ComponentDefinition,
  SemanticComponent,
  SemanticComponentCatalog,
} from "../../api/applicationBuilder";
import { pagesOf } from "./editorModel";

export type AppPreviewViewport = "mobile" | "tablet" | "desktop";
export type AppPreviewState =
  SemanticComponentCatalog["previewStates"][number]["id"];

export function AppSpecPreview({
  spec,
  catalog,
  viewport,
  previewState = "default",
  selectedId = null,
  onSelect,
  testId,
  label = "Application",
  compact = false,
  containerRef,
}: {
  spec: Record<string, unknown>;
  catalog: SemanticComponentCatalog;
  viewport: AppPreviewViewport;
  previewState?: AppPreviewState;
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  testId?: string;
  label?: string;
  compact?: boolean;
  containerRef?: Ref<HTMLElement>;
}) {
  const page = pagesOf(spec)[0];
  const definitions = useMemo(
    () => new Map(catalog.components.map((item) => [item.type, item])),
    [catalog.components],
  );
  const theme =
    spec.theme && typeof spec.theme === "object" && !Array.isArray(spec.theme)
      ? (spec.theme as Record<string, unknown>)
      : {};
  const tokens =
    theme.tokens &&
    typeof theme.tokens === "object" &&
    !Array.isArray(theme.tokens)
      ? (theme.tokens as Record<string, unknown>)
      : {};
  const widthClass =
    viewport === "mobile"
      ? "max-w-[320px]"
      : viewport === "tablet"
        ? "max-w-[768px]"
        : "max-w-[1100px]";
  return (
    <section
      ref={containerRef}
      aria-label={`${label} ${viewport} preview`}
      data-testid={testId}
      data-preview-state={previewState}
      aria-busy={previewState === "loading"}
      style={previewThemeStyle(tokens)}
      className={`mx-auto w-full overflow-hidden border border-zinc-300 shadow-sm dark:border-zinc-700 ${compact ? "min-h-44" : "min-h-96"} ${widthClass}`}
    >
      <div className="flex items-center gap-2 border-b border-current/10 px-3 py-2">
        <strong
          data-audit-contrast="true"
          className="min-w-0 flex-1 truncate text-xs"
        >
          {page?.title || page?.id || "No page"}
        </strong>
        <span className="rounded-full border border-current/15 px-2 py-1 text-[9px]">
          {
            catalog.previewStates.find((item) => item.id === previewState)
              ?.label
          }
        </span>
      </div>
      <PreviewStateBanner state={previewState} />
      <div
        style={{ padding: "var(--app-space)" }}
        className={
          previewState === "disabled" ? "pointer-events-none opacity-50" : ""
        }
      >
        {page?.root ? (
          <PreviewNode
            item={page.root}
            definitions={definitions}
            selectedId={selectedId}
            onSelect={onSelect}
            previewState={previewState}
            viewport={viewport}
          />
        ) : (
          <p className="p-4 text-center text-xs opacity-50">
            Page canvas is not initialized.
          </p>
        )}
      </div>
    </section>
  );
}

function PreviewNode({
  item,
  definitions,
  selectedId,
  onSelect,
  previewState,
  viewport,
}: {
  item: SemanticComponent;
  definitions: Map<string, ComponentDefinition>;
  selectedId: string | null;
  onSelect?: (id: string) => void;
  previewState: AppPreviewState;
  viewport: AppPreviewViewport;
}) {
  const selected = item.id === selectedId;
  const props = item.properties ?? {};
  const children = item.children ?? [];
  const shell = `relative rounded-lg ${selected ? "ring-2 ring-accent-500" : ""}`;
  const select = (event: MouseEvent) => {
    if (!onSelect) return;
    event.stopPropagation();
    onSelect(item.id);
  };
  if (definitions.get(item.type)?.container) {
    const responsiveColumns =
      props.columns &&
      typeof props.columns === "object" &&
      !Array.isArray(props.columns)
        ? (props.columns as Record<string, unknown>)
        : {};
    const columnCount = Math.max(
      1,
      Math.min(
        12,
        Number(
          responsiveColumns[viewport] ??
            (viewport === "mobile" ? 1 : viewport === "tablet" ? 2 : 3),
        ),
      ),
    );
    return (
      <div
        data-component-id={item.id}
        onClick={onSelect ? select : undefined}
        style={{
          borderRadius: "var(--app-radius)",
          gap: "var(--app-space)",
          ...(item.type === "layout.card"
            ? { padding: "var(--app-space)" }
            : {}),
          ...(item.type === "layout.grid"
            ? { gridTemplateColumns: `repeat(${columnCount}, minmax(0, 1fr))` }
            : {}),
        }}
        className={`${shell} ${item.type === "layout.row" ? "flex flex-wrap" : item.type === "layout.grid" ? "grid" : item.type === "layout.card" ? "border border-current/15" : "flex flex-col"}`}
      >
        {children.length ? (
          children.map((child) => (
            <PreviewNode
              key={child.id}
              item={child}
              definitions={definitions}
              selectedId={selectedId}
              onSelect={onSelect}
              previewState={previewState}
              viewport={viewport}
            />
          ))
        ) : (
          <div
            style={{ borderRadius: "var(--app-radius)" }}
            className="border border-dashed border-current/20 p-4 text-center text-[10px] opacity-50"
          >
            Drop components here
          </div>
        )}
      </div>
    );
  }
  if (previewState === "loading")
    return (
      <div
        role="status"
        aria-label={`Loading ${item.id}`}
        style={{ borderRadius: "var(--app-radius)" }}
        className={`${shell} h-11 animate-pulse bg-current/10`}
      />
    );
  const tableColumns = Array.isArray(props.columns)
    ? props.columns.filter(
        (column): column is Record<string, unknown> =>
          Boolean(column) &&
          typeof column === "object" &&
          !Array.isArray(column),
      )
    : [];
  const chartSeries = Array.isArray(props.series)
    ? props.series.filter(
        (series): series is Record<string, unknown> =>
          Boolean(series) &&
          typeof series === "object" &&
          !Array.isArray(series),
      )
    : [];
  const eventInteractive = Boolean(
    item.events && Object.keys(item.events).length,
  );
  const focusClass =
    "focus:outline focus:outline-2 focus:outline-offset-2 focus:outline-accent-500";
  const content =
    item.type === "input.text" ? (
      <label className="block text-xs">
        <span data-audit-contrast="true">{String(props.label ?? "Input")}</span>
        <input
          data-audit-interactive="true"
          readOnly
          disabled={previewState === "disabled"}
          style={{ borderRadius: "var(--app-radius)" }}
          placeholder={String(props.placeholder ?? "")}
          className={`mt-1 min-h-11 w-full border border-current/20 bg-transparent px-3 ${focusClass}`}
        />
      </label>
    ) : item.type === "action.workflow-run" ? (
      <WorkflowContractPreview
        properties={props}
        previewState={previewState}
        focusClass={focusClass}
      />
    ) : item.type === "display.metric" ? (
      <div
        style={{
          borderRadius: "var(--app-radius)",
          backgroundColor:
            "color-mix(in srgb, var(--app-accent) 8%, transparent)",
        }}
        className="p-3"
      >
        <span data-audit-contrast="true" className="text-xs opacity-60">
          {String(props.label ?? "Metric")}
        </span>
        <strong data-audit-contrast="true" className="block text-2xl">
          {previewState === "empty" ? "—" : String(props.value ?? 0)}
        </strong>
      </div>
    ) : item.type === "data.table" ? (
      <div
        role={eventInteractive ? "grid" : undefined}
        tabIndex={eventInteractive ? 0 : undefined}
        data-audit-interactive={eventInteractive ? "true" : undefined}
        aria-label={String(props.label ?? "Data table")}
        style={{ borderRadius: "var(--app-radius)" }}
        className={`min-h-11 overflow-hidden border border-current/15 text-xs ${eventInteractive ? focusClass : ""}`}
      >
        <div className="flex min-h-11 items-center gap-2 px-3 py-2">
          <span data-audit-contrast="true" className="mr-auto font-medium">
            {String(props.label ?? "Data table")}
          </span>
          {Boolean(props.enableCreate) && (
            <button
              type="button"
              disabled={previewState !== "default"}
              className={`min-h-11 rounded-lg bg-accent-600 px-3 text-white ${focusClass}`}
            >
              Add item
            </button>
          )}
        </div>
        {previewState === "empty" ? (
          <p className="border-t border-current/10 p-3 opacity-60">No rows</p>
        ) : tableColumns.length ? (
          <div
            className="grid border-t border-current/10"
            style={{
              gridTemplateColumns: `repeat(${tableColumns.length + (props.enableUpdate || props.enableDelete ? 1 : 0)}, minmax(0, 1fr))`,
            }}
          >
            {tableColumns.map((column, index) => (
              <span
                key={`${String(column.key)}-${index}`}
                className="truncate border-r border-current/10 px-2 py-2 last:border-r-0"
              >
                {String(column.label ?? column.key)}
              </span>
            ))}
            {Boolean(props.enableUpdate || props.enableDelete) && (
              <span className="px-2 py-2">Actions</span>
            )}
          </div>
        ) : (
          <p className="border-t border-current/10 p-3 opacity-60">
            Data Table
          </p>
        )}
      </div>
    ) : item.type === "chart.line" ? (
      <div
        role="img"
        tabIndex={eventInteractive ? 0 : undefined}
        data-audit-interactive={eventInteractive ? "true" : undefined}
        aria-label={String(props.label ?? "Line chart")}
        style={{
          borderRadius: "var(--app-radius)",
          backgroundColor:
            "color-mix(in srgb, var(--app-accent) 8%, transparent)",
        }}
        className={`flex min-h-11 h-28 items-end justify-center gap-3 p-3 text-xs opacity-70 ${eventInteractive ? focusClass : ""}`}
      >
        {previewState === "empty"
          ? "No series"
          : chartSeries.length
            ? chartSeries.map((series, index) => (
                <span
                  data-audit-contrast="true"
                  key={`${String(series.key)}-${index}`}
                  className="rounded-full border border-current/20 px-2 py-1"
                >
                  {String(series.label ?? series.key)}
                </span>
              ))
            : "Line Chart"}
      </div>
    ) : (
      <p data-audit-contrast="true" className="text-sm">
        {String(props.text ?? props.value ?? item.type)}
      </p>
    );
  return (
    <div
      data-component-id={item.id}
      onClick={onSelect ? select : undefined}
      style={{ borderRadius: "var(--app-radius)" }}
      className={`${shell} p-1`}
    >
      {content}
    </div>
  );
}

function WorkflowContractPreview({
  properties,
  previewState,
  focusClass,
}: {
  properties: Record<string, unknown>;
  previewState: AppPreviewState;
  focusClass: string;
}) {
  const inputs = Array.isArray(properties.contractInputs)
    ? properties.contractInputs.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item) && typeof item === "object" && !Array.isArray(item),
      )
    : [];
  const outputs = Array.isArray(properties.contractOutputs)
    ? properties.contractOutputs.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item) && typeof item === "object" && !Array.isArray(item),
      )
    : [];
  return (
    <div className="space-y-3">
      {inputs.map((field) => {
        const name = String(field.name ?? "input");
        const label = String(field.label ?? name);
        const control = String(field.control ?? "json");
        const controlClass = `mt-1 min-h-11 w-full rounded-lg border border-current/20 bg-transparent px-3 ${focusClass}`;
        return (
          <label key={name} className="block text-xs">
            <span data-audit-contrast="true">{label}</span>
            {control === "boolean" || control === "select" ? (
              <select aria-label={label} disabled className={controlClass}>
                <option>{control === "boolean" ? "No / Yes" : "Select…"}</option>
              </select>
            ) : control === "json" ? (
              <textarea aria-label={label} disabled rows={3} className={`${controlClass} py-2`} />
            ) : (
              <input aria-label={label} disabled type={control === "number" ? "number" : "text"} className={controlClass} />
            )}
          </label>
        );
      })}
      <button
        data-audit-interactive="true"
        data-audit-contrast="true"
        disabled={previewState !== "default"}
        style={{ borderRadius: "var(--app-radius)", backgroundColor: "var(--app-accent)" }}
        className={`min-h-11 w-full px-4 text-xs font-semibold text-white disabled:opacity-50 ${focusClass}`}
      >
        {String(properties.label ?? "Run")}
      </button>
      <section className="rounded-lg border border-dashed border-current/20 p-3 text-xs opacity-60">
        <strong className="block">{String(properties.resultLabel ?? "Result")}</strong>
        <span>{outputs.length ? outputs.map((item) => String(item.label ?? item.name)).join(" · ") : "Workflow output"}</span>
      </section>
    </div>
  );
}

function PreviewStateBanner({ state }: { state: AppPreviewState }) {
  if (state === "loading")
    return (
      <p
        role="status"
        className="mx-3 mt-3 rounded-lg bg-current/5 px-3 py-2 text-xs"
      >
        Loading preview…
      </p>
    );
  if (state === "empty")
    return (
      <p
        role="status"
        className="mx-3 mt-3 rounded-lg border border-dashed border-current/20 px-3 py-2 text-xs"
      >
        Empty dataset preview
      </p>
    );
  if (state === "error")
    return (
      <p
        role="alert"
        className="mx-3 mt-3 rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-700"
      >
        Preview error · Retry or review the binding.
      </p>
    );
  if (state === "disabled")
    return (
      <p
        role="status"
        className="mx-3 mt-3 rounded-lg bg-current/5 px-3 py-2 text-xs"
      >
        Disabled preview
      </p>
    );
  return null;
}

function previewThemeStyle(tokens: Record<string, unknown>): CSSProperties {
  const accents: Record<string, string> = {
    blue: "#1d4ed8",
    violet: "#6d28d9",
    emerald: "#047857",
    amber: "#b45309",
  };
  const spaces: Record<string, string> = {
    xs: "0.25rem",
    sm: "0.5rem",
    md: "0.75rem",
    lg: "1rem",
    xl: "1.5rem",
  };
  const radii: Record<string, string> = {
    none: "0",
    sm: "0.375rem",
    md: "0.625rem",
    lg: "1rem",
  };
  const surface = String(tokens.surface ?? "layered");
  const color = String(tokens.color ?? "slate");
  const highContrast = tokens.text === "high-contrast";
  const darkSurface = color === "neutral" && highContrast;
  return {
    "--app-accent": accents[String(tokens.accent)] ?? accents.blue,
    "--app-space": spaces[String(tokens.spacing)] ?? spaces.md,
    "--app-radius": radii[String(tokens.radius)] ?? radii.md,
    backgroundColor: darkSurface
      ? "#09090b"
      : surface === "elevated"
        ? "#f8fafc"
        : "#ffffff",
    color: darkSurface ? "#fafafa" : "#18181b",
    fontFamily:
      tokens.typography === "mono"
        ? "ui-monospace, SFMono-Regular, Menlo, monospace"
        : "ui-sans-serif, system-ui, sans-serif",
    boxShadow:
      tokens.shadow === "strong"
        ? "0 20px 40px rgb(15 23 42 / 0.18)"
        : tokens.shadow === "medium"
          ? "0 12px 24px rgb(15 23 42 / 0.12)"
          : tokens.shadow === "subtle"
            ? "0 4px 12px rgb(15 23 42 / 0.08)"
            : "none",
    borderRadius: radii[String(tokens.radius)] ?? radii.md,
  } as CSSProperties;
}
