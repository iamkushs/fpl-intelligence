#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

./scripts/verify-markdown.sh
./scripts/verify-backend.sh
./scripts/verify-frontend.sh
