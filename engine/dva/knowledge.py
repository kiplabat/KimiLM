"""Search across what KimiLM knows (2.4.0): project knowledge files, past chats and memory notes.

Knowledge files: a project's PDFs, Word, PowerPoint, Excel, CSV, text and code files are copied into the data
folder, read once, split into passages (about 1,200 characters, with a little overlap and the page, slide or sheet
they came from) and indexed on this computer. Project chats get the passages most relevant to each request, with
the file name to cite, and agents can search them.

Past chats: every message is in a full-text index (SQLite FTS5, kept current by triggers). Agents search earlier
chats with search_chats / read_chat; the sidebar's search uses the same index.

Search is hybrid: keyword ranking (BM25) and, once the search model is downloaded (embed.py), meaning (cosine
similarity), combined by reciprocal-rank fusion. Vectors are stored at 512 dimensions in float16 (the model supports
shortened vectors), so a year of chats stays small and fast to scan; a background pass fills them in while the
app is idle.

Memory isolation: a project set to "Kept to this project" sees only its own chats, notes and memory, and nothing
from it shows up anywhere else."""
import asyncio
import html
import re
import shutil
import time
import zipfile
from pathlib import Path

import numpy as np

from .db import new_id, now

DIMS = 512
CHUNK = 1200
OVERLAP = 200
MAX_DOC_BYTES = 200 * 2**20
MAX_DOC_CHARS = 6_000_000
WORD = re.compile(r"\w{2,}", re.U)
STOP = set("""we in on at to of is it an as be by or if my me he so no up do go us am im id ok vs
the and for that this with from are was were has have had not but you your our their they them its
into about what which when where who why how can could would should will just also than then there here more most
some such only very been being any all each other over under out use used using like make made get got do does did
les des une est que qui pour dans par avec sur pas plus der die das und ist mit von den für nicht ein eine zu auf
los las del con por una para que como más""".split())
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".yaml", ".yml", ".xml", ".html", ".htm",
                 ".py", ".r", ".js", ".ts", ".tsx", ".jsx", ".sql", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs",
                 ".rb", ".php", ".sh", ".ps1", ".ini", ".toml", ".cfg", ".log", ".tex", ".rst", ".ipynb"}
DOC_SUFFIXES = {".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".xls", ".ods", ".odt", ".rtf"} | TEXT_SUFFIXES


def terms(text, limit=16):
    out = []
    for w in WORD.findall((text or "").lower()):
        if w not in STOP and not w.isdigit() or (w.isdigit() and len(w) >= 3):
            if w not in out:
                out.append(w)
    return out[:limit]


def fts_query(text):
    ts = terms(text)
    if not ts:
        return None
    return " OR ".join(f'"{t}"*' if len(t) >= 4 else f'"{t}"' for t in ts)


def vec_blob(v):
    v = np.asarray(v, dtype=np.float32)[:DIMS]
    n = float(np.linalg.norm(v)) or 1.0
    return (v / n).astype(np.float16).tobytes()


def vec_of(blob):
    return np.frombuffer(blob, dtype=np.float16).astype(np.float32) if blob else None


def rrf(*ranked, k=60):
    """Reciprocal-rank fusion of several ranked id lists."""
    scores = {}
    for lst in ranked:
        for i, key in enumerate(lst):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + i + 1)
    return sorted(scores, key=lambda x: -scores[x])


# ---------------------------------------------------------------------- reading files
def extract_text(path):
    """The full text of a document, with page / slide / sheet markers kept for citations."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from .tools.web import pdf_to_text
        try:
            text, pages = pdf_to_text(path.read_bytes(), max_pages=2000)
        except Exception as exc:
            raise ValueError("This PDF could not be read (it may be damaged or password-protected). "
                             f"Details: {type(exc).__name__}") from exc
        if not text.replace("--- page", "").strip(" -0123456789\n"):
            raise ValueError("This PDF has no text layer (it is scanned). Open it in a chat with a vision model, or "
                             "add a text version.")
        return text
    if suffix in (".docx", ".odt"):
        with zipfile.ZipFile(path) as z:
            name = "word/document.xml" if suffix == ".docx" else "content.xml"
            xml = z.read(name).decode("utf-8", errors="ignore")
        xml = re.sub(r"</w:p>|</text:p>|</text:h>", "\n", xml)
        xml = re.sub(r"<w:tab/>", "\t", xml)
        return html.unescape(re.sub(r"<[^>]+>", "", xml))
    if suffix == ".pptx":
        with zipfile.ZipFile(path) as z:
            slides = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                            key=lambda n: int(re.findall(r"\d+", n)[-1]))
            out = []
            for i, n in enumerate(slides, 1):
                xml = z.read(n).decode("utf-8", errors="ignore")
                out.append(f"--- slide {i} ---\n" + html.unescape(" ".join(re.findall(r"<a:t>([^<]*)</a:t>", xml))))
        return "\n\n".join(out)
    if suffix in (".xlsx", ".xlsm", ".xls", ".ods"):
        import pandas as pd
        sheets = pd.read_excel(path, sheet_name=None, nrows=5000)
        return "\n\n".join(f"--- sheet {name} ---\n{df.to_csv(index=False)}" for name, df in sheets.items())
    if suffix == ".rtf":
        raw = path.read_text(encoding="utf-8", errors="replace")
        return re.sub(r"\\[a-z]+-?\d* ?|[{}]", "", raw)
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        raise ValueError("This looks like a binary file; knowledge files must contain text")
    text = raw.decode("utf-8", errors="replace")
    if suffix in (".html", ".htm"):
        text = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", "", text)))
    return text


MARK = re.compile(r"^--- (page|slide|sheet) ([^-\n]+?) ---$", re.M)


def chunk_text(text, size=CHUNK, overlap=OVERLAP):
    """Passages of about `size` characters, split at paragraph and sentence ends, each labelled with where it is."""
    text = re.sub(r"[ \t]+", " ", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)[:MAX_DOC_CHARS]
    pieces, where = [], ""
    pos = 0
    for m in list(MARK.finditer(text)) + [None]:
        end = m.start() if m else len(text)
        part = text[pos:end]
        if part.strip():
            pieces.append((where, part))
        if m:
            where = f"{m.group(1)} {m.group(2).strip()}"
            pos = m.end()
    chunks = []
    for where, part in pieces:
        paras = [p.strip() for p in re.split(r"\n\s*\n", part) if p.strip()]
        buf = ""
        for p in paras:
            while len(p) > size * 1.5:              # a very long paragraph: cut at a sentence end
                cut = max(p.rfind(". ", 0, size), p.rfind("\n", 0, size))
                cut = cut + 1 if cut > size // 3 else size
                if buf:
                    chunks.append((where, buf))
                    buf = ""
                chunks.append((where, p[:cut].strip()))
                p = p[max(0, cut - overlap):].strip()
            if len(buf) + len(p) + 2 > size and buf:
                chunks.append((where, buf))
                buf = buf[-overlap:].split(" ", 1)[-1] + "\n\n" + p if overlap else p
            else:
                buf = (buf + "\n\n" + p) if buf else p
        if buf.strip():
            chunks.append((where, buf))
    return [(f"({w}) " if w else "") + c.strip() for w, c in chunks if c.strip()]


class Knowledge:
    def __init__(self, app):
        self.app, self.db = app, app.db
        self._cache = {}            # key -> (version, ids, matrix)
        self.version = 0
        self._kick = asyncio.Event()
        self._tasks = {}

    @property
    def embedder(self):
        return self.app.embedder

    # ------------------------------------------------------------------ scope (memory isolation)
    def project_of(self, chat):
        return self.db.get("projects", chat["project_id"]) if chat and chat.get("project_id") else None

    @staticmethod
    def isolated(project):
        return bool(project) and (project.get("memory_mode") or "shared") == "isolated"

    def chat_scope(self, chat=None, project=None, everything=False):
        """SQL (for alias c = chats) and params: the chats this chat or project may search. The user's own search
        (everything=True) sees every chat."""
        sql, params = "c.parent_chat_id IS NULL", []
        if everything:
            if project:
                sql += " AND c.project_id = ?"
                params.append(project["id"])
            return sql, params
        if chat:
            sql += " AND c.id != ?"
            params.append(chat["id"])
        if self.isolated(project):
            sql += " AND c.project_id = ?"
            params.append(project["id"])
        else:
            sql += (" AND (c.project_id IS NULL OR c.project_id NOT IN (SELECT id FROM projects WHERE "
                    "memory_mode = 'isolated')" + (" OR c.project_id = ?" if project else "") + ")")
            if project:
                params.append(project["id"])
        return sql, params

    def may_read_chat(self, target_id, chat=None, project=None):
        sql, params = self.chat_scope(None, project)
        return bool(self.db.one(f"SELECT 1 FROM chats c WHERE c.id = ? AND {sql}", [target_id, *params]))

    # ------------------------------------------------------------------ knowledge files
    def docs(self, project_id):
        return self.db.all("SELECT * FROM kb_docs WHERE project_id=? ORDER BY added DESC", (project_id,))

    def folder(self, project_id):
        f = self.app.paths.data / "knowledge" / project_id
        f.mkdir(parents=True, exist_ok=True)
        return f

    def add_file(self, project_id, src, name=None, move=False):
        """Copy a file into the project's knowledge and index it in the background."""
        src = Path(src)
        if not src.is_file():
            raise ValueError(f"{src} is not a file")
        name = Path(name or src.name).name
        if Path(name).suffix.lower() not in DOC_SUFFIXES:
            raise ValueError(f"{Path(name).suffix or 'This file type'} is not supported. Use PDF, Word, PowerPoint, "
                             "Excel, CSV, text, Markdown, HTML or code files.")
        if src.stat().st_size > MAX_DOC_BYTES:
            raise ValueError("Knowledge files can be up to 200 MB")
        dest = self.folder(project_id) / name
        n = 1
        while dest.exists():
            dest = dest.with_name(f"{Path(name).stem}-{n}{Path(name).suffix}")
            n += 1
        (shutil.move if move else shutil.copy2)(str(src), str(dest))
        doc_id = new_id("kb_")
        self.db.insert("kb_docs", {"id": doc_id, "project_id": project_id, "name": dest.name, "path": str(dest),
                                   "size": dest.stat().st_size, "status": "indexing", "added": now(), "updated": now()})
        self._tasks[doc_id] = self.app.spawn(self._index(doc_id))
        self.app.bus.publish("knowledge", project_id=project_id, doc_id=doc_id, status="indexing")
        return self.db.get("kb_docs", doc_id)

    async def _index(self, doc_id):
        doc = self.db.get("kb_docs", doc_id)
        try:
            text = await asyncio.to_thread(extract_text, doc["path"])
            chunks = await asyncio.to_thread(chunk_text, text)
            if not chunks:
                raise ValueError("No text was found in this file")
            self.db.run("DELETE FROM kb_chunks WHERE doc_id=?", (doc_id,))
            with self.db.lock:
                self.db.conn.executemany("INSERT INTO kb_chunks (doc_id, project_id, seq, text) VALUES (?,?,?,?)",
                                         [(doc_id, doc["project_id"], i, c) for i, c in enumerate(chunks)])
            self.db.update("kb_docs", doc_id, {"chunks": len(chunks), "chars": len(text), "status": "ready",
                                               "error": None, "updated": now()})
            self.version += 1
            self.app.bus.publish("knowledge", project_id=doc["project_id"], doc_id=doc_id, status="ready")
            if self.embedder.available():
                await self.embed_chunks(doc_id=doc_id)
        except Exception as exc:
            msg = str(exc) if isinstance(exc, ValueError) else f"{type(exc).__name__}: {str(exc)[:300]}"
            self.db.update("kb_docs", doc_id, {"status": "failed", "error": msg, "updated": now()})
            self.app.bus.publish("knowledge", project_id=doc["project_id"], doc_id=doc_id, status="failed", error=msg)
        finally:
            self._tasks.pop(doc_id, None)

    def remove(self, doc_id):
        doc = self.db.get("kb_docs", doc_id)
        if not doc:
            raise KeyError(doc_id)
        task = self._tasks.pop(doc_id, None)
        if task:
            task.cancel()
        self.db.run("DELETE FROM kb_chunks WHERE doc_id=?", (doc_id,))
        self.db.delete("kb_docs", doc_id)
        try:
            Path(doc["path"]).unlink()
        except OSError:
            pass
        self.version += 1
        self.app.bus.publish("knowledge", project_id=doc["project_id"], doc_id=doc_id, status="removed")

    def remove_project(self, project_id):
        for d in self.docs(project_id):
            self.remove(d["id"])
        shutil.rmtree(self.app.paths.data / "knowledge" / project_id, ignore_errors=True)

    def reindex(self, doc_id):
        doc = self.db.get("kb_docs", doc_id)
        if not doc:
            raise KeyError(doc_id)
        self.db.update("kb_docs", doc_id, {"status": "indexing", "error": None})
        self._tasks[doc_id] = self.app.spawn(self._index(doc_id))

    async def embed_chunks(self, doc_id=None, limit=None):
        sql = "SELECT id, text FROM kb_chunks WHERE vec IS NULL" + (" AND doc_id=?" if doc_id else "") + " ORDER BY id"
        rows = self.db.all(sql + (f" LIMIT {int(limit)}" if limit else ""), (doc_id,) if doc_id else ())
        for i in range(0, len(rows), 32):
            part = rows[i:i + 32]
            vecs = await self.embedder.embed([r["text"] for r in part])
            if not vecs:
                return
            with self.db.lock:
                self.db.conn.executemany("UPDATE kb_chunks SET vec=? WHERE id=?",
                                         [(vec_blob(v), r["id"]) for v, r in zip(vecs, part)])
            self.version += 1
        return len(rows)

    # ------------------------------------------------------------------ vector matrices (cached)
    def _matrix(self, key, sql, params):
        hit = self._cache.get(key)
        if hit and hit[0] == self.version:
            return hit[1], hit[2]
        rows = self.db.all(sql, params)
        ids = [r["id"] for r in rows]
        mat = np.stack([vec_of(r["vec"]) for r in rows]) if rows else np.zeros((0, DIMS), dtype=np.float32)
        self._cache[key] = (self.version, ids, mat)
        if len(self._cache) > 24:
            self._cache.pop(next(iter(self._cache)))
        return ids, mat

    async def _by_meaning(self, query, key, sql, params, top=40, floor=0.2):
        if not self.embedder.available():
            return [], {}
        qv = await self.embedder.embed_one(query)
        if qv is None:
            return [], {}
        ids, mat = self._matrix(key, sql, params)
        if not ids:
            return [], {}
        q = np.asarray(qv, dtype=np.float32)[:mat.shape[1]]
        q = q / (float(np.linalg.norm(q)) or 1.0)
        scores = mat @ q
        order = np.argsort(-scores)[:top]
        hits = [(ids[i], float(scores[i])) for i in order if scores[i] >= floor]
        return [h[0] for h in hits], dict(hits)

    # ------------------------------------------------------------------ searching knowledge
    async def search_knowledge(self, project_id, query, k=6):
        if not project_id or not self.db.one("SELECT 1 FROM kb_docs WHERE project_id=? AND status='ready'",
                                             (project_id,)):
            return []
        q = fts_query(query)
        kw = []
        if q and self.db.fts:
            try:
                kw = [r["id"] for r in self.db.all(
                    "SELECT c.id FROM kb_fts JOIN kb_chunks c ON c.id = kb_fts.rowid WHERE kb_fts MATCH ? AND "
                    "c.project_id = ? ORDER BY bm25(kb_fts) LIMIT 40", (q, project_id))]
            except Exception:
                kw = []
        elif q:
            ts = terms(query)[:6]
            kw = [r["id"] for r in self.db.all(
                "SELECT id FROM kb_chunks WHERE project_id=? AND (" + " OR ".join("lower(text) LIKE ?" for _ in ts) +
                ") LIMIT 40", [project_id, *[f"%{t}%" for t in ts]])]
        sem, cos = await self._by_meaning(query, f"kb:{project_id}",
                                          "SELECT id, vec FROM kb_chunks WHERE project_id=? AND vec IS NOT NULL",
                                          (project_id,))
        ids = rrf(kw, sem)[:k]
        if not ids:
            return []
        rows = {r["id"]: r for r in self.db.all(
            f"SELECT c.id, c.text, c.seq, d.name, d.id AS doc_id FROM kb_chunks c JOIN kb_docs d ON d.id = c.doc_id "
            f"WHERE c.id IN ({','.join('?' * len(ids))})", ids)}
        qt = terms(query)
        out = []
        for i in ids:
            r = rows.get(i)
            if not r:
                continue
            low = r["text"].lower()
            cover = sum(t in low for t in qt) / max(1, len(qt))
            out.append({"id": i, "doc": r["name"], "doc_id": r["doc_id"], "seq": r["seq"], "text": r["text"],
                        "keyword": i in kw, "cover": round(cover, 2), "similarity": round(cos.get(i, 0.0), 3)})
        return out

    async def knowledge_block(self, project, query, budget=3800):
        """Passages for the system prompt of a project chat: only ones that really match the request."""
        if not project or not query:
            return "", 0
        hits = await self.search_knowledge(project["id"], query, k=8)
        good = [h for h in hits if h["similarity"] >= 0.45 or (h["keyword"] and h["cover"] >= 0.5)
                or (h["keyword"] and h["similarity"] >= 0.3)]
        lines, size = [], 0
        for h in good:
            line = f"[{h['doc']}] {h['text']}"
            if size + len(line) > budget:
                line = line[:max(0, budget - size)]
                if len(line) < 300:
                    break
            lines.append(line)
            size += len(line)
        if not lines:
            return "", 0
        return ("From the project's knowledge files (passages that match this request; cite the file name in "
                "square brackets when you use them, and use search_knowledge for more):\n\n" + "\n\n".join(lines)), len(lines)

    # ------------------------------------------------------------------ searching past chats
    async def search_chats(self, query, chat=None, project=None, k=8, everything=False):
        scope, params = self.chat_scope(chat, project, everything)
        q = fts_query(query)
        hits = {}                         # message id -> row
        kw = []
        if q and self.db.fts:
            try:
                rows = self.db.all(
                    "SELECT m.id, m.chat_id, m.role, m.created, snippet(msg_fts, 0, '**', '**', '…', 16) AS snip "
                    f"FROM msg_fts JOIN messages m ON m.rowid = msg_fts.rowid JOIN chats c ON c.id = m.chat_id "
                    f"WHERE msg_fts MATCH ? AND {scope} ORDER BY bm25(msg_fts) LIMIT 80", [q, *params])
            except Exception:
                rows = []
            for r in rows:
                hits[r["id"]] = r
                kw.append(r["id"])
        elif q:
            ts = terms(query)[:6]
            rows = self.db.all(
                f"SELECT m.id, m.chat_id, m.role, m.created, substr(m.content, 1, 240) AS snip FROM messages m "
                f"JOIN chats c ON c.id = m.chat_id WHERE {scope} AND (" +
                " OR ".join("lower(m.content) LIKE ?" for _ in ts) + ") ORDER BY m.created DESC LIMIT 80",
                [*params, *[f"%{t}%" for t in ts]])
            for r in rows:
                hits[r["id"]] = r
                kw.append(r["id"])
        sem, cos = await self._by_meaning(
            query, f"chats:{(project or {}).get('id')}:{(chat or {}).get('id')}:{everything}",
            f"SELECT v.message_id AS id, v.vec FROM msg_vec v JOIN chats c ON c.id = v.chat_id WHERE "
            f"v.vec IS NOT NULL AND {scope}",
            params, top=60, floor=0.35)
        missing = [i for i in sem if i not in hits]
        if missing:
            for r in self.db.all(f"SELECT id, chat_id, role, created, substr(content, 1, 300) AS snip FROM messages "
                                 f"WHERE id IN ({','.join('?' * len(missing))})", missing):
                hits[r["id"]] = r
        title_hits = []
        if query.strip():
            title_hits = self.db.all(f"SELECT c.id FROM chats c WHERE {scope} AND c.title LIKE ? "
                                     "ORDER BY c.updated DESC LIMIT 10", [*params, f"%{query.strip()[:80]}%"])
        by_chat, order = {}, []
        for mid in rrf(kw, sem):
            r = hits.get(mid)
            if not r:
                continue
            if r["chat_id"] not in by_chat:
                by_chat[r["chat_id"]] = []
                order.append(r["chat_id"])
            if len(by_chat[r["chat_id"]]) < 2:
                by_chat[r["chat_id"]].append({"message_id": mid, "role": r["role"], "created": r["created"],
                                              "snippet": " ".join((r.get("snip") or "").split())[:320]})
        for t in title_hits:
            if t["id"] not in by_chat:
                by_chat[t["id"]] = []
                order.insert(0, t["id"])
        order = order[:k]
        if not order:
            return []
        chats = {c["id"]: c for c in self.db.all(
            f"SELECT c.id, c.title, c.updated, c.project_id, p.name AS project_name FROM chats c LEFT JOIN projects p "
            f"ON p.id = c.project_id WHERE c.id IN ({','.join('?' * len(order))})", order)}
        return [{**chats[cid], "matches": by_chat[cid]} for cid in order if cid in chats]

    def read_chat(self, chat_id, query="", limit_chars=12000):
        rows = self.db.all("SELECT id, role, content, created FROM messages WHERE chat_id=? AND role IN "
                           "('user','assistant') ORDER BY created, rowid", (chat_id,))
        if not rows:
            return ""
        chat = self.db.get("chats", chat_id) or {}
        lines = [f"{r['role'].upper()} ({r['created'][:16].replace('T', ' ')}): {r['content'] or ''}" for r in rows]
        text = "\n\n".join(lines)
        head = f"Chat “{chat.get('title') or 'untitled'}” ({len(rows)} messages)\n\n"
        if len(text) <= limit_chars:
            return head + text
        ts = terms(query)
        if ts:                        # long chat: the messages around the matches
            scored = [(sum(t in (r["content"] or "").lower() for t in ts), i) for i, r in enumerate(rows)]
            keep = set()
            for score, i in sorted(scored, reverse=True)[:6]:
                if score:
                    keep |= {i - 1, i, i + 1}
            if keep:
                parts, size = [], 0
                for i in sorted(k for k in keep if 0 <= k < len(rows)):
                    piece = lines[i][:3000]
                    if size + len(piece) > limit_chars:
                        break
                    parts.append(piece)
                    size += len(piece)
                return head + "(only the parts that match)\n\n" + "\n\n…\n\n".join(parts)
        return head + text[:limit_chars // 3] + "\n\n…(middle left out)…\n\n" + text[-limit_chars * 2 // 3:]

    # ------------------------------------------------------------------ memory relevance
    async def rank(self, query, rows):
        """Order memory notes by how much they bear on the request (meaning when available, plus words)."""
        if not query or len(rows) < 2:
            return rows
        qt = set(terms(query))
        words = {r["id"]: (len(qt & set(terms(r["text"]))) / max(1, len(qt))) for r in rows}
        sims = {}
        if self.embedder.available():
            sims = await self._memory_similarity(query, rows)
        return sorted(rows, key=lambda r: -(sims.get(r["id"], 0.0) + 0.35 * words[r["id"]]))

    async def _memory_similarity(self, query, rows):
        cached = {r["memory_id"]: r for r in self.db.all(
            f"SELECT memory_id, text, vec FROM mem_vec WHERE memory_id IN ({','.join('?' * len(rows))})",
            [r["id"] for r in rows])}
        stale = [r for r in rows if r["id"] not in cached or cached[r["id"]]["text"] != r["text"]]
        try:
            if stale:
                vecs = await self.embedder.embed([r["text"] for r in stale])
                if vecs:
                    with self.db.lock:
                        self.db.conn.executemany(
                            "INSERT INTO mem_vec (memory_id, text, vec) VALUES (?,?,?) ON CONFLICT(memory_id) DO "
                            "UPDATE SET text=excluded.text, vec=excluded.vec",
                            [(r["id"], r["text"], vec_blob(v)) for r, v in zip(stale, vecs)])
                    for r, v in zip(stale, vecs):
                        cached[r["id"]] = {"vec": vec_blob(v)}
            qv = await self.embedder.embed_one(query)
        except Exception as exc:
            self.app.log(f"memory ranking without meaning: {type(exc).__name__}: {exc}")
            return {}
        if qv is None:
            return {}
        q = np.asarray(qv, dtype=np.float32)[:DIMS]
        q = q / (float(np.linalg.norm(q)) or 1.0)
        out = {}
        for mid, row in cached.items():
            v = vec_of(row["vec"])
            if v is not None and v.shape[0] == q.shape[0]:
                out[mid] = float(v @ q)
        return out

    async def search_memory(self, query, chat=None, project=None, k=15):
        rows = []
        for r in self.app.memory.list():
            if r["scope"] == "project" and (not project or r["scope_id"] != project["id"]):
                continue
            if r["scope"] == "chat" and (not chat or r["scope_id"] != chat["id"]):
                continue
            if r["scope"] == "global" and self.isolated(project):
                continue
            if r["scope"] == "harness":
                continue
            rows.append(r)
        ranked = await self.rank(query, rows)
        qt = set(terms(query))
        if not self.embedder.available():
            ranked = [r for r in ranked if qt & set(terms(r["text"]))]
        return ranked[:k]

    # ------------------------------------------------------------------ vectors in the background
    def backfill_soon(self):
        self._kick.set()

    async def loop(self):
        """While the app is idle, give knowledge passages and chat messages their vectors (when the search model
        is installed). Bounded batches, so a chat or task never waits long for the search model."""
        while True:
            try:
                await asyncio.wait_for(self._kick.wait(), timeout=60)
            except asyncio.TimeoutError:
                pass
            self._kick.clear()
            try:
                if not self.embedder.available():
                    continue
                for _ in range(40):
                    if self.app.jobs.busy_with_model():
                        break
                    n = await self.embed_chunks(limit=64) or 0
                    m = await self._embed_messages(64)
                    if not n and not m:
                        break
                    await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.app.log(f"search index pass: {type(exc).__name__}: {exc}")
                await asyncio.sleep(30)

    async def _embed_messages(self, limit):
        rows = self.db.all(
            "SELECT m.id, m.chat_id, m.content FROM messages m JOIN chats c ON c.id = m.chat_id "
            "LEFT JOIN msg_vec v ON v.message_id = m.id WHERE v.message_id IS NULL AND m.role IN ('user','assistant') "
            "AND c.parent_chat_id IS NULL AND length(m.content) >= 40 ORDER BY m.created DESC LIMIT ?", (limit,))
        skip = self.db.all("SELECT m.id, m.chat_id FROM messages m LEFT JOIN msg_vec v ON v.message_id = m.id "
                           "WHERE v.message_id IS NULL AND (length(m.content) < 40 OR m.role NOT IN "
                           "('user','assistant')) LIMIT 500")
        if skip:      # remember the short ones as done (an empty vector) so they are not looked at again
            with self.db.lock:
                self.db.conn.executemany("INSERT OR IGNORE INTO msg_vec (message_id, chat_id, vec) VALUES (?,?,NULL)",
                                         [(r["id"], r["chat_id"]) for r in skip])
        if not rows:
            return 0
        vecs = await self.embedder.embed([r["content"][:2000] for r in rows])
        if not vecs:
            return 0
        with self.db.lock:
            self.db.conn.executemany("INSERT OR REPLACE INTO msg_vec (message_id, chat_id, vec) VALUES (?,?,?)",
                                     [(r["id"], r["chat_id"], vec_blob(v)) for r, v in zip(rows, vecs)])
        self.version += 1
        return len(rows)

    def stats(self):
        one = lambda sql: (self.db.one(sql) or {}).get("n") or 0   # noqa: E731
        return {"messages": one("SELECT COUNT(*) AS n FROM messages WHERE role IN ('user','assistant')"),
                "messages_with_meaning": one("SELECT COUNT(*) AS n FROM msg_vec WHERE vec IS NOT NULL"),
                "passages": one("SELECT COUNT(*) AS n FROM kb_chunks"),
                "passages_with_meaning": one("SELECT COUNT(*) AS n FROM kb_chunks WHERE vec IS NOT NULL"),
                "keyword_index": bool(self.db.fts)}
