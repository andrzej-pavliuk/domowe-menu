#!/usr/bin/env sh
set -eu
PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"
[ -f Dockerfile ] || { printf '%s\n' 'Brak Dockerfile w katalogu aplikacji.' >&2; exit 1; }
VERSION="${1:-0.4.1}"
IMAGE="domowe-menu:${VERSION}"
ARCHIVE="$(dirname "$PROJECT_DIR")/domowe-menu-${VERSION}.tar"
docker buildx build --platform linux/amd64 --load -t "$IMAGE" .
docker save -o "$ARCHIVE" "$IMAGE"
printf '\nGotowy obraz: %s\n' "$ARCHIVE"
