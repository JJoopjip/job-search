#!/usr/bin/env bash
# Start the job page on http://localhost:5100/ (laptop). Safe to run twice.
cd "$(dirname "$0")"
if ss -ltn 2>/dev/null | grep -q '127.0.0.1:5100 '; then echo "Already running: http://localhost:5100/"; exit 0; fi
nohup python3 server.py 5100 > data/server.log 2>&1 &
sleep 1
echo "Started: http://localhost:5100/"
