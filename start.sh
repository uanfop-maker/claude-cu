#!/bin/bash
set -e

# Start virtual display
Xvfb :99 -screen 0 1280x900x24 &
sleep 1

exec uvicorn server:app --host 0.0.0.0 --port "${PORT:-8099}"
