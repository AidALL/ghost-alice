#!/usr/bin/env bash
# Ghost-ALICE - Context7 MCP install script
#
# Context7 is a RAG MCP that injects current official library docs into LLM context.
# Register this MCP in Claude Code. The API key is optional and raises the rate limit when present.
#
# Usage:
#   bash _shared/mcp/install-context7.sh                       # Use no key or an env var
#   CONTEXT7_API_KEY=ctx7_xxx bash _shared/mcp/install-context7.sh
#   bash _shared/mcp/install-context7.sh --uninstall           # Unregister
#   bash _shared/mcp/install-context7.sh --status              # Check registration status

set -euo pipefail

# -- Force UTF-8 locale --------------------------------------
ensure_utf8_locale() {
  local current="${LC_ALL:-${LANG:-}}"
  case "$current" in
    *UTF-8*|*utf8*|*utf-8*) return 0 ;;
  esac
  if command -v locale >/dev/null 2>&1; then
    local loc
    for loc in C.UTF-8 en_US.UTF-8 ko_KR.UTF-8 en_GB.UTF-8; do
      if locale -a 2>/dev/null | grep -qix "$loc"; then
        export LC_ALL="$loc" LANG="$loc"
        return 0
      fi
    done
  fi
  export LC_ALL="C.UTF-8" LANG="C.UTF-8"
}
ensure_utf8_locale

# -- Load shared secrets helper -------------------------------
SCRIPT_DIR_CONTEXT7="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECRETS_LOADER="$SCRIPT_DIR_CONTEXT7/../secrets/load.sh"
if [ -f "$SECRETS_LOADER" ]; then
  # shellcheck source=../secrets/load.sh
  source "$SECRETS_LOADER"
else
  # Fallback when helper is missing; supports env var -> prompt only.
  secrets_get_or_prompt() {
    local key="$1"; local label="${2:-$1}"
    local val="${!key:-}"
    if [ -n "$val" ]; then printf "%s" "$val"; return 0; fi
    if [ ! -t 0 ]; then return 1; fi
    printf "[INFO] %s: " "$label" >&2
    read -r val
    [ -z "$val" ] && return 1
    printf "%s" "$val"
  }
fi

# -- Colors ---------------------------------------------------
if [ -t 1 ]; then
  GREEN=$'\033[0;32m'; RED=$'\033[0;31m'; YELLOW=$'\033[0;33m'; CYAN=$'\033[0;36m'; NC=$'\033[0m'
else
  GREEN=''; RED=''; YELLOW=''; CYAN=''; NC=''
fi
info()  { printf "%s[INFO]%s %s\n"  "$CYAN"   "$NC" "$1"; }
ok()    { printf "%s[OK]%s %s\n"    "$GREEN"  "$NC" "$1"; }
warn()  { printf "%s[WARN]%s %s\n"  "$YELLOW" "$NC" "$1"; }
error() { printf "%s[ERROR]%s %s\n" "$RED"    "$NC" "$1" >&2; }

# -- Constants ------------------------------------------------
MCP_NAME="context7"
MCP_URL="https://mcp.context7.com/mcp"

# -- Check claude CLI -----------------------------------------
require_claude() {
  if ! command -v claude >/dev/null 2>&1; then
    error "claude CLI not found."
    error "Install Claude Code first: https://docs.claude.com/claude-code"
    exit 1
  fi
}

# -- Registration check ---------------------------------------
is_registered() {
  claude mcp list 2>/dev/null | grep -qE "^${MCP_NAME}([[:space:]]|:|$)"
}

# -- Unregister -----------------------------------------------
uninstall_context7() {
  require_claude
  if is_registered; then
    claude mcp remove "$MCP_NAME" >/dev/null
    ok "Context7 MCP unregistered."
  else
    info "Context7 MCP is not registered."
  fi
}

# -- Status ---------------------------------------------------
show_status() {
  require_claude
  if is_registered; then
    ok "Context7 MCP is registered."
    claude mcp list 2>/dev/null | grep -E "^${MCP_NAME}" || true
  else
    info "Context7 MCP is not registered."
  fi
}

# -- Install --------------------------------------------------
install_context7() {
  require_claude

  # Skip when already registered.
  if is_registered; then
    warn "Context7 MCP is already registered."
    info "To re-register: bash $0 --uninstall && bash $0"
    return 0
  fi

  # Resolve API key through the shared secrets helper.
  # Priority: environment variable -> ~/.ghost-alice/secrets.env -> prompt when interactive, with save option.
  info "An API key raises the rate limit and enables private repo access."
  info "Get one at https://context7.com/dashboard; registration also works on the free tier without a key."
  local api_key=""
  api_key=$(secrets_get_or_prompt CONTEXT7_API_KEY "Context7 API key" || true)

  # Register.
  if [ -n "$api_key" ]; then
    claude mcp add --transport http "$MCP_NAME" "$MCP_URL" \
      --header "CONTEXT7_API_KEY: $api_key"
    ok "Context7 MCP registered with API key."
  else
    claude mcp add --transport http "$MCP_NAME" "$MCP_URL"
    ok "Context7 MCP registered without API key on the free tier."
  fi

  printf "\n"
  info "Check with: claude mcp list, or /mcp inside Claude Code"
  info "Use it by including 'use context7' in the prompt."
  info "Example: \"Show how to use Next.js after(). use context7\""
}

# -- Main -----------------------------------------------------
case "${1:-}" in
  --uninstall|--remove|-u)
    uninstall_context7
    ;;
  --status|-s)
    show_status
    ;;
  --help|-h)
    cat <<'EOF'
Usage: bash _shared/mcp/install-context7.sh [OPTION]

Options:
  (no args)         Register Context7 MCP in Claude Code
  --status, -s      Check current registration status
  --uninstall, -u   Unregister
  --help, -h        Show this help

Environment:
  CONTEXT7_API_KEY  Context7 API key. When set, it skips the prompt.
EOF
    ;;
  "")
    install_context7
    ;;
  *)
    error "Unknown option: $1"
    error "Help: bash $0 --help"
    exit 1
    ;;
esac
