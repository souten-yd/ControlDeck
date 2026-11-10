import { api } from "./client";
import type { UserInfo } from "../types";

export function passkeysAvailable(): boolean {
  return window.isSecureContext && typeof PublicKeyCredential !== "undefined";
}
function decode(value: string): ArrayBuffer {
  const text = value.replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(text), (c) => c.charCodeAt(0)).buffer;
}
function encode(value: ArrayBuffer): string {
  let text = "";
  for (const byte of new Uint8Array(value)) text += String.fromCharCode(byte);
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function serialize(credential: PublicKeyCredential) {
  const response = credential.response;
  const common = { id: credential.id, rawId: encode(credential.rawId), type: credential.type,
    clientExtensionResults: credential.getClientExtensionResults() };
  if (response instanceof AuthenticatorAttestationResponse) {
    return { ...common, response: { clientDataJSON: encode(response.clientDataJSON),
      attestationObject: encode(response.attestationObject), transports: response.getTransports?.() ?? [] } };
  }
  const assertion = response as AuthenticatorAssertionResponse;
  return { ...common, response: { clientDataJSON: encode(assertion.clientDataJSON),
    authenticatorData: encode(assertion.authenticatorData), signature: encode(assertion.signature),
    userHandle: assertion.userHandle ? encode(assertion.userHandle) : null } };
}
type DescriptorJSON = Omit<PublicKeyCredentialDescriptor, "id"> & { id: string };
type CreationJSON = Omit<PublicKeyCredentialCreationOptions, "challenge" | "user" | "excludeCredentials"> & {
  challenge: string; user: Omit<PublicKeyCredentialUserEntity, "id"> & { id: string };
  excludeCredentials?: DescriptorJSON[];
};
type RequestJSON = Omit<PublicKeyCredentialRequestOptions, "challenge" | "allowCredentials"> & {
  challenge: string; allowCredentials?: DescriptorJSON[];
};

export async function registerPasskey(name: string, password: string, totpCode: string): Promise<void> {
  const options = await api<CreationJSON>("/auth/passkeys/register/options", {
    method: "POST", json: { name, password, totp_code: totpCode || undefined },
  });
  const credential = await navigator.credentials.create({ publicKey: { ...options,
    challenge: decode(options.challenge), user: { ...options.user, id: decode(options.user.id) },
    excludeCredentials: options.excludeCredentials?.map((item) => ({ ...item, id: decode(item.id) })),
  } }) as PublicKeyCredential | null;
  if (!credential) throw new Error("パスキーの登録がキャンセルされました");
  await api("/auth/passkeys/register/verify", { method: "POST", json: { credential: serialize(credential) } });
}
export async function loginWithPasskey(): Promise<UserInfo> {
  const options = await api<RequestJSON>("/auth/login/passkey/options", { method: "POST" });
  const credential = await navigator.credentials.get({ publicKey: { ...options,
    challenge: decode(options.challenge),
    allowCredentials: options.allowCredentials?.map((item) => ({ ...item, id: decode(item.id) })),
  } }) as PublicKeyCredential | null;
  if (!credential) throw new Error("パスキーの認証がキャンセルされました");
  return api<UserInfo>("/auth/login/passkey/verify", { method: "POST", json: { credential: serialize(credential) } });
}
export function passkeyError(error: unknown): string {
  if (error instanceof DOMException && error.name === "NotAllowedError") {
    return "認証がキャンセルされたか、利用できるパスキーがありません。登録した端末・接続先を確認してください";
  }
  return error instanceof Error ? error.message : "パスキーを利用できませんでした";
}
const rememberedUserKey = "controldeck.login.username";
export function rememberedUsername(): string {
  try { return localStorage.getItem(rememberedUserKey) ?? ""; } catch { return ""; }
}
export function rememberUsername(username: string): void {
  try { localStorage.setItem(rememberedUserKey, username); } catch { /* Private storage can be unavailable. */ }
}
