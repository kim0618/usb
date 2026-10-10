#!/usr/bin/env bash
# Build the context collector release from one commit: exactly the paths in RELEASE_FILES.txt,
# read with `git show <commit>:<path>` (never the working tree), plus MANIFEST.sha256.
#
#   deploy/crypto_context/stage_release.sh <commit> <out_dir>
#
# Output: <out_dir>/crypto-context-<short>/ (tree) and <out_dir>/crypto-context-<short>.tgz.
# Local only. It does not ssh, copy to a host, or touch any service.
set -euo pipefail
commit=${1:?commit}
out=${2:?out_dir}
repo=$(git rev-parse --show-toplevel)
full=$(git -C "$repo" rev-parse --verify "$commit^{commit}")
short=${full:0:7}
list="$repo/deploy/crypto_context/RELEASE_FILES.txt"
git -C "$repo" cat-file -e "$full:deploy/crypto_context/RELEASE_FILES.txt"
stage="$out/crypto-context-$short"
rm -rf "$stage"
mkdir -p "$stage"
git -C "$repo" show "$full:deploy/crypto_context/RELEASE_FILES.txt" | grep -v '^#' | grep -v '^$' \
  | while read -r path; do
      mkdir -p "$stage/$(dirname "$path")"
      git -C "$repo" show "$full:$path" > "$stage/$path"
    done
(cd "$stage" && find . -type f ! -name MANIFEST.sha256 | sed 's|^\./||' | LC_ALL=C sort \
  | xargs sha256sum > MANIFEST.sha256)
printf 'source_commit %s\n' "$full" > "$stage/SOURCE_COMMIT"
tar -C "$out" -czf "$out/crypto-context-$short.tgz" "crypto-context-$short"
echo "stage=$stage"
echo "tarball=$out/crypto-context-$short.tgz sha256=$(sha256sum "$out/crypto-context-$short.tgz" | cut -c1-64)"
echo "files=$(wc -l < "$stage/MANIFEST.sha256")"
