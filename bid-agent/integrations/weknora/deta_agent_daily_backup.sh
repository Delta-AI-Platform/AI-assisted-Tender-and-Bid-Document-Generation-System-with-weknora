#!/usr/bin/env bash
set -Eeuo pipefail
umask 0077

BACKUP_ROOT="${DETA_AGENT_BACKUP_ROOT:-/srv/deta-bid/backups/daily}"
RETENTION="${DETA_AGENT_BACKUP_RETENTION:-3}"
WEKNORA_DB="/home/ubuntu/codex-workspace/weknora-build-tools/data/weknora.db"
PROJECT_DB="/home/ubuntu/codex-workspace/bid-agent/runs/native-projects.sqlite"
PROJECT_FILES="/home/ubuntu/codex-workspace/bid-agent/runs/native-projects"

[[ "$RETENTION" =~ ^[1-9][0-9]*$ ]] || { echo "invalid retention" >&2; exit 1; }
for source in "$WEKNORA_DB" "$PROJECT_DB"; do [[ -r "$source" ]] || { echo "unreadable database: $source" >&2; exit 1; }; done
[[ -d "$PROJECT_FILES" ]] || { echo "project directory missing" >&2; exit 1; }
mkdir -p "$BACKUP_ROOT"
exec 9>"$BACKUP_ROOT/.backup.lock"; flock -n 9 || { echo "backup already running" >&2; exit 1; }
stamp="$(date '+%Y%m%d-%H%M%S')"; temp="$BACKUP_ROOT/.incomplete-$stamp"; final="$BACKUP_ROOT/$stamp"
cleanup(){ rc=$?; [[ $rc -eq 0 ]] || rm -rf -- "$temp"; exit $rc; }; trap cleanup EXIT
mkdir "$temp"
/home/ubuntu/codex-workspace/bid-agent/.venv/bin/python - "$WEKNORA_DB" "$temp/weknora.db" "$PROJECT_DB" "$temp/native-projects.sqlite" <<'PY'
import sqlite3,sys
for source,target in zip(sys.argv[1::2],sys.argv[2::2]):
    with sqlite3.connect(source,timeout=30) as src,sqlite3.connect(target) as dst:src.backup(dst)
    with sqlite3.connect(target) as check:
        if check.execute('pragma integrity_check').fetchone()[0] != 'ok':raise SystemExit('SQLite integrity check failed: '+target)
PY
tar --one-file-system --gzip --create --file="$temp/native-projects.tar.gz" --directory="$(dirname "$PROJECT_FILES")" "$(basename "$PROJECT_FILES")"
cat >"$temp/backup.meta" <<EOF
created_at=$(date --iso-8601=seconds)
weknora_db=$WEKNORA_DB
project_db=$PROJECT_DB
project_files=$PROJECT_FILES
EOF
(cd "$temp" && sha256sum weknora.db native-projects.sqlite native-projects.tar.gz backup.meta >SHA256SUMS && sha256sum -c SHA256SUMS >/dev/null)
mv "$temp" "$final"; trap - EXIT
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '[0-9]*' -printf '%f\n' | sort | head -n -"$RETENTION" | while read -r old; do rm -rf -- "$BACKUP_ROOT/$old"; done
python3 - "$BACKUP_ROOT/latest-status.json" "$final" <<'PY'
import json,os,sys,tempfile
target,folder=sys.argv[1:];payload={'status':'success','completed_at':__import__('datetime').datetime.now().astimezone().isoformat(),'backup_directory':folder,'checksum_status':'passed'}
fd,tmp=tempfile.mkstemp(dir=os.path.dirname(target));os.close(fd)
with open(tmp,'w') as f:json.dump(payload,f,ensure_ascii=False,indent=2)
os.chmod(tmp,0o600);os.replace(tmp,target)
PY
echo "backup completed: $final"
