---
name: controldeck-addon-development
description: Use when building, changing or reviewing a ControlDeck Add-on (control-deck-addon.json, external-service, embedded view, agent tool, workflow executor, Resource Broker, Host runtime API) or a web app / game meant to run inside ControlDeck. Triggers on "アドイン", "Add-on", "拡張機能を作って", "MediaForge のような", "ControlDeck で動く", "embedded view", "agent tool". Read the contract below before writing code, and verify in a real browser before calling it done.
license: MIT
metadata:
  author: ControlDeck
  version: "1.0.0"
---

# ControlDeck Add-on の開発

## この手順が要る理由

Add-on は ControlDeck とは別のプロセスで動き、ControlDeck は中身を import も実行もしない。
境界を守らない実装（Cookie を読む、host path を返す、ブラウザから GPU の lease を取る）は
ControlDeck に拒否されるか、動いても安全審査で戻される。書く前に契約を決め、書いたら実画面で確かめる。

## 書く前に決めること

| 項目 | 決め方 |
| --- | --- |
| 何を出すか | contribution の種類を選ぶ: navigation / embedded view / command / quick action / settings / workflow executor / agent tool / context action / setup checklist。これ以外は無い |
| 何を頼むか | `host_capabilities` は要るものだけ（context.read, theme.read, route.open, files.pick, files.export, projects.pick, jobs.read, jobs.write, notifications.show, resources.acquire）。多く頼むほど利用者の同意画面が重くなる |
| どこで動くか | `runtime.kind: "external-service"`、`base_url` は loopback HTTP か HTTPS。`health_path` を必ず持つ |
| GPU を使うか | 使うなら service 側から Resource Broker へ要求する（下記）。ブラウザの JavaScript から取らない |
| ファイル | host path を受け取らない・返さない。Host の picker が返す `grant:` ID と outputs API を使う |

## manifest（`control-deck-addon.json`）の骨格

```json
{
  "api_version": "2",
  "id": "my-addon",
  "name": "My Add-on",
  "version": "0.1.0",
  "description": "...",
  "publisher": "...",
  "requires": {"addon_contract": ">=2.0 <3.0"},
  "runtime": {"kind": "external-service", "base_url": "http://127.0.0.1:9160", "health_path": "/health"},
  "host_capabilities": ["theme.read", "jobs.write"],
  "contributions": {"navigation": [{"id": "main", "label": {"en": "My Add-on", "ja": "マイ拡張"}, "route": "/x/my-addon/main"}]}
}
```

検査は `./deck.sh ext lint ./control-deck-addon.json`（ControlDeck のリポジトリで）。lint が通っても利用可能ではない。
install → enable（capability の grant と health）を通った contribution だけが有効になる。

## 守る境界

- embedded view は opaque iframe（`Origin: null`）。API の fetch は `credentials: "include"` と、Bridge から受け取った nonce を
  `X-Control-Deck-Bridge-Session` に付ける。Cookie を読んだり保存したりしない。
- service が ControlDeck を呼ぶときは短命の service token（`Authorization: Bearer`、`X-Control-Deck-Addon-ID`）。
  受け取った token は `/api/v1/addon-runtime/token/introspect` で確かめる。HMAC の鍵は持たない。
- 長い処理は Host Job にする（`POST /api/v1/addon-runtime/{id}/jobs`）。PATCH は phase 必須、progress は増えるだけ、2Hz 以下。
  `.../control` を poll して、取り消されたら止める。
- GPU は `POST .../resources/requests` で頼み、lease を activate → renew（30 秒で切れる）→ release。
  動けるなら `vram.minimum_bytes` と `preferred_devices: ["gpu0", "host"]`（RAM でも動く）を申告する。
  ControlDeck は LLM を降ろして GPU を貸すことがある（Models の「Add-on への GPU の譲り方」）。生成が終わったら必ず release する。
- agent tool の入力 schema の description は、道具を使う AI が読む。何を渡すと何が返るかを具体的に書く。
- 秘密値（token、password、cookie）を URL・ログ・manifest・Job result に入れない。

## UI の決まり（ControlDeck の画面と同じ）

- 主な操作へ 2 手以内。全部のボタンを並べない（主操作 1 つ＋その他メニュー）。
- 320px（スマホ）と PC 幅の両方で使える。下部の安全領域（`env(safe-area-inset-bottom)`）を空ける。
- ライト・ダークの両方。テーマは `theme.read` で ControlDeck に合わせる。
- 待たせるときは理由と出口（取り消し）を出す。失敗は消さずに説明する。赤は破壊的な操作と重大なエラーだけ。
- 破壊的な操作だけ確認ダイアログ。完了はトースト。

## 開発の手元環境

- ControlDeck のリポジトリの `tools/fake-addon/`（`run.py`）が、契約どおりに動く最小の Add-on。迷ったらこれを真似る。
- 実 E2E の例は `frontend/e2e/addon-v2-execution.spec.ts`。

## 完成の判定

コードを書いただけでは完成ではない。

1. health が 200 を返し、lint が通る。
2. 開発サーバーを起こし、OpenCode の子エージェント **ui-verifier** に URL と主な流れを渡して実画面で確かめる
   （幅 320/390/768/1280、ダーク、処理中・失敗・空の状態）。結果は ControlDeck の OpenCode 画面「結果」に出る。
3. critical / major の指摘が残っていない。残すなら理由を利用者に伝える。
