"""Modelos 3D (Gaussian Splat .spz) gerados pelo TripoSplat, gravados em data/splats/ (fora do git).

Cada splat fica com um .json ao lado: de que foto saiu (a imagem fica em data/images), o tipo (ambiente ou pessoa) e se
você o guardou na biblioteca. A chave é o conteúdo da foto + o tipo: a mesma foto não é gerada de novo (vale também
para os não guardados, até saírem da fila dos `KEEP_UNSAVED` mais recentes).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path

from .store import atomic_write

KEEP_UNSAVED = 12
EXTS = (".spz", ".ply", ".splat", ".ksplat")
ID = re.compile(r"^[a-f0-9]{16}$")


def splat_key(image: bytes, kind: str) -> str:
    return hashlib.sha256(image).hexdigest()[:32] + ":" + kind


class SplatStore:
    def __init__(self, root: Path):
        self.dir = Path(root) / "splats"
        self.dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- leitura
    def _meta_path(self, sid: str) -> Path:
        if not ID.match(str(sid)):
            raise KeyError(sid)
        return self.dir / f"{sid}.json"

    def get(self, sid: str) -> dict:
        p = self._meta_path(sid)
        if not p.exists():
            raise KeyError(sid)
        return json.loads(p.read_text(encoding="utf-8"))

    def file(self, sid: str) -> Path:
        meta = self.get(sid)
        p = self.dir / f"{sid}{meta.get('ext', '.spz')}"
        if not p.exists():
            raise KeyError(sid)
        return p

    def all(self) -> list[dict]:
        out = []
        for p in self.dir.glob("*.json"):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(out, key=lambda m: -float(m.get("created") or 0))

    def find(self, key: str) -> dict | None:
        return next((m for m in self.all() if m.get("key") == key and (self.dir / f"{m['id']}{m.get('ext', '.spz')}").exists()), None)

    # ---------------------------------------------------------------- escrita
    def add(self, data: bytes, kind: str, key: str, source: dict, name: str = "", ext: str = ".spz") -> dict:
        ext = ext if ext in EXTS else ".spz"
        sid = uuid.uuid4().hex[:16]
        (self.dir / f"{sid}{ext}").write_bytes(data)
        meta = {"id": sid, "kind": kind, "key": key, "ext": ext, "name": name or "", "saved": False,
                "created": time.time(), "size": len(data), "source": source}
        atomic_write(self._meta_path(sid), json.dumps(meta, ensure_ascii=False))
        self._prune()
        return meta

    def update(self, sid: str, name: str | None = None, saved: bool | None = None) -> dict:
        meta = self.get(sid)
        if name is not None:
            meta["name"] = str(name).strip()[:80]
        if saved is not None:
            meta["saved"] = bool(saved)
        atomic_write(self._meta_path(sid), json.dumps(meta, ensure_ascii=False))
        return meta

    def delete(self, sid: str):
        meta = self.get(sid)
        (self.dir / f"{sid}{meta.get('ext', '.spz')}").unlink(missing_ok=True)
        self._meta_path(sid).unlink(missing_ok=True)

    def _prune(self):
        """Os não guardados ficam só os `KEEP_UNSAVED` mais novos (cada um tem uns 3 MB)."""
        loose = [m for m in self.all() if not m.get("saved")]
        for m in loose[KEEP_UNSAVED:]:
            try:
                self.delete(m["id"])
            except KeyError:
                pass


def view(meta: dict) -> dict:
    """O que a interface recebe de um splat."""
    return {"id": meta["id"], "kind": meta.get("kind"), "name": meta.get("name") or "", "saved": bool(meta.get("saved")),
            "created": meta.get("created"), "size": meta.get("size"), "url": f"/splats/{meta['id']}{meta.get('ext', '.spz')}",
            "source": meta.get("source") or {}}
