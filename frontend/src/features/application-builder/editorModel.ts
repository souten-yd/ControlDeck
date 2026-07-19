import type { SemanticComponent } from "../../api/applicationBuilder";

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
