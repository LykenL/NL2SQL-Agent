#!/bin/sh
# Install a repo-local post-commit hook that pushes every new commit to its
# upstream branch. Git hooks live in .git/ and are not versioned, so run this
# once per clone.
#
#   ./scripts/install-autopush.sh          # install
#   touch .git/AUTOPUSH_OFF                # temporarily disable
#   rm .git/AUTOPUSH_OFF                   # re-enable
set -e

gitdir=$(git rev-parse --git-dir)
hook="$gitdir/hooks/post-commit"

cat > "$hook" <<'HOOK'
#!/bin/sh
# Auto-push every new commit to its upstream branch.
# Installed by scripts/install-autopush.sh (git hooks are local, not versioned).
# Disable with: touch .git/AUTOPUSH_OFF
gitdir=$(git rev-parse --git-dir 2>/dev/null) || exit 0
[ -f "$gitdir/AUTOPUSH_OFF" ] && exit 0

branch=$(git symbolic-ref --short HEAD 2>/dev/null) || exit 0
upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null) || exit 0
[ -z "$upstream" ] && exit 0
remote=${upstream%%/*}

# Never publish a half-finished history.
if [ -d "$gitdir/rebase-merge" ] || [ -d "$gitdir/rebase-apply" ] || [ -f "$gitdir/MERGE_HEAD" ]; then
  exit 0
fi

# Background + detached from the commit process so `git commit` returns immediately.
(
  GIT_TERMINAL_PROMPT=0 git push "$remote" "$branch" >> "$gitdir/autopush.log" 2>&1
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] autopush $branch -> $remote exit=$?" >> "$gitdir/autopush.log"
) &

exit 0
HOOK

chmod +x "$hook"
echo "installed: $hook"
echo "log:       $gitdir/autopush.log"
