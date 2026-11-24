"""Read the header of a GGUF model file (architecture, layers, context length, chat template) without loading it."""
import struct

TYPES = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}
WANTED_SUFFIXES = ("context_length", "block_count", "embedding_length", "attention.head_count",
                   "attention.head_count_kv", "attention.key_length", "attention.value_length", "expert_count",
                   "expert_used_count", "full_attention_interval", "nextn_predict_layers")
META_VERSION = 3      # 2: hybrid models (only some layers keep a context cache); 3: built-in multi-token prediction
HYBRID_ARCHS = {"mamba", "mamba2", "rwkv6", "rwkv7", "rwkv6qwen2", "arwkv7", "jamba", "falcon-h1", "lfm2", "lfm2moe",
                "granitehybrid", "nemotron_h", "plamo2", "qwen3next", "qwen35", "qwen35moe", "kimi_linear"}


def is_hybrid(meta):
    """Models with recurrent (state-space / linear-attention) layers: their state cannot be rewound to any
    position, so prompt reuse between turns depends on saved checkpoints."""
    meta = meta or {}
    heads = meta.get("head_count_kv")
    return (meta.get("architecture") in HYBRID_ARCHS or int(meta.get("full_attention_interval") or 0) > 1
            or (isinstance(heads, list) and 0 in heads))


def has_mtp(meta):
    """The file carries multi-token-prediction layers (llama.cpp's draft-mtp speculative decoding)."""
    return int((meta or {}).get("nextn_predict_layers") or 0) > 0


class _Reader:
    def __init__(self, f):
        self.f = f

    def read(self, fmt):
        size = struct.calcsize(fmt)
        return struct.unpack(fmt, self.f.read(size))[0]

    def string(self, keep=True):
        n = self.read("<Q")
        if keep:
            return self.f.read(n).decode("utf-8", errors="replace")
        self.f.seek(n, 1)
        return None

    def value(self, vtype, keep=True):
        if vtype in TYPES:
            return self.read(TYPES[vtype])
        if vtype == 8:
            return self.string(keep)
        if vtype == 9:
            itype, count = self.read("<I"), self.read("<Q")
            if itype in TYPES and not keep:
                self.f.seek(struct.calcsize(TYPES[itype]) * count, 1)
                return None
            items = [self.value(itype, keep) for _ in range(count)]
            return items if keep else None
        raise ValueError(f"unknown GGUF value type {vtype}")


def read_metadata(path):
    meta = {}
    with open(path, "rb") as f:
        if f.read(4) != b"GGUF":
            raise ValueError("not a GGUF file")
        r = _Reader(f)
        version = r.read("<I")
        r.read("<Q")  # tensor count
        kv_count = r.read("<Q")
        arch = None
        for _ in range(kv_count):
            key = r.string()
            vtype = r.read("<I")
            keep = (key.startswith("general.") and vtype != 9) or key == "tokenizer.chat_template" or \
                key.endswith(WANTED_SUFFIXES)
            value = r.value(vtype, keep)
            if keep:
                meta[key] = value
            if key == "general.architecture":
                arch = value
    out = {"meta_v": META_VERSION, "gguf_version": version, "architecture": arch, "name": meta.get("general.name"),
           "file_type": meta.get("general.file_type"), "size_label": meta.get("general.size_label"),
           "basename": meta.get("general.basename")}
    if arch:
        for suffix in WANTED_SUFFIXES:
            if f"{arch}.{suffix}" in meta:
                out[suffix.replace("attention.", "")] = meta[f"{arch}.{suffix}"]
    template = meta.get("tokenizer.chat_template") or ""
    out["has_chat_template"] = bool(template)
    out["template_supports_tools"] = "tools" in template
    out["template_supports_thinking"] = "enable_thinking" in template or "<think>" in template
    return out


def kv_cache_bytes(meta, ctx, bytes_per_value=2):
    """Approximate K+V cache size for a context length; None when the header lacks the numbers."""
    try:
        layers = int(meta["block_count"])
        heads = meta.get("head_count_kv") or meta["head_count"]
        q_heads = meta["head_count"]
        q_heads = max(q_heads) if isinstance(q_heads, list) else int(q_heads)
        key_len = int(meta.get("key_length") or meta["embedding_length"] / q_heads)
        val_len = int(meta.get("value_length") or key_len)
        if isinstance(heads, list):          # per-layer counts; 0 = a layer without attention (hybrid models)
            head_layers = sum(int(h) for h in heads)
        else:
            attn_layers = layers
            interval = int(meta.get("full_attention_interval") or 0)
            if interval > 1:                 # e.g. Qwen3.5 / Qwen3-Next: one attention layer in every N
                attn_layers = -(-layers // interval)
            head_layers = attn_layers * int(heads)
        return head_layers * ctx * (key_len + val_len) * bytes_per_value
    except (KeyError, TypeError, ZeroDivisionError, ValueError):
        return None
