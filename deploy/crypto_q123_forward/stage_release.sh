#!/usr/bin/env bash
# Build the Q123 forward recorder release from one commit: exactly the paths in RELEASE_FILES.txt,
# read with `git show <commit>:<path>` (never the working tree), plus MANIFEST.sha256.
#
#   deploy/crypto_q123_forward/stage_release.sh <commit> <out_dir>
#
# Local only. It does not ssh, copy to a host, or touch any service.
set -euo pipefail
commit=${1:?commit}
out=${2:?out_dir}
repo=$(git rev-parse --show-toplevel)
full=$(git -C "$repo" rev-parse --verify "$commit^{commit}")
short=${full:0:7}
git -C "$repo" cat-file -e "$full:deploy/crypto_q123_forward/RELEASE_FILES.txt"
stage="$out/q123-forward-$short"
rm -rf "$stage"
mkdir -p "$stage"
git -C "$repo" show "$full:deploy/crypto_q123_forward/RELEASE_FILES.txt" | grep -v '^#' \
  | grep -v '^$' | while read -r path; do
      mkdir -p "$stage/$(dirname "$path")"
      git -C "$repo" show "$full:$path" > "$stage/$path"
    done
(cd "$stage" && find . -type f ! -name MANIFEST.sha256 | sed 's|^\./||' | LC_ALL=C sort \
  | xargs sha256sum > MANIFEST.sha256)
printf 'source_commit %s\n' "$full" > "$stage/SOURCE_COMMIT"
tar -C "$out" -czf "$out/q123-forward-$short.tgz" "q123-forward-$short"
echo "stage=$stage"
echo "tarball=$out/q123-forward-$short.tgz sha256=$(sha256sum "$out/q123-forward-$short.tgz" | cut -c1-64)"
echo "files=$(wc -l < "$stage/MANIFEST.sha256")"
