/** 目録の模擬の補助: 行（entries）だけの模擬の目録に、画面が読むモデルのまとめ（models）を足す。
 *  2026-11-28 に目録の画面を「1 モデル 1 枚のカード」に作り直した（model-add.spec.ts）。古い spec の 1 行の模擬は、
 *  行ごとに 1 枚のカード（id は "m-<行の id>"）にする。本番の目録の形は fixtures/model-catalog-20261128.json。 */
type Entry = Record<string, unknown> & { id: string; name: string };

export function withModels<T extends { runtimes: Array<{ entries: Entry[] }> }>(catalog: T): T {
  return {
    ...catalog,
    runtimes: catalog.runtimes.map((block) => ({
      ...block,
      models: block.entries.map((entry) => ({
        id: `m-${entry.id}`, name: entry.name, description: "", note_expert: "", uncensored: !!entry.uncensored,
        gated: false, kind: entry.kind ?? "model", recommended: false, recommended_variant: entry.id,
        size_min_gib: entry.size_gib ?? null, size_max_gib: entry.size_gib ?? null,
        variants: [{
          id: entry.id, label: entry.quantization || "標準", quantization: entry.quantization ?? "",
          acquire: entry.acquire, size_gib: entry.size_gib ?? null, memory_gib: entry.memory_gib ?? null,
          fit: entry.fit ?? "unknown", supported: entry.supported ?? true, reason: "", repo: entry.repo ?? "",
          format: entry.format ?? "gguf", kind: entry.kind ?? "model", family: entry.family ?? "", files: entry.files ?? [],
          state: "available", uncensored: !!entry.uncensored, note: "",
        }],
      })),
    })),
  };
}

/** カードを開いて主のボタン（取得する・登録する）を押す。 */
export async function openCardAndAct(card: import("@playwright/test").Locator) {
  const { expect } = await import("@playwright/test");
  if ((await card.locator("> button").getAttribute("aria-expanded")) !== "true") await card.locator("> button").click();
  const primary = card.getByTestId("catalog-primary");
  await expect(primary).toBeEnabled();
  await primary.click();
}
