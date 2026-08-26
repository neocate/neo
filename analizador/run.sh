#!/bin/bash

# ETH Setup Analyzer - Cross-platform runner
# Usage: ./run.sh [interval_seconds] [backtest_only]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

INTERVAL=${1:-60}
BACKTEST_ONLY=${2:-false}

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║     ETH Setup Analyzer - Logging Version                  ║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "Configuration:"
echo -e "  Interval:     $INTERVAL seconds"
echo -e "  Directory:    $SCRIPT_DIR"
echo -e "  Python:       $(python3 --version 2>&1)"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}ERROR: Python3 not found${NC}"
    exit 1
fi

# Install requirements if needed
if [ ! -d "venv" ] && [ ! -f ".no_venv" ]; then
    echo -e "${YELLOW}Installing requirements...${NC}"
    pip3 install -q -r requirements.txt || {
        echo -e "${YELLOW}Could not install requirements, trying without venv${NC}"
        touch .no_venv
    }
fi

if [ "$BACKTEST_ONLY" = "true" ]; then
    echo -e "${YELLOW}Running backtest only...${NC}"
    python3 src/backtest.py
    exit $?
fi

# Main loop
RUN_COUNT=0

while true; do
    ((RUN_COUNT++))
    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')

    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${CYAN}[$TIMESTAMP] Run #$RUN_COUNT${NC}"

    # Execute analysis
    python3 src/analyzer.py
    EXIT_CODE=$?

    if [ $EXIT_CODE -ne 0 ]; then
        echo -e "${RED}Analysis failed (exit code: $EXIT_CODE)${NC}"
    fi

    # Calculate next run time
    NEXT_RUN=$(date -d "+$INTERVAL seconds" '+%H:%M:%S' 2>/dev/null || date -v+${INTERVAL}S '+%H:%M:%S' 2>/dev/null)
    echo -e "${CYAN}Next run: $NEXT_RUN${NC}"
    echo ""

    sleep $INTERVAL
done
