#!/usr/bin/env bash
# 互換ラッパー: ./deck.sh admin へ委譲
exec "$(dirname "${BASH_SOURCE[0]}")/../deck.sh" admin "$@"
