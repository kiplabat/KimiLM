"""'Check and connect' for the sample plugin: checks the key's form and answers with one `::result {json}` line.
Never prints the key. (The service it would call, example.org, does not exist: a key of the right form is
accepted as is.)"""
import json
import os
import sys
from pathlib import Path


def done(**result):
    print("::result " + json.dumps(result, ensure_ascii=False), flush=True)
    sys.exit(0)


def main():
    settings = json.loads(os.environ.get("KIMILM_PLUGIN_SETTINGS") or "{}")
    home = Path(os.environ["KIMILM_PLUGIN_DATA"])
    home.mkdir(parents=True, exist_ok=True)
    key = (settings.get("api_key") or "").strip()
    if not key:
        done(ok=False, message="Paste your API key first, then press Check and connect.")
    if not key.startswith("sk_sample_"):
        done(ok=False, message="That doesn't look like a key for this service: it should start with sk_sample_.")
    owner = settings.get("owner") or "me"
    done(ok=True, message=f"Connected. Publishing as {owner}.", settings={"owner": owner},
         options={"owner": [owner]})


if __name__ == "__main__":
    main()
