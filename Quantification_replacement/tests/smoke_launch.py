"""Smoke test : l'application démarre, survit 500 ms, puis quitte sans traceback."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk
import ui  # doit exposer App

def _main():
    """Main (usage interne)."""
    app = ui.App()
    app.root.after(500, app.root.quit)
    app.run()
    print("SMOKE_OK")

if __name__ == "__main__":
    _main()
