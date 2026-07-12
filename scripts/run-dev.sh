#!/usr/bin/env bash
# 互換ラッパー: ./deck.sh（自動セットアップ + 起動）へ委譲
exec "$(dirname "${BASH_SOURCE[0]}")/../deck.sh" start
