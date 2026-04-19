#!/usr/bin/env bash
# Load all AlKazBot secrets from macOS Keychain into the current shell.
#
# IMPORTANT: `source` this file; do NOT execute. The `export` statements
# are only applied when this runs in your shell's process.
#
#     source scripts/load-secrets.sh
#     # or equivalently:
#     . scripts/load-secrets.sh
#
# After this the env vars are set for the duration of THIS shell only.
#
# Known secret-name → env-var mappings are defined in the `MAPPING`
# array below. Add pairs here when you stash new secrets with
# `scripts/secrets.sh set <name> <value>`.
#
# Example — retrieve Railway API token:
#
#   ./scripts/secrets.sh set railway-token <paste-token-here>
#   source scripts/load-secrets.sh
#   echo "$RAILWAY_API_TOKEN"
#
# The Keychain prompts exactly ONCE per app/session (macOS caches the
# decision), so subsequent gets are silent. If you get an access prompt,
# click "Always Allow" to avoid repeats.
#
# --------------------------------------------------------------------------

# Map secret-name → env-var-name. Extend as you add more secrets.
# Format: "secret-name=ENV_VAR_NAME"
MAPPING=(
    "railway-token=RAILWAY_API_TOKEN"
    "railway-token=RAILWAY_TOKEN"
    "anthropic-api-key=ANTHROPIC_API_KEY"
    "telegram-bot-token=TELEGRAM_BOT_TOKEN"
    "prod-database-url=DATABASE_URL"
)

# Resolve script dir robustly (macOS has no `readlink -f`).
_sd="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)"
_secrets="$_sd/secrets.sh"

if [[ ! -x "$_secrets" ]]; then
    echo "load-secrets: $_secrets is not executable. chmod +x it." >&2
    return 1 2>/dev/null || exit 1
fi

_loaded=0
_missing=0
for pair in "${MAPPING[@]}"; do
    _name="${pair%=*}"
    _var="${pair#*=}"
    if _value="$("$_secrets" get "$_name" 2>/dev/null)"; then
        export "$_var=$_value"
        _loaded=$((_loaded + 1))
    else
        _missing=$((_missing + 1))
    fi
done
echo "load-secrets: exported=$_loaded missing=$_missing (run: scripts/secrets.sh list)"

unset _sd _secrets _loaded _missing _name _var _value pair
