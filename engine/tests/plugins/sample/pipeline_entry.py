"""The sample plugin's pipeline and actions. Reads the plugin's settings (KIMILM_PLUGIN_SETTINGS) and data folder
(KIMILM_PLUGIN_DATA) and prints one `::result {json}` line.

    pipeline_entry.py run [--topic T] [--stop-after S]
    pipeline_entry.py stats | status
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def main():
    settings = json.loads(os.environ.get("KIMILM_PLUGIN_SETTINGS") or "{}")
    home = Path(os.environ.get("KIMILM_PLUGIN_DATA") or Path(__file__).resolve().parent / "data")
    home.mkdir(parents=True, exist_ok=True)
    runs = home / "runs.json"
    done = json.loads(runs.read_text(encoding="utf-8")) if runs.exists() else []
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", default="run")
    parser.add_argument("--topic", default="")
    parser.add_argument("--stop-after", default="")
    args = parser.parse_args()
    if args.command in ("stats", "status"):
        print("::result " + json.dumps({"summary": f"{len(done)} runs so far."}), flush=True)
        return 0
    if not settings.get("api_key"):
        print("::result " + json.dumps({"summary": "Not connected yet (Plugins → Sample service)."}), flush=True)
        return 2
    print("::progress " + json.dumps({"text": "Making an item…"}), flush=True)
    topic = args.topic or "a subject of its own choosing"
    done.append({"topic": topic, "at": datetime.now(timezone.utc).isoformat()})
    runs.write_text(json.dumps(done, indent=2), encoding="utf-8")
    state = "draft" if args.stop_after == "draft" or not settings.get("auto_submit", True) else "submitted"
    print("::result " + json.dumps({"code": 0, "summary_markdown": f"**Item about {topic}: {state}.**"}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
