#!/usr/bin/env bash
# Rebuild the whole platform from nothing on this Mac (about 45 minutes, most of it model tuning):
# simulated business → CRM + lake landing → full pipeline load → models → web app.
#   scripts/rebuild.sh [YYYY-MM-DD]      (default: today; the simulated history ends the day before)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
PY=.venv/bin/python
ASOF=${1:-$(date +%F)}
YESTERDAY=$($PY -c "from datetime import date, timedelta; print(date.fromisoformat('$ASOF') - timedelta(1))")
echo "== rebuild as of $ASOF"
rm -rf lake var/sim_truth
$PY -m sim init --reset                               # CRM aisleco_crm (OLTP v6) + master data
$PY -m sim backfill --to "$YESTERDAY"                 # the business from 1 Jul 2022; webhooks through the gateway
$PY -m pipeline init --drop                           # warehouse aisleco_dw + 39 data-quality rules
$PY -m pipeline run --asof "$ASOF" --full-refresh     # initial load: sources → Bronze → Silver → Gold → marts
$PY -m ml init                                        # feature registry, ml schema
$PY -m ml eda --asof "$ASOF"                          # docs/v2/ml_eda.md (label window evidence)
$PY -m ml train --asof "$ASOF"                        # tuned, calibrated, gated, registered in MLflow
$PY -m ml score --asof "$ASOF"                        # today's Sales list
$PY -m ml forecast --asof "$ASOF"                     # next 26 weeks of staff-hours
$PY -m pipeline run --asof "$ASOF"                    # marts again, now with scores and forecast
$PY -m app.api.users seed
(cd app/web && npm install --silent && npm run build --silent)
$PY scripts/build_catalog.py
echo "== done. Start the services: scripts/services.sh start"
