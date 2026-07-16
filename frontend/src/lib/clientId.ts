/**
 * UUIDを生成する。認証トークン等の暗号用途には使用しない。
 *
 * iOS Safariの非secure HTTP contextではrandomUUID、環境によってはcrypto自体が
 * 利用できないため、利用可能なAPIを順番に選ぶ。
 */
export const createUuid = (): string => {
  if (
    typeof globalThis.crypto !== "undefined"
    && typeof globalThis.crypto.randomUUID === "function"
  ) {
    return globalThis.crypto.randomUUID();
  }

  if (
    typeof globalThis.crypto !== "undefined"
    && typeof globalThis.crypto.getRandomValues === "function"
  ) {
    const bytes = new Uint8Array(16);
    globalThis.crypto.getRandomValues(bytes);

    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;

    const hex = Array.from(bytes, (value) => value.toString(16).padStart(2, "0"));
    return [
      hex.slice(0, 4).join(""),
      hex.slice(4, 6).join(""),
      hex.slice(6, 8).join(""),
      hex.slice(8, 10).join(""),
      hex.slice(10, 16).join(""),
    ].join("-");
  }

  return `fallback-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
};
