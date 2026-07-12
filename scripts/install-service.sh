#!/usr/bin/env bash
# 互換ラッパー: ./deck.sh service へ委譲
exec "$(dirname "${BASH_SOURCE[0]}")/../deck.sh" service
