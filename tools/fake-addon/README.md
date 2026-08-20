# Add-on v2 fake service

PR-0以降の契約・UX・bridge・resource broker E2Eで共通利用する、loopback限定のテスト用serviceです。
本番add-onではありません。

```bash
./deck.sh ext lint tools/fake-addon/control-deck-addon.json
.venv/bin/python tools/fake-addon/run.py
curl http://127.0.0.1:9130/health
```

`POST /test/health` で `healthy` / `degraded` / `unavailable` / `setup_required` と
video executorのavailabilityを切り替えられます。`POST /fake-gpu/jobs` は
`duration_sec` と `vram_bytes` を受け、返されたIDを `DELETE /fake-gpu/jobs/{id}` でキャンセルできます。
