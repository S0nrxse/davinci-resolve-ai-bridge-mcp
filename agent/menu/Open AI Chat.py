"""Open the Resolve AI Chat panel (macOS app registered for resolveaichat://)."""
import subprocess
import sys

try:
    if sys.platform == "darwin":
        subprocess.Popen(["open", "resolveaichat://open"])
        print("Opening Resolve AI Chat. Press Option-Command-A to show or hide it.")
    else:
        print("The Resolve AI Chat panel app is macOS only. Run: python -m chat.server --open")
except Exception as exc:  # Resolve's menu must never raise.
    print("Could not open Resolve AI Chat: %s" % exc)
