# LLM runtime生成契約 詳細設計

## 目的と現状差分

provider catalogの`list/load/unload/health`は共通化済みだが、生成はワークフローchatと永続chatが
Ollama JSON LinesおよびOpenAI互換SSEを個別に解析している。この重複によりthinking、エラー、
キャンセル、利用時刻更新の挙動が呼出元ごとにずれる。生成経路をruntime providerへ集約する。

## 境界

- `LlmRuntimeProvider`: `complete`、`stream_chat`、`cancel`、`get_capabilities`を提供する。
- `OllamaRuntimeProvider`: 管理中Ollama endpointだけをnative `/api/chat`へ変換し、thinkingと
  keep-aliveを扱う。構造化出力は互換性の高いOpenAI endpointを使う。
- `LlamaCppRuntimeProvider`: catalogのlocalhost portに一致するendpointを扱う。
- `OpenAICompatibleRuntimeProvider`: LM Studio等を含む外部OpenAI互換endpointのfallback。
- lifecycleのinstall/list/load/unload/healthは既存`provider_adapters`を正とし、生成契約に重複実装しない。

## 型付き契約

- 入力`RuntimeChatRequest`: base URL、model、messages、API key、temperature、max tokens、thinking、
  structured response、keep-alive。base URLは末尾`/v1`へ正規化する。
- stream出力`RuntimeChunk`: `content`、`thinking`、`usage`のいずれか。provider固有payloadをUIへ漏らさない。
- 各生成に推測困難なrequest IDを割り当てる。同一IDの同時使用は禁止する。
- `cancel(request_id)`はactive controlへ通知する。iteratorは通知を検知して`GenerationCancelled`を送出し、
  `httpx` stream contextを必ず閉じる。外側taskの`CancelledError`でも同じfinally cleanupを通る。
- active registryは生成終了・失敗・取消の全経路で削除し、完了後cancelはfalseを返す。

## エラー・安全性

- HTTP status、JSON/SSE破損、空choicesをprovider errorへ正規化する。応答本文やAPI keyはログ・例外へ含めない。
- GPU preflightとllama instance利用時刻更新はprovider呼出し直前に1回だけ行う。
- remote endpointへの任意URL許可範囲は既存LLM機能と同じとし、この変更で権限を拡大しない。
- provider catalogのcapabilityへ`chat/stream/cancel`を加え、管理操作capabilityとは分離する。

## 移行

1. 永続chat workerを共通streamへ移し、1秒checkpointとジョブイベントは維持する。
2. 非stream `_llm`を共通completeへ移し、JSON schemaの3段fallbackを維持する。
3. 旧WebSocket `/chat/stream`も共通streamを使う。切断時はrequest IDをcancelして上流接続を閉じる。
4. 永続chatの正式な取消は既存`/jobs/{id}/cancel`を利用し、job task cancelとprovider cancelを両方行う。

## 受け入れ条件

- Ollama JSONLとOpenAI SSEを同じcontent/thinking eventへ正規化する単体テスト。
- 明示cancelおよびtask cancel後にactive requestが0となり、完了後cancelがfalse。
- structured response fallback、HTTP errorで秘密値を露出しない。
- 永続chatの部分保存、ブラウザ切断後継続、job cancelの既存テストが通る。
- provider catalogが生成能力を正しく公開し、実機llama.cppでstream完了とcancelを確認する。
