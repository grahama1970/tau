R=/tmp/replay-c; mkdir -p "$R/repo/src"; cd "$R/repo"
count() { git worktree list --porcelain 2>/dev/null | grep -c "unrelated"; }
git init -q -b main .
echo x > src/f; git add -A
git -c user.email=t@t -c user.name=t commit -qm init >/dev/null
git worktree add --detach -q "$R/unrelated" HEAD
git config gc.worktreePruneExpire now
git config gc.auto 0
git config maintenance.auto false
rm -rf "$R/unrelated"
ln -s /etc/hostname src/escape; git add src/escape
git -c user.email=t@t -c user.name=t commit -qm symlink >/dev/null
sleep 2   # give any background maintenance time to land
echo "with maintenance.auto=false: unrelated=$(count)"
