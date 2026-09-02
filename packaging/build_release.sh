#!/bin/bash
# Reproducible HelSpin release archive.
#
# Same tag in, same bytes out, on any machine, at any time -- so a checksum
# recorded today still verifies the download years from now.
#
# Two things defeat naive archiving and are handled here:
#   * Git stores no timestamps, so a checkout stamps every file with the
#     clock. All mtimes are pinned to the commit date instead.
#   * .git/index caches inode numbers and stat times -- machine state that
#     differs between two clones of the same commit. The working .git is
#     therefore NOT shipped; history travels as a git bundle, which is
#     reproducible, and `git clone helspin-<v>.bundle` restores it in full.
set -e
v="$1"; u=$(echo "$v" | tr . _); dest="$2"
src=/home/claude/work/helspin
work=$(mktemp -d)
git clone -q "$src" "$work/repo"
cd "$work/repo"
for t in $(git tag); do
  tv=${t#v}
  newest=$(printf '%s\n%s\n' "$tv" "$v" | sort -V | tail -1)
  [ "$newest" = "$tv" ] && [ "$tv" != "$v" ] && git tag -d "$t" >/dev/null
done
git checkout -q main 2>/dev/null || true
git reset --hard -q "v$v"
git remote remove origin 2>/dev/null || true
git reflog expire --expire=now --all
git gc --prune=now -q 2>/dev/null || true
ts=$(git log -1 --format=%cI "v$v")
mkdir -p "$work/out/helspin"
git archive "v$v" | tar -x -C "$work/out/helspin"
git bundle create -q "$work/out/helspin/helspin-$u.bundle" --all 2>/dev/null
cd "$work/out"
find helspin -exec touch --no-dereference --date="$ts" {} +
rm -f "$dest"
# -X drops platform extra fields (uid/gid, sub-second times); the sorted list
# fixes entry order, which directory traversal does not guarantee.
find helspin | LC_ALL=C sort | zip -qX "$dest" -@
cd /; rm -rf "$work"
