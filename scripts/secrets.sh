#!/usr/bin/env bash
# Secure secret management via macOS Keychain.
#
# Stores project secrets (Railway API token, DB password backup, etc.) in
# the user's *login* Keychain — the same place macOS protects wifi passwords
# and Safari passwords. Access control is enforced by macOS — anyone who
# reads a plaintext file on your disk can't get these without your login.
#
# Usage:
#   ./scripts/secrets.sh set <name> <value>        — store / overwrite
#   ./scripts/secrets.sh set-prompt <name>         — prompt for value (no history)
#   ./scripts/secrets.sh get <name>                — print value to stdout
#   ./scripts/secrets.sh list                      — list known secret names
#   ./scripts/secrets.sh remove <name>             — delete
#
# Secret names are stored under service `${SERVICE_PREFIX}-<name>` with
# `$USER` as the account, so they're scoped to THIS repo and THIS user.
#
# To pull all known secrets into env vars for the current shell, see
# `scripts/load-secrets.sh` (source it, don't exec).
#
# --------------------------------------------------------------------------

set -euo pipefail

SERVICE_PREFIX="AlKazBot"
ACCOUNT="$USER"

die() { echo "error: $*" >&2; exit 1; }

need_mac() {
    [[ "$(uname -s)" == "Darwin" ]] \
        || die "Only macOS is supported (needs /usr/bin/security)."
}

service_of() { echo "${SERVICE_PREFIX}-$1"; }

cmd_set() {
    local name="${1:?secret name required}"
    local value="${2:?value required}"
    local svc; svc="$(service_of "$name")"
    # -U = update if exists. Replace quietly.
    #
    # ACL notes on `-T`:
    #   `-T /usr/bin/security` adds the `security` CLI to the trusted-apps
    #   list, so future `security find-generic-password` reads don't
    #   prompt for the login password. Combined with user clicking
    #   "Always Allow" on the first prompt (if any), this makes the
    #   pipeline silent across sessions.
    security add-generic-password \
        -U \
        -s "$svc" \
        -a "$ACCOUNT" \
        -w "$value" \
        -T /usr/bin/security \
        2>/dev/null
    echo "stored: $name (Keychain service=$svc account=$ACCOUNT)"
}

cmd_set_prompt() {
    local name="${1:?secret name required}"
    local value
    # -s suppresses echo so the value doesn't leak into scrollback.
    read -r -s -p "value for $name: " value
    echo
    [[ -n "$value" ]] || die "empty value rejected"
    cmd_set "$name" "$value"
}

cmd_get() {
    local name="${1:?secret name required}"
    local svc; svc="$(service_of "$name")"
    security find-generic-password -s "$svc" -a "$ACCOUNT" -w 2>/dev/null \
        || die "secret '$name' not found (service=$svc)"
}

cmd_list() {
    # Grep the keychain dump for our service prefix. `security dump-keychain`
    # would include plaintext, so we pipe through a filter that only extracts
    # the service name line.
    security dump-keychain 2>/dev/null \
        | grep -E '"svce"<blob>="'"${SERVICE_PREFIX}"'-' \
        | sed -E 's/.*"svce"<blob>="'"${SERVICE_PREFIX}"'-([^"]+)".*/\1/' \
        | sort -u
}

cmd_remove() {
    local name="${1:?secret name required}"
    local svc; svc="$(service_of "$name")"
    security delete-generic-password -s "$svc" -a "$ACCOUNT" >/dev/null 2>&1 \
        && echo "deleted: $name" \
        || die "secret '$name' not found (service=$svc)"
}

main() {
    need_mac
    local sub="${1:-}"
    shift || true
    case "$sub" in
        set)         cmd_set "$@" ;;
        set-prompt)  cmd_set_prompt "$@" ;;
        get)         cmd_get "$@" ;;
        list)        cmd_list ;;
        remove|rm|delete) cmd_remove "$@" ;;
        ""|help|-h|--help)
            sed -n '2,/^# ---/p' "$0" | sed 's/^# \{0,1\}//'
            ;;
        *) die "unknown subcommand: $sub (try: set set-prompt get list remove)" ;;
    esac
}

main "$@"
