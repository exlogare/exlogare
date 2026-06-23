#!/usr/bin/env bash
# Emit randomised secrets ready to paste into .env.
#
# Usage:
#   scripts/generate_secrets.sh [.env.example]
set -euo pipefail

BASE="${1:-.env.example}"
if [[ ! -f "$BASE" ]]; then
    echo "base env file not found: $BASE" >&2
    exit 1
fi

rand_urlsafe() {
    python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
}

rand_hex() {
    python3 -c 'import secrets; print(secrets.token_hex(24))'
}

fernet_key() {
    python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
}

declare -A NEW
NEW[JWT_SECRET]="$(rand_urlsafe)"
NEW[ENCRYPTION_KEY]="$(fernet_key)"
NEW[POSTGRES_PASSWORD]="$(rand_hex)"
NEW[ADMIN_PASSWORD]="$(rand_urlsafe | tr -d '=' | head -c 24)"

SECRET_KEYS=(JWT_SECRET ENCRYPTION_KEY POSTGRES_PASSWORD ADMIN_PASSWORD)

while IFS= read -r line; do
    if [[ "$line" =~ ^([A-Z_]+)= ]]; then
        key="${BASH_REMATCH[1]}"
        for sk in "${SECRET_KEYS[@]}"; do
            if [[ "$key" == "$sk" && -n "${NEW[$key]+x}" ]]; then
                printf '%s=%s\n' "$key" "${NEW[$key]}"
                continue 2
            fi
        done
    fi
    printf '%s\n' "$line"
done < "$BASE"
