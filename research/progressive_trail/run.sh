#!/usr/bin/env bash
set -Eeuo pipefail
trial_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python3 "$trial_dir/progressive_trial.py" --home "${SCOUT_OPTIONS_HOME:-$HOME/scout-options}" --out "${SCOUT_OPTIONS_HOME:-$HOME/scout-options}/.scout-options/progressive-test-results"
