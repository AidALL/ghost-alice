# `_shared/secrets` - shared secrets loader

Ghost-ALICE scripts and skills use this helper as the single entry point for
credentials such as API keys, tokens, passwords, and account emails. Register a
value once, then retrieve it through the helper instead of hard-coding it in
project files.
## Contents

- [Storage](#storage)
- [Lookup priority](#lookup-priority)
- [bash usage](#bash-usage)
- [Python usage](#python-usage)
- [Key Naming](#key-naming)
- [Cautions](#cautions)


## Storage

- Location: `~/.ghost-alice/secrets.env`
- Permissions: `600` (owner read and write only). The helper sets this value.
- Directory permissions: `700`
- Format: `KEY=value` (.env style, quotes optional)

Example file:

```
# Ghost-ALICE secrets. Plaintext KEY=value format.
EXAMPLE_SERVICE_TOKEN=example-token-value
EXAMPLE_ACCOUNT_EMAIL=user@example.com
OPENAI_API_KEY=sk-example
```

## Lookup priority

Every lookup function finds the value in the following order.

1. An already-exported environment variable (for example, `export CONTEXT7_API_KEY=...`)
2. The `~/.ghost-alice/secrets.env` file
3. prompt is Limited to `get_or_prompt`, interactive environments only, with a save option offered after input.

## bash usage

```bash
source _shared/secrets/load.sh

# 1. Look up from env/file only (fails if absent)
key=$(secrets_get CONTEXT7_API_KEY) || { echo "key not found"; exit 1; }

# 2. Look up in env/file/prompt order (asks whether to save on input)
key=$(secrets_get_or_prompt CONTEXT7_API_KEY "Context7 API key") || exit 1

# 3. Set / unset directly
secrets_set MY_KEY "value"
secrets_unset MY_KEY

# 4. List registered keys (values masked)
secrets_list
```

## Python usage

```python
import sys, os
sys.path.insert(0, os.path.join("<project root>", "_shared", "secrets"))
import load as secrets

# 1. Look up from env/file only (None if absent)
key = secrets.get("CONTEXT7_API_KEY")
if key is None:
    raise RuntimeError("CONTEXT7_API_KEY not registered")

# 2. Look up in env/file/prompt order
key = secrets.get_or_prompt("CONTEXT7_API_KEY", label="Context7 API key")

# 3. Set / unset directly
secrets.set("MY_KEY", "value")
secrets.unset("MY_KEY")

# 4. List registered keys (values masked)
secrets.list_keys()
```

Or the CLI:

```bash
python _shared/secrets/load.py list
python _shared/secrets/load.py get CONTEXT7_API_KEY
python _shared/secrets/load.py set MY_KEY value
python _shared/secrets/load.py unset MY_KEY
```

## Key Naming

- All uppercase plus underscores (`SCREAMING_SNAKE_CASE`)
- ServiceName_Role format: `OPENAI_API_KEY`, `EXAMPLE_SERVICE_TOKEN`, `EXAMPLE_ACCOUNT_EMAIL`
- Multiple credentials for the same service may use an environment suffix such as `_PROD` or `_DEV`

Common key examples:

| Key | Purpose | Issuer |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI API access for integrations that explicitly need it | https://platform.openai.com |
| `ANTHROPIC_API_KEY` | Anthropic API access for integrations that explicitly need it | https://console.anthropic.com |
| `EXAMPLE_SERVICE_TOKEN` | Placeholder for a third-party service token | Service account settings |
| `EXAMPLE_ACCOUNT_EMAIL` | Placeholder for a service account email | Service account settings |

Add project-specific keys in private deployment documentation, not in the public
core docs.

## Cautions

- `secrets.env` is plaintext. Keep it only on a trusted personal or organization-managed machine. Do not put it on a shared server.
- Never commit it to git. The helper does not block this automatically, so take care yourself.
- The helper keeps permissions at 600 even after saving, but right after editing manually, run `chmod 600 ~/.ghost-alice/secrets.env` once.
- OS Keychain (macOS Keychain, Linux Secret Service, Windows Credential Manager) is
  not supported at present. This is a decision to lower the barrier to entry for non-coding users. It will be implemented separately if it becomes necessary.
