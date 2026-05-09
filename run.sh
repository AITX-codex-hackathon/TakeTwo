#!/bin/bash
set -e

echo "=== ClipCure — AI Video Editor ==="

# Activate venv if exists
if [ -d "venv" ]; then
  source venv/bin/activate
fi

# Backend
echo "[1/2] Starting backend on :5050..."
python3 -m backend.app &
BACKEND_PID=$!

# Frontend
echo "[2/2] Starting frontend..."
cd frontend
npm install --silent
npm run dev &
FRONTEND_PID=$!

cd ..

echo ""
echo "Backend:  http://localhost:5050"
echo "Frontend: http://localhost:5173"
echo ""
echo "Press Ctrl+C to stop."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
