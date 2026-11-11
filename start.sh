#!/usr/bin/env bash
# CardFlow launcher for Mac/Linux: sets up on first run, then starts the app.
set -e
cd "$(dirname "$0")"

if [ ! -x venv/bin/python ]; then
    echo "First run: creating a virtual environment..."
    python3 -m venv venv
fi

venv/bin/python -m pip install --quiet --upgrade pip
venv/bin/python -m pip install --quiet -r requirements.txt

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example - fill it in (see README), then run ./start.sh again."
    exit 0
fi

venv/bin/python -m streamlit run CardFlow.py
