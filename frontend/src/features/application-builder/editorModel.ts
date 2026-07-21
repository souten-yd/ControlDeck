import type { DesignTemplateDefinition, SemanticComponent } from "../../api/applicationBuilder";

export interface AppPage { id: string; title?: string; description?: string; root?: SemanticComponent | null; [key: string]: unknown }

export function pagesOf(spec: Record<string, unknown>): AppPage[] {
  return Array.isArray(spec.pages) ? spec.pages as AppPage[] : [];
}

export function findComponent(root: SemanticComponent | null | undefined, id: string): SemanticComponent | null {
  if (!root) return null;
  if (root.id === id) return root;
  for (const child of root.children ?? []) {
    const found = findComponent(child, id);
    if (found) return found;
  }
  return null;
}

export function updateComponent(root: SemanticComponent, id: string, update: (item: SemanticComponent) => SemanticComponent): SemanticComponent {
  if (root.id === id) return update(root);
  return { ...root, children: (root.children ?? []).map((child) => updateComponent(child, id, update)) };
}

export function removeComponent(root: SemanticComponent, id: string): SemanticComponent {
  return { ...root, children: (root.children ?? []).filter((child) => child.id !== id).map((child) => removeComponent(child, id)) };
}

export function parentOf(root: SemanticComponent, id: string): SemanticComponent | null {
  if ((root.children ?? []).some((child) => child.id === id)) return root;
  for (const child of root.children ?? []) {
    const found = parentOf(child, id);
    if (found) return found;
  }
  return null;
}

export function uniqueComponentId(root: SemanticComponent | null | undefined, type: string): string {
  const base = type.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "") || "component";
  let index = 1;
  while (findComponent(root, `${base}-${index}`)) index += 1;
  return `${base}-${index}`;
}

export function instantiateTemplate(root: SemanticComponent | null | undefined, template: DesignTemplateDefinition, values: Record<string, unknown>): SemanticComponent {
  const reserved = new Set<string>();
  const collect = (item: SemanticComponent | null | undefined) => {
    if (!item) return;
    reserved.add(item.id);
    for (const child of item.children ?? []) collect(child);
  };
  collect(root);
  const replacements = new Map<string, Map<string, unknown>>();
  for (const parameter of template.parameters) {
    const value = Object.prototype.hasOwnProperty.call(values, parameter.key) ? values[parameter.key] : parameter.default;
    for (const target of parameter.targets) {
      const properties = replacements.get(target.componentId) ?? new Map<string, unknown>();
      properties.set(target.property, value); replacements.set(target.componentId, properties);
    }
  }
  const instantiate = (item: SemanticComponent): SemanticComponent => {
    const base = item.type.replace(/[^a-z0-9]+/gi, "-").replace(/^-|-$/g, "") || "component";
    let index = 1;
    while (reserved.has(`${base}-${index}`)) index += 1;
    const id = `${base}-${index}`;
    reserved.add(id);
    const cloned = structuredClone(item);
    const replacement = replacements.get(item.id);
    return {
      ...cloned,
      id,
      ...(replacement ? { properties: { ...(cloned.properties ?? {}), ...Object.fromEntries(replacement) } } : {}),
      children: (item.children ?? []).map(instantiate),
    };
  };
  return instantiate(template.root);
}
