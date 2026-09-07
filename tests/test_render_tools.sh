#!/usr/bin/env bash
set -euo pipefail
source scripts/install-render-tools.sh

run_case() (
  local scenario="$1" expected="$2" calls=0 ready=0 actual=0
  render_tools_ready() { [[ "$scenario" == ready || "$ready" == 1 ]]; }
  timeout() {
    calls=$((calls + 1))
    [[ "$1" == --kill-after=10s ]] || return 99
    [[ "$*" != *fonts-noto-cjk-extra* && "$*" != *-qq* ]] || return 99
    if [[ "$*" == *' update' ]]; then
      [[ "$2" == 90s ]] || return 99
      [[ "$scenario" != update_failure ]] || return 124
    else
      [[ "$2" == 120s && "$*" == *--no-install-recommends* ]] || return 99
      if [[ ( "$scenario" == retry || "$scenario" == update_failure ) && "$calls" == 1 || "$scenario" == install_failure ]]; then
        return 124
      fi
      [[ "$scenario" == missing_tools ]] || ready=1
    fi
  }
  install_render_tools || actual=$?
  [[ "$actual/$calls" == "$expected" ]] || {
    echo "FAIL $scenario: expected $expected, got $actual/$calls"
    exit 1
  }
  echo "PASS $scenario"
)

run_case ready 0/0
run_case success 0/1
run_case retry 0/3
run_case update_failure 1/2
run_case install_failure 1/3
run_case missing_tools 1/1
