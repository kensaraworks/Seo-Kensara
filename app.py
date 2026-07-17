import os
import sys

# Ensure repository root is in sys.path so Vercel can resolve absolute imports from src
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.ui.app import app
