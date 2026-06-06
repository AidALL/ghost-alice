#!/usr/bin/env bash
# Ghost-ALICE installer library: i18n (internationalization)
# Sourced by install.sh. Do not execute directly.

t() {
  local primary="$1" en="$2"
  [ -z "$en" ] && { printf '%s' "$primary"; return; }
  printf '%s' "$en"
}
