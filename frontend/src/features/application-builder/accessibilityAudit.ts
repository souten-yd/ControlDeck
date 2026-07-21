import type { SemanticComponentCatalog } from "../../api/applicationBuilder";

export type AccessibilityAuditCategory = "contrast" | "focus" | "keyboard" | "touch";

export interface AccessibilityAuditIssue {
  code: string;
  category: AccessibilityAuditCategory;
  message: string;
  componentId: string;
}

export interface AccessibilityAuditResult {
  issues: AccessibilityAuditIssue[];
  checked: Record<AccessibilityAuditCategory, number>;
}

type Rgb = { red: number; green: number; blue: number; alpha: number };

export function auditApplicationPreview(root: HTMLElement, rules: SemanticComponentCatalog["accessibilityAudit"]): AccessibilityAuditResult {
  const issues: AccessibilityAuditIssue[] = [];
  const checked: AccessibilityAuditResult["checked"] = { contrast: 0, focus: 0, keyboard: 0, touch: 0 };
  const interactive = Array.from(root.querySelectorAll<HTMLElement>("[data-audit-interactive='true']"));
  const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;

  for (const element of interactive) {
    const componentId = componentName(element);
    checked.keyboard += 1;
    if (!isKeyboardReachable(element)) issues.push({ code: "A11Y_KEYBOARD_UNREACHABLE", category: "keyboard", componentId, message: "Keyboard focusで到達できません" });

    const rect = element.getBoundingClientRect();
    checked.touch += 1;
    if (rect.width + 0.5 < rules.minimumTouchTarget || rect.height + 0.5 < rules.minimumTouchTarget) issues.push({
      code: "A11Y_TOUCH_TARGET_SMALL", category: "touch", componentId,
      message: `${Math.round(rect.width)}×${Math.round(rect.height)}px · ${rules.minimumTouchTarget}px以上が必要です`,
    });

    if (isKeyboardReachable(element)) {
      checked.focus += 1;
      element.focus({ preventScroll: true });
      const style = getComputedStyle(element);
      const outlineWidth = Number.parseFloat(style.outlineWidth) || 0;
      const hasShadow = style.boxShadow !== "none" && style.boxShadow !== "";
      if (outlineWidth + 0.01 < rules.minimumFocusIndicator && !hasShadow) issues.push({
        code: "A11Y_FOCUS_INDICATOR_MISSING", category: "focus", componentId,
        message: `${rules.minimumFocusIndicator}px以上のfocus indicatorが必要です`,
      });
    }
  }
  previousFocus?.focus({ preventScroll: true });

  for (const element of root.querySelectorAll<HTMLElement>("[data-audit-contrast='true']")) {
    const foreground = parseColor(getComputedStyle(element).color);
    const background = opaqueBackground(element);
    if (!foreground || !background) continue;
    checked.contrast += 1;
    const opacity = inheritedOpacity(element);
    const rendered = blend({ ...foreground, alpha: foreground.alpha * opacity }, background);
    const ratio = contrastRatio(rendered, background);
    const style = getComputedStyle(element);
    const fontSize = Number.parseFloat(style.fontSize) || 0;
    const fontWeight = Number.parseInt(style.fontWeight, 10) || 400;
    const threshold = fontSize >= 24 || (fontSize >= 18.66 && fontWeight >= 700) ? rules.minimumLargeTextContrast : rules.minimumContrast;
    if (ratio + 0.01 < threshold) issues.push({
      code: "A11Y_CONTRAST_LOW", category: "contrast", componentId: componentName(element),
      message: `${ratio.toFixed(2)}:1 · ${threshold.toFixed(1)}:1以上が必要です`,
    });
  }
  return { issues, checked };
}

function isKeyboardReachable(element: HTMLElement): boolean {
  if (element.matches(":disabled,[aria-disabled='true']")) return false;
  return element.tabIndex >= 0;
}

function componentName(element: HTMLElement): string {
  return element.closest<HTMLElement>("[data-component-id]")?.dataset.componentId ?? "application";
}

function inheritedOpacity(element: HTMLElement): number {
  let opacity = 1; let current: HTMLElement | null = element;
  while (current) { opacity *= Number.parseFloat(getComputedStyle(current).opacity) || 1; current = current.parentElement; }
  return opacity;
}

function opaqueBackground(element: HTMLElement): Rgb | null {
  let current: HTMLElement | null = element;
  while (current) {
    const color = parseColor(getComputedStyle(current).backgroundColor);
    if (color && color.alpha >= 0.99) return color;
    current = current.parentElement;
  }
  return { red: 255, green: 255, blue: 255, alpha: 1 };
}

function parseColor(value: string): Rgb | null {
  const match = value.match(/^rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)(?:\s*[,/]\s*([\d.]+))?\s*\)$/i);
  if (!match) return null;
  return { red: Number(match[1]), green: Number(match[2]), blue: Number(match[3]), alpha: match[4] === undefined ? 1 : Number(match[4]) };
}

function blend(foreground: Rgb, background: Rgb): Rgb {
  const alpha = Math.max(0, Math.min(1, foreground.alpha));
  return { red: foreground.red * alpha + background.red * (1 - alpha), green: foreground.green * alpha + background.green * (1 - alpha), blue: foreground.blue * alpha + background.blue * (1 - alpha), alpha: 1 };
}

function contrastRatio(first: Rgb, second: Rgb): number {
  const a = luminance(first); const b = luminance(second);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

function luminance(color: Rgb): number {
  const channel = (value: number) => { const normalized = value / 255; return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * channel(color.red) + 0.7152 * channel(color.green) + 0.0722 * channel(color.blue);
}
