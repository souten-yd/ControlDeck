# Models 共通 VRAM マージン

Models → 共通設定 → GPU メモリで、GPU 上に残す余裕を GiB 単位で保存する。
既定は 3 GiB、範囲は 0〜256 GiB。RAM の余裕とは別の設定。
保存は実行中の推論を停止せず、次回のモデル起動から適用する。

| ランタイム | 適用先 |
|---|---|
| Strata Qwen / Unsloth | `--set vram-reserve-mib=<MiB>` |
| Strata DeepSeek | `--set reserve-mib=<MiB>` |
| Maya GLM / Qwen | `--vram-reserve-mib <MiB>` |
| Maya DeepSeek | `--reserve-mib <MiB>` |
| Maya MiniMax | `--reserve-gb <GiB>` |
| Radiance | YAML `memory.gpu-headroom-mib` |
| llama.cpp ROCm / Vulkan | `--fit on --fit-target <MiB>`、既定のGPU層数999は `auto` へ変換 |
| 外部 Ollama | サービス側で `OLLAMA_GPU_OVERHEAD=<bytes>` の設定が必要。画面で共通値から換算した値を表示する |

個別モデルに共通値より大きい予約があれば、大きい方を採用する。
GiB→MiB変換は切り上げ。Mayaは元の設定ファイル名と思考設定のサイドカーを維持し、変更前の予約値を
`control_deck_vram_margin` に記録するため、共通マージンを小さく戻せる。
ネイティブ画面で予約値が変更された場合は、その新しい値を個別値として採用する。
llama.cpp はマージン保存時に永続systemd unitも同期する（再起動・enable状態変更なし）。
固定のGPU層数と自動フィッティングは両立しないため、GPU層数0（CPU専用）以外は
自動（999/-1）が必要。固定層数を維持する場合は共通マージンを0にする。
`--fit-target`非対応の古いバイナリでは、未適用のまま起動せず更新を案内する。

OllamaはControlDeck管理のプロセスではなく、外部URLやシステムサービスを利用できる。
そのサービスの環境変数変更・再起動は、この共通設定からは行わない。
VRAM予約は各エンジンの配分目標であり、他アプリの後からの確保や一時バッファによって
実際の空き容量は変わる。RAM不足への対策は別に必要。

## 検証

- 共通設定の保存・入力範囲・単位変換・大きい個別値の維持・マージン縮小をテスト。
- Strataの系列別引数、Mayaの4系列、Radiance YAML、llama.cpp引数・固定層数拒否・永続unit同期をテスト。
- 導入済みStrata 0.1.59.1の `launch --plan` が `--vram-reserve-mib=3072` を受け付けることを確認。
- 導入済みllama.cppの `--help` で `--fit-target` とGPU層数 `auto` を確認。
- 320px / 1280pxの実ブラウザで共通設定を保存し、再読み込み後の保持と横スクロール・ページ例外なしを確認。
- 大型モデルを再ロードした推論・負荷試験は実施していない。稼働中のユーザーの推論を中断していない。

- ローカル全体: 1584 passed / 2 skipped。最終Maya修正後の独立集中テスト: 16 passed。
- UI・アクセシビリティ: 18 passed。サンドボックスミラー: 320px/1280pxで保存成功。
- 独立レビュー: 必須修正0件。最終コードCI: 43872798598、PR #405。
