import { expect, test } from "@playwright/test";
import { createUuid } from "../src/lib/clientId";

const CLIENT_INSTANCE_RE = /^[A-Za-z0-9_-]{16,80}$/;

const withCrypto = (cryptoValue: Crypto | undefined, run: () => void): void => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  Object.defineProperty(globalThis, "crypto", {
    configurable: true,
    value: cryptoValue,
  });
  try {
    run();
  } finally {
    if (descriptor) Object.defineProperty(globalThis, "crypto", descriptor);
    else Reflect.deleteProperty(globalThis, "crypto");
  }
};

test("uses randomUUID when available", () => {
  let calls = 0;
  withCrypto({
    randomUUID: () => {
      calls += 1;
      return "12345678-1234-4abc-8def-1234567890ab";
    },
  } as Crypto, () => {
    expect(createUuid()).toBe("12345678-1234-4abc-8def-1234567890ab");
  });
  expect(calls).toBe(1);
});

test("uses getRandomValues when randomUUID is unavailable", () => {
  withCrypto({
    getRandomValues: (array: Uint8Array) => {
      array.forEach((_, index) => { array[index] = index; });
      return array;
    },
  } as unknown as Crypto, () => {
    const id = createUuid();
    expect(id).toBe("00010203-0405-4607-8809-0a0b0c0d0e0f");
    expect(id).toMatch(/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
  });
});

test("works without crypto and satisfies the backend contract", () => {
  withCrypto(undefined, () => {
    const id = createUuid();
    expect(id).toMatch(CLIENT_INSTANCE_RE);
    expect(id).toMatch(/^fallback-[a-z0-9]+-[a-z0-9]+$/);
  });
});

test("generates 1000 unique identifiers without crypto", () => {
  withCrypto(undefined, () => {
    const ids = Array.from({ length: 1_000 }, () => createUuid());
    expect(new Set(ids).size).toBe(ids.length);
  });
});
