#!/usr/bin/env bash
set -euo pipefail
demo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
project_dir=$(dirname -- "$demo_dir")
exec python -u "$demo_dir/infinite_meeting_server.py" \
    --asr-models "${ASR_MODELS:-$project_dir/models/audio8-160ms}" "$@"
