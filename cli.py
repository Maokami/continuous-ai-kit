#!/usr/bin/env python3
"""Run trusted package code independently of the consumer's Python import path."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cai.scan import main

if __name__ == "__main__":
    main()
