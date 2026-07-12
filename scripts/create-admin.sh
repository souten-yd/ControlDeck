#!/usr/bin/env bash
# 管理者アカウント作成
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

refuse_root
ensure_venv

[ $# -ge 1 ] || die "使用方法: $0 <username>"
cd "$REPO_ROOT/backend"
exec "$VENV/bin/python" -m app.cli create-admin "$1"
