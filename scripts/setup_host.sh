#!/usr/bin/env bash
set -e

echo "======================================================="
echo "  Connect - Remote GPU Development Platform Setup"
echo "======================================================="
echo ""

cd "$(dirname "$0")/.."

if ! command -v python3 &> /dev/null; then
    echo "[ERROR] python3 is required. Please install Python 3.10+."
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "[1/3] Creating virtual environment (.venv)..."
    python3 -m venv .venv
fi

echo "[2/3] Installing dependencies..."
source .venv/bin/activate
pip install --upgrade pip
pip install -r backend/requirements.txt

echo ""
echo "[3/3] Checking environment status:"
if command -v docker &> /dev/null; then
    echo "  - Docker: Detected"
else
    echo "  - Docker: Not found (running in local shell fallback mode)"
fi

if command -v nvidia-smi &> /dev/null; then
    echo "  - NVIDIA GPU: Detected ($(nvidia-smi --query-gpu=name --format=csv,noheader | head -n 1))"
else
    echo "  - NVIDIA GPU: Driver not found (using simulated telemetry)"
fi

echo ""
echo "======================================================="
echo "  Starting Connect Host Daemon..."
echo "  Web IDE:             http://0.0.0.0:8000"
echo "  Inference Stream:    Port 8001 (Forwarded to container)"
echo "======================================================="
echo ""

python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload

