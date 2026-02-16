#!/bin/bash

PROJECT_DIR="$HOME/camera-server"
LOGFILE="$PROJECT_DIR/deploy.log"

cd $PROJECT_DIR || exit 1

# read .env
set -a
source .env
set +a

log() {
  echo "[$(date)] $1" >> $LOGFILE
}

notify() {
  if [ -n "$DISCORD_DEPLOY_LOG_WEBHOOK" ]; then
    curl -s -H "Content-Type: application/json" \
      -d "{\"content\":\"$1\"}" \
      "$DISCORD_DEPLOY_LOG_WEBHOOK" >/dev/null
  fi
}

log "Deploy start"

# git pull
if ! git pull >> $LOGFILE 2>&1; then
  log "Deploy failed: git pull error"
  notify "❌ Deploy failed: git pull error"
  exit 1
fi

# サーバー再起動
log "Stopping old server"
pkill -f camera_server.py || true
sleep 3

log "Starting server"
tmux kill-session -t camera_server 2>/dev/null || true
tmux new-session -d -s camera_server "venv/bin/python camera_server.py"

log "Deploy success"
notify "✅ Deploy success"
