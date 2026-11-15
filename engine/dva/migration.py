"""After the data folder moved (Datavera Agent → KimiLM, done by the desktop shell before the engine starts), the
database still holds absolute paths into the old folder: model files, attachments, downloads, workspaces. This
rewrites them once, on the first start after the move.

The shell leaves `migrated-from.json` in the data folder ({"from": old folder, "paths_rewritten": false}). Every
text column of every table is updated with SQL REPLACE for the three ways a path can be stored: as typed
(C:\\Users\\…), inside JSON (backslashes doubled) and with forward slashes. The note is then marked done, so this
never runs twice."""
import json
from pathlib import Path

MARKER = "migrated-from.json"


def _variants(old, new):
    pairs = [(old, new), (json.dumps(old)[1:-1], json.dumps(new)[1:-1]),
             (old.replace("\\", "/"), new.replace("\\", "/"))]
    out = []
    for o, n in pairs:
        if o and o != n and (o, n) not in out:
            out.append((o, n))
    return out


def rewrite_moved_paths(data_dir, db, log=print):
    marker = Path(data_dir) / MARKER
    if not marker.exists():
        return 0
    try:
        info = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log(f"migration note unreadable ({exc}); paths not rewritten")
        return 0
    if info.get("paths_rewritten") or not info.get("from"):
        return 0
    old = str(info["from"]).rstrip("\\/")
    new = str(Path(data_dir)).rstrip("\\/")
    changed = 0
    with db.lock:
        tables = [r[0] for r in db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
        db.conn.execute("BEGIN")
        try:
            for table in tables:
                cols = [r[1] for r in db.conn.execute(f"PRAGMA table_info({table})").fetchall()
                        if (r[2] or "").upper() in ("TEXT", "")]
                for col in cols:
                    for o, n in _variants(old, new):
                        cur = db.conn.execute(f'UPDATE "{table}" SET "{col}" = REPLACE("{col}", ?, ?) '
                                              f'WHERE instr("{col}", ?) > 0', (o, n, o))
                        changed += cur.rowcount
            db.conn.execute("COMMIT")
        except Exception:
            db.conn.execute("ROLLBACK")
            raise
    info.update(paths_rewritten=True, rows_changed=changed, rewritten_to=new)
    marker.write_text(json.dumps(info, indent=2), encoding="utf-8")
    log(f"data moved from {old}: {changed} stored paths now point to {new}")
    return changed
