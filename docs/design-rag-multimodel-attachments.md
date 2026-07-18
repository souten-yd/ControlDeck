# 設計: RAG強化（Embed/Reranker）・Model管理再編・VLM添付・会話別コレクション

2026-07-18 の要求バッチの要件定義と詳細設計。実装は段階的に PR 分割する。

## 1. 要件整理

| # | 要件 | 優先度 |
|---|---|---|
| R1 | BGE-M3（FP16・GPU常駐オプション）を埋め込みモデルとして導入 | 高 |
| R2 | Qwen3-Reranker-4B（Q4_K_M）で RAG 検索結果を再ランク | 高 |
| R3 | RAG API 呼び出し時に自動ロード、アイドル時自動アンロード | 高 |
| R4 | Embed/Reranker ロード時に ASR モデルをアンロードして VRAM 確保 | 高 |
| R5 | Model 画面をタブ再編: LLM+VLM / Embed・Reranker / TTS | 高 |
| R6 | VLM 有効化を Ollama/llama.cpp のモデル個別設定へ組込み | 中 |
| R7 | チャットに 📎 添付（画像→VLM、コード/文書/PDF→RAG） | 高 |
| R8 | 会話別 RAG コレクション（添付・検索資料・Deep Research レポートを会話名で登録・再利用） | 高 |
| R9 | SearXNG を完全サーバー管理化（ユーザー操作を不要に） | 中 |
| R10 | Web 検索エンジンは SearXNG を既定に（問題なければ将来固定化） | 中 |
| R11 | 参照文献一覧は既定最小化・タップ開閉 | 中 |
| R12 | 機能選択判断（plan）は結果のみ表示・タップで理由 | 中 |
| R13 | 新規会話 ➕ ボタン（ゴミ箱と履歴選択の間） | 中 |
| R14 | 操作メニュー 2 列化・OpenCode アイコン変更 | 中 |
| R15 | ワークフロー FAB のモバイルナビ被り修正・ヘッダー✨削除 | 中 |
| R16 | アプリ内 Web ビュー（FrameDeck :9000）黒画面バグ修正 | 高 |

## 2. モデルサービング設計（R1-R4）

### 方式
llama.cpp の既存マルチインスタンス基盤（catalog + systemd unit + ondemand 起動）を再利用し、
**役割付きインスタンス**として管理する。新規サービング機構は作らない。

- instance に `role: "llm" | "embedding" | "reranker"` を追加（既定 "llm"）。
- embedding instance: `llama-server --embedding --pooling mean` で起動（BGE-M3 GGUF F16）。
  OpenAI 互換 `/v1/embeddings` を提供 → 既存 rag.build/query の
  OpenAI 互換 embeddings クライアントがそのまま使える。
- reranker instance: `llama-server --rerank`（Qwen3-Reranker-4B Q4_K_M）。
  `/v1/rerank`（Jina 互換）を提供。
- モデル取得: HF から GGUF を既存の pull/register 経路で取得し、instance 登録時に
  role を指定。推奨モデルはプリセットとして UI に表示（ワンタップ導入）。

### 自動ロード/アンロード（R3）
- rag.build / rag.query / chat の検索前処理から `ensure_role_ready("embedding")` /
  `ensure_role_ready("reranker")` を呼ぶ（llama.cpp の LlamaCppRuntimeProvider._prepare と
  同じ ondemand 起動パターン。health 200 まで待機）。
- アイドルアンロードは既存 idle_unload_loop の対象に role instance も含める
  （last_used_at は embeddings/rerank 呼び出しで更新）。
- 「GPU 常駐」オプション = instance の `idle_exclude: true`（既存機構）。

### VRAM 確保（R4）
- role instance の起動直前に ASR（faster-whisper 等）モデルをアンロードする hook を追加。
  既存 ASR 管理（chat_asr）に `unload()` を追加し、ensure_role_ready から呼ぶ。
- 逆方向（ASR 使用時に embed を落とす）は行わない（音声入力は短時間・CPU fallback 可のため）。

### rerank 段（R2）
- rag.query: 既存コサイン top_k*4 を候補とし、reranker が有効なら `/v1/rerank` で
  上位 top_k を選択。reranker 未導入/停止時は従来動作（graceful degradation）。

## 3. Model 画面タブ再編（R5, R6）

```
[ LLM / VLM ] [ Embed / Reranker ] [ TTS ]   ← セグメントコントロール
```
- **LLM/VLM**: 従来のモデル一覧。モデル個別設定に「VLM（画像入力）を有効化」トグルを追加
  （Ollama: capabilities に vision があるモデルのみ表示 / llama.cpp: mmproj パス設定）。
- **Embed/Reranker**: 推奨プリセット（BGE-M3 F16 / Qwen3-Reranker-4B Q4_K_M）の
  導入ボタン + 状態（未導入/停止/稼働）+ 常駐トグル（idle_exclude）+ アンロードボタン。
- **TTS**: 既存 TTS 設定があれば移設、なければ導入プレースホルダ。
- URL クエリ `?tab=` でタブ直リンク可能に。

## 4. 添付と会話別コレクション（R7, R8）

- 会話コレクション名: `chat-<conversation_id>`（表示名は会話タイトル）。
  Knowledge 画面では「会話コレクション」グループとして区別表示。
- 📎 ボタン: 入力欄左端（マイクの左）。タップでファイル選択（画像/コード/文書/PDF）。
  - 画像: VLM 有効モデル選択中なら base64 で chat メッセージに添付（Ollama native images /
    OpenAI 互換 image_url）。VLM 無効なら案内表示。
  - テキスト系/PDF: サーバーへアップロード → 既存 rag.build 経路で会話コレクションへ登録 →
    以後の質問で rag.query が会話コレクションを自動参照。
- Web 検索の参考資料・Deep Research レポートも生成完了時に会話コレクションへ登録。

## 5. その他（R9-R16）

- SearXNG: ManagedApplication としての手動操作 UI を撤去し、lifespan + ondemand 起動の
  現行サーバー管理のみとする（アプリ一覧から非表示 or 読み取り専用バッジ）。
- 検索エンジン: 既定値を searxng に変更（選択 UI は当面残し、安定後に固定化）。
- FrameDeck 黒画面: アプリ内 Web ビューのプロキシ/iframe 経路を調査（別ブラウザでは正常
  なため、ビューア側の CSP/サンドボックス/プロキシ書換えを疑う）。

## 6. PR 分割計画

1. docs（本書）
2. 小粒 UI バッチ（R11-R15 + 検索既定 SearXNG）
3. SearXNG サーバー管理化（R9）
4. llama.cpp role instance + ensure_role_ready + idle 統合（R1-R4 基盤）
5. rag.query rerank 段 + Embed/Reranker タブ（R2, R5）
6. VLM 有効化 + 📎 画像添付（R6, R7 画像）
7. 文書添付 + 会話別コレクション（R7 文書, R8）
8. FrameDeck 黒画面修正（R16）
