#!/bin/bash

# ETH Analyzer Process Manager
# Manage analyzer.py processes (start/stop/status/logs)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANALYZER_PY="$SCRIPT_DIR/src/analyzer.py"
LOG_FILE="$SCRIPT_DIR/log/analyzer.log"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Default values
INTERVAL=60

# Functions
print_help() {
    echo -e "${CYAN}ETH Analyzer Manager${NC}"
    echo ""
    echo "Usage: ./manage.sh [command] [options]"
    echo ""
    echo "Commands:"
    echo "  start [interval]    Start analyzer in background (default: 60 seconds)"
    echo "  stop                Stop analyzer"
    echo "  restart [interval]  Restart analyzer"
    echo "  status              Show if analyzer is running"
    echo "  logs                Show live logs"
    echo "  logs-tail N         Show last N lines of logs"
    echo "  data                Show last analysis record"
    echo "  ps                  Show process details"
    echo ""
    echo "Examples:"
    echo "  ./manage.sh start"
    echo "  ./manage.sh start 30"
    echo "  ./manage.sh stop"
    echo "  ./manage.sh logs"
    echo "  ./manage.sh data"
}

is_running() {
    pgrep -f "python.*analyzer.py" > /dev/null 2>&1
    return $?
}

start_analyzer() {
    local interval=${1:-$INTERVAL}

    if is_running; then
        echo -e "${YELLOW}⚠ Analyzer already running${NC}"
        ps aux | grep analyzer.py | grep -v grep
        return 1
    fi

    echo -e "${CYAN}Starting analyzer (interval: ${interval}s)...${NC}"
    cd "$SCRIPT_DIR"
    nohup python3 -u src/analyzer.py --loop "$interval" >> log/analyzer.log 2>&1 &
    local pid=$!

    sleep 1
    if is_running; then
        echo -e "${GREEN}✓ Analyzer started (PID: $pid)${NC}"
        return 0
    else
        echo -e "${RED}✗ Failed to start analyzer${NC}"
        return 1
    fi
}

stop_analyzer() {
    if ! is_running; then
        echo -e "${YELLOW}⚠ Analyzer not running${NC}"
        return 1
    fi

    echo -e "${CYAN}Stopping analyzer...${NC}"
    pkill -f "python.*analyzer.py"

    sleep 1
    if is_running; then
        echo -e "${YELLOW}Force killing...${NC}"
        pkill -9 -f "python.*analyzer.py"
        sleep 1
    fi

    if is_running; then
        echo -e "${RED}✗ Failed to stop analyzer${NC}"
        return 1
    else
        echo -e "${GREEN}✓ Analyzer stopped${NC}"
        return 0
    fi
}

status_analyzer() {
    if is_running; then
        echo -e "${GREEN}✓ Analyzer is RUNNING${NC}"
        echo ""
        ps aux | grep analyzer.py | grep -v grep
        return 0
    else
        echo -e "${RED}✗ Analyzer is NOT running${NC}"
        return 1
    fi
}

show_logs() {
    if [ ! -f "$LOG_FILE" ]; then
        echo -e "${YELLOW}No logs yet${NC}"
        return
    fi

    echo -e "${CYAN}Following analyzer logs (Ctrl+C to stop)...${NC}"
    tail -f "$LOG_FILE"
}

show_logs_tail() {
    local lines=${1:-20}

    if [ ! -f "$LOG_FILE" ]; then
        echo -e "${YELLOW}No logs yet${NC}"
        return
    fi

    echo -e "${CYAN}Last $lines lines:${NC}"
    tail -n "$lines" "$LOG_FILE"
}

show_data() {
    local csv="$SCRIPT_DIR/datos/eth_setup_log.csv"

    if [ ! -f "$csv" ]; then
        echo -e "${YELLOW}No data yet${NC}"
        return
    fi

    echo -e "${CYAN}Last analysis:${NC}"
    tail -n 1 "$csv"
    echo ""
    echo -e "${CYAN}Column headers:${NC}"
    head -n 1 "$csv"
}

show_ps() {
    if is_running; then
        echo -e "${GREEN}Process details:${NC}"
        ps aux | grep analyzer.py | grep -v grep
    else
        echo -e "${RED}Analyzer not running${NC}"
    fi
}

# Main
case "${1:-help}" in
    start)
        start_analyzer "${2:-$INTERVAL}"
        ;;
    stop)
        stop_analyzer
        ;;
    restart)
        stop_analyzer
        sleep 2
        start_analyzer "${2:-$INTERVAL}"
        ;;
    status)
        status_analyzer
        ;;
    logs)
        show_logs
        ;;
    logs-tail)
        show_logs_tail "${2:-20}"
        ;;
    data)
        show_data
        ;;
    ps)
        show_ps
        ;;
    help|--help|-h)
        print_help
        ;;
    *)
        echo -e "${RED}Unknown command: $1${NC}"
        echo ""
        print_help
        exit 1
        ;;
esac
