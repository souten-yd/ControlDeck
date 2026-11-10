# Add-on AIのlocal-only契約

PatentBuddyなどの機密資料を扱うAdd-onは `local_only: true` をcomplete/stream要求に付ける。Hostがその要求ごとに管理対象llama.cpp・ローカルGGUF・loopback endpointを検証し、検証不能なら503で止める。ローカルURLだけでOllama/cloud relayを許可しない。従来要求は互換維持。

capabilitiesで契約対応/利用可能性を確認できるが、事前確認後の設定変更に備えて実行時にも再確認する。Host GPU admission後、providerへ送る直前にも確認する。local-only要求は環境proxy・redirectを使わない。モデル名/パス/provider URLはAdd-onへ返さない。

ローカルOllamaを含む他runtimeのlocal-only対応は証明契約を追加するまで拒否する。OS/Host管理者自体が悪意を持つ場合まで隔離する契約ではない。Hostが管理するモデル実行の境界で保証する。

実装/受入進行中。
