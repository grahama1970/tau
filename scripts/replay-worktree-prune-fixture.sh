R=/tmp/replay-$1; mkdir -p "$R/repo/src"; cd "$R/repo"
count() { git worktree list --porcelain 2>/dev/null | grep -c "unrelated" ; }
step() { printf '%-40s unrelated=%s\n' "$1" "$(count)"; }
git init -q -b main .
echo x > src/f; git add -A
git -c user.email=t@t -c user.name=t commit -qm init >/dev/null
git worktree add --detach -q "$R/unrelated" HEAD;   step "worktree add unrelated"
git config gc.worktreePruneExpire now;              step "config pruneExpire=now"
git config gc.auto 0;                               step "config gc.auto=0"
rm -rf "$R/unrelated";                              step "rm -rf unrelated dir"
echo out > "$R/external"; ln -s "$R/external" src/escape
git add src/escape;                                 step "git add symlink"
git -c user.email=t@t -c user.name=t commit -qm symlink >/dev/null; step "git commit symlink"
