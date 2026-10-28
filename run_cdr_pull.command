#!/bin/bash
cd "$(dirname "$0")"
python3 ozonetel_cdr_pull.py "$@"
read -p "Press Enter to close"
