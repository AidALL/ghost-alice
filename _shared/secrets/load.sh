#!/usr/bin/env bash
# Ghost-ALICE shared secrets loader for bash
#
# All skills and scripts retrieve login information such as API keys, tokens,
# passwords, and email credentials through the same path. Once a value is
# registered, later calls retrieve it automatically instead of prompting each time.
#
# Location: ~/.ghost-alice/secrets.env (mode 600)
# Format: KEY=value (.env style)
#
# Usage:
#   source _shared/secrets/load.sh
#
#   # 1. Look up from env var/file only (fail when missing)
#   key=$(secrets_get CONTEXT7_API_KEY) || { echo "missing key"; exit 1; }
#
#   # 2. Look up env/file/prompt in order (asks whether to save entered values)
#   key=$(secrets_get_or_prompt CONTEXT7_API_KEY "Context7 API key") || exit 1
#
#   # 3. Store directly
#   secrets_set MY_KEY "value"
#
# Lookup order for every function:
#   1) Already exported environment variable
#   2) ~/.ghost-alice/secrets.env
#   3) prompt (interactive environments only; secrets_get_or_prompt only).

set -u

GHOST_ALICE_SECRETS_FILE="${GHOST_ALICE_SECRETS_FILE:-$HOME/.ghost-alice/secrets.env}"

# Define color variables when the caller has not provided them.
if [ -z "${C_RESET:-}" ]; then
  if [ -t 2 ]; then
    C_CYAN=$'\033[0;36m'; C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[0;33m'
    C_RED=$'\033[0;31m'; C_RESET=$'\033[0m'
  else
    C_CYAN=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_RESET=""
  fi
fi

# -- File initialization -------------------------------------
secrets_file_init() {
  local dir
  dir=$(dirname "$GHOST_ALICE_SECRETS_FILE")
  if [ ! -d "$dir" ]; then
    mkdir -p "$dir"
    chmod 700 "$dir" 2>/dev/null || true
  fi
  if [ ! -f "$GHOST_ALICE_SECRETS_FILE" ]; then
    cat > "$GHOST_ALICE_SECRETS_FILE" <<'HEADER'
# Ghost-ALICE secrets. Plaintext KEY=value format.
# Keep mode 600. Never commit this file to git.
# One key per line. Quotes are optional (KEY=value and KEY="value" are both allowed).
HEADER
  fi
  chmod 600 "$GHOST_ALICE_SECRETS_FILE" 2>/dev/null || true
}

# -- Single-key lookup (env -> file) -------------------------
# Usage: secrets_get KEY
# stdout: value on success, exit 1 on failure
secrets_get() {
  local key="$1"
  local val=""

  # 1. env var
  val="${!key:-}"
  if [ -n "$val" ]; then
    printf "%s" "$val"
    return 0
  fi

  # 2. ~/.ghost-alice/secrets.env
  if [ -f "$GHOST_ALICE_SECRETS_FILE" ]; then
    val=$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$GHOST_ALICE_SECRETS_FILE" 2>/dev/null \
          | head -1 \
          | sed -E "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*//")
    # Remove surrounding quotes.
    val="${val%\"}"; val="${val#\"}"
    val="${val%\'}"; val="${val#\'}"
    # Remove trailing CR.
    val="${val%$'\r'}"
    if [ -n "$val" ]; then
      printf "%s" "$val"
      return 0
    fi
  fi

  return 1
}

# -- Store key (overwrite) -----------------------------------
# Usage: secrets_set KEY VALUE
secrets_set() {
  local key="$1"
  local value="$2"
  secrets_file_init

  local tmp
  tmp=$(mktemp)
  if [ -f "$GHOST_ALICE_SECRETS_FILE" ]; then
    grep -vE "^[[:space:]]*${key}[[:space:]]*=" "$GHOST_ALICE_SECRETS_FILE" > "$tmp" || true
  fi
  printf "%s=%s\n" "$key" "$value" >> "$tmp"
  mv "$tmp" "$GHOST_ALICE_SECRETS_FILE"
  chmod 600 "$GHOST_ALICE_SECRETS_FILE" 2>/dev/null || true
}

# -- Delete key ----------------------------------------------
# Usage: secrets_unset KEY
secrets_unset() {
  local key="$1"
  if [ ! -f "$GHOST_ALICE_SECRETS_FILE" ]; then
    return 0
  fi
  local tmp
  tmp=$(mktemp)
  grep -vE "^[[:space:]]*${key}[[:space:]]*=" "$GHOST_ALICE_SECRETS_FILE" > "$tmp" || true
  mv "$tmp" "$GHOST_ALICE_SECRETS_FILE"
  chmod 600 "$GHOST_ALICE_SECRETS_FILE" 2>/dev/null || true
}

# -- Lookup + prompt + save option ---------------------------
# Usage: secrets_get_or_prompt KEY [LABEL]
# stdout: value on success, exit 1 for empty input or non-TTY
secrets_get_or_prompt() {
  local key="$1"
  local label="${2:-$1}"

  local val
  if val=$(secrets_get "$key"); then
    printf "%s" "$val"
    return 0
  fi

  if [ ! -t 0 ]; then
    printf "%s[WARN]%s %s is not registered; skipping prompt in non-TTY mode.\n" \
      "$C_YELLOW" "$C_RESET" "$label" >&2
    return 1
  fi

  printf "%s[INFO]%s %s is not registered.\n" "$C_CYAN" "$C_RESET" "$label" >&2
  printf "Enter a value, or press Enter to skip: " >&2
  read -r val
  if [ -z "$val" ]; then
    return 1
  fi

  printf "%s[INFO]%s Save to %s? [Y/n] " "$C_CYAN" "$C_RESET" "$GHOST_ALICE_SECRETS_FILE" >&2
  local save_choice=""
  read -r save_choice
  case "$save_choice" in
    n|N|no|NO|No) ;;
    *) secrets_set "$key" "$val"
       printf "%s[OK]%s %s saved\n" "$C_GREEN" "$C_RESET" "$key" >&2 ;;
  esac

  printf "%s" "$val"
  return 0
}

# -- Registered key list (masked values) ---------------------
secrets_list() {
  if [ ! -f "$GHOST_ALICE_SECRETS_FILE" ]; then
    printf "%s[INFO]%s secrets file does not exist (%s).\n" \
      "$C_CYAN" "$C_RESET" "$GHOST_ALICE_SECRETS_FILE" >&2
    return 0
  fi
  printf "Registered secrets (%s):\n\n" "$GHOST_ALICE_SECRETS_FILE"
  awk -F= '
    /^[[:space:]]*#/ || /^[[:space:]]*$/ { next }
    /=/ {
      key=$1
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", key)
      val=$2
      for (i=3; i<=NF; i++) val = val "=" $i
      gsub(/^[[:space:]]+/, "", val)
      gsub(/^"|"$/, "", val)
      gsub(/^'\''|'\''$/, "", val)
      n = length(val)
      if (n <= 4) masked = "****"
      else masked = substr(val, 1, 2) "****" substr(val, n-1, 2) "  (" n " chars)"
      printf "  %-30s %s\n", key, masked
    }
  ' "$GHOST_ALICE_SECRETS_FILE"
}
