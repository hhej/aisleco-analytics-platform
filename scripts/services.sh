#!/usr/bin/env bash
# Start / stop / show the local services of the Aisle & Co. platform (PLAN §5). PostgreSQL runs as a brew service.
#   scripts/services.sh start [gateway|mlflow|airflow|web]   (no name = all)
#   scripts/services.sh stop  [name]
#   scripts/services.sh status
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN="$ROOT/var/run"; LOG="$ROOT/var/log"; mkdir -p "$RUN" "$LOG"
PY="$ROOT/.venv/bin/python"
port() { case $1 in gateway) echo 8001;; mlflow) echo 5050;; airflow) echo 8080;; web) echo 8000;; esac; }  # bash 3.2 (macOS): no maps

cmd() {
  case "$1" in
    gateway) echo "env GATEWAY_MODE=${GATEWAY_MODE:-sim} $PY -m sources.chat_gateway" ;;  # the business is simulated: honour X-Sim-Clock
    mlflow)  echo "$ROOT/.venv/bin/mlflow server --host 127.0.0.1 --port 5050 --backend-store-uri postgresql+psycopg:///mlflow --default-artifact-root $ROOT/lake/mlflow" ;;
    airflow) echo "$ROOT/scripts/airflow.sh standalone" ;;
    web)     echo "$PY -m app.api" ;;
  esac
}

start() {
  local n=$1
  if [ -f "$RUN/$n.pid" ] && kill -0 "$(cat "$RUN/$n.pid")" 2>/dev/null; then echo "$n already running"; return; fi
  [ "$n" = mlflow ] && { psql -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname='mlflow'" | grep -q 1 || createdb mlflow; }
  (cd "$ROOT" && nohup $(cmd "$n") >>"$LOG/$n.log" 2>&1 & echo $! >"$RUN/$n.pid")
  echo "$n started on port $(port $n) (log var/log/$n.log)"
}

tree() { local c; for c in $(pgrep -P "$1"); do echo "$c"; tree "$c"; done; }

stop() {
  local n=$1 all i
  [ -f "$RUN/$n.pid" ] || { echo "$n not running"; return; }
  # the whole tree, collected before signalling: Airflow standalone's scheduler/triggerer otherwise outlive it (and keep the port)
  all="$(cat "$RUN/$n.pid") $(tree "$(cat "$RUN/$n.pid")")"
  kill -TERM $all 2>/dev/null
  for i in $(seq 1 30); do kill -0 $all 2>/dev/null || break; sleep 1; done
  rm -f "$RUN/$n.pid"; echo "$n stopped"
}

status() {
  pg_isready -q && echo "postgres   up    5432" || echo "postgres   DOWN  5432"
  for n in gateway mlflow airflow web; do
    if lsof -nP -iTCP:$(port $n) -sTCP:LISTEN >/dev/null 2>&1; then printf "%-10s up    %s\n" $n $(port $n); else printf "%-10s down  %s\n" $n $(port $n); fi
  done
}

names=${2:-gateway mlflow airflow web}
case "$1" in
  start) for n in $names; do start $n; done ;;
  stop) for n in $names; do stop $n; done ;;
  status) status ;;
  *) echo "usage: $0 start|stop|status [gateway|mlflow|airflow|web]"; exit 1 ;;
esac
