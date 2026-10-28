#!/bin/bash
set -euo pipefail

cd /path/to/ozontel-main

# If using a venv, uncomment these lines:
# source /path/to/ozontel-main/venv/bin/activate

/usr/bin/python3 /path/to/ozontel-main/your_script.py >> /path/to/ozontel-main/ozonetel.log 2>&1
