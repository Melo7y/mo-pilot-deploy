#!/bin/sh
set -eu

mode="${1:-loop}"
interval="${BACKUP_INTERVAL_SECONDS:-86400}"
retention_days="${BACKUP_RETENTION_DAYS:-30}"

case "$mode" in
    once|loop) ;;
    *)
        echo "usage: backup.sh once|loop" >&2
        exit 2
        ;;
esac

case "$interval:$retention_days" in
    *[!0-9:]*|:*|*:)
        echo "backup interval and retention days must be positive integers" >&2
        exit 2
        ;;
esac

if [ "$interval" -le 0 ] || [ "$retention_days" -le 0 ]; then
    echo "backup interval and retention days must be positive integers" >&2
    exit 2
fi

umask 077
mkdir -p /backups

run_backup() {
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    backup_id="backup-$timestamp"
    temporary="/backups/.partial-$backup_id-$$"
    destination="/backups/$backup_id"

    cleanup() {
        if [ -n "${temporary:-}" ] && [ -d "$temporary" ]; then
            rm -rf -- "$temporary"
        fi
    }
    trap cleanup EXIT HUP INT TERM

    mkdir "$temporary"
    echo "creating PostgreSQL backup $backup_id"
    pg_dump --format=custom --no-owner --no-privileges --file="$temporary/database.dump"

    echo "creating attachment backup $backup_id"
    tar -C /attachments -czf "$temporary/attachments.tar.gz" .

    cat > "$temporary/manifest.txt" <<EOF
backup_id=$backup_id
created_at_utc=$timestamp
database=$PGDATABASE
EOF
    (
        cd "$temporary"
        sha256sum database.dump attachments.tar.gz manifest.txt > SHA256SUMS
    )
    mv "$temporary" "$destination"
    temporary=""
    trap - EXIT HUP INT TERM

    find /backups -mindepth 1 -maxdepth 1 -type d -name 'backup-*' \
        -mtime "+$retention_days" -exec rm -rf -- {} \;
    echo "backup ready: $destination"
}

run_backup
if [ "$mode" = "once" ]; then
    exit 0
fi

while :; do
    sleep "$interval"
    run_backup
done
