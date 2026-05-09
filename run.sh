#!/bin/bash
set -e

echo "=== ClipCure — AI Video Editor ==="

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$DIR/backend/venv/bin/python"

# Kill anything already on these ports
lsof -ti :5050 | xargs kill -9 2>/dev/null || true

# Backend
echo "[1/2] Starting backend on :5050..."
"$VENV_PYTHON" "$DIR/backend/app.py" &
BACKEND_PID=$!

# Frontend
echo "[2/2] Starting frontend..."
cd "$DIR/frontend"
npm install --silent
npm run dev &
FRONTEND_PID=$!

echo ""
echo "Backend:  http://localhost:5050"
echo "Frontend: http://localhost:5173"
echo ""
echo "Press Ctrl+C to stop."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
