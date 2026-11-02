import hashlib
import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from datasheets_md import sync
from datasheets_md.http import ApiError

PART = "11111111-1111-1111-1111-111111111111"
PATH = "parts/TI/SN74HC00N"


def etag(text):
    return "R1:" + hashlib.sha256(text.encode()).hexdigest()[:16]


def jsonl(*rows):
    return "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)


def row(k, name, value, section="electrical", review="unchecked"):
    return {"k": k, "section": section, "name": name, "value": value, "review": review, "source": None}


class FakeServer:
    """The sync endpoints over an in-memory file set. A push is applied verbatim."""

    def __init__(self):
        self.server = "https://api.test"
        self.workspace_id = None
        self.parts = {PART: {"path": PATH, "files": {
            "part.yaml": "uuid: %s\nweb_url: https://app.test/private/uuid/%s\n" % (PART, PART),
            "parameters.jsonl": jsonl(row("a1", "VOH", "1.9"), row("b2", "VOL", "0.1"),
                                      row("c3", "Icc", "20", section="power")),
            "pins.csv": "id,number,name,etype,on_symbol,description\npin-a,1,IN,input,true,\npin-b,2,OUT,output,true,\n",
            "cad.yaml": "footprint: {}\npackage_id: null\nsymbol: {}\n",
            "datasheet.md": "# SN74HC00N\n{{figure:ff44 bbox=0,0,1,1}}\n![Pinout](images/ff44.png)\n",
        }, "images": {"ff44.png": b"PNG1"}}}
        self.settings = {"settings/mapping.yaml": "name: default\n", "settings/symbol_style.yaml": "style: {}\n"}
        self.pushes = []
        self.feedback = []
        self.reject = None

    def _files(self):
        out = {p: t for p, t in self.settings.items()}
        for part in self.parts.values():
            out.update({f"{part['path']}/{n}": t for n, t in part["files"].items() if n != "datasheet.md"})
        return out

    def get(self, path, params=None, **_):
        if path == "/workspace/sync/manifest/":
            return {"renderer": "R1", "workspace": {"id": 7, "name": "Test"},
                    "settings": {p: etag(t) for p, t in self.settings.items()},
                    "parts": [{"uuid": u, "path": p["path"], "files": {n: etag(t) for n, t in p["files"].items()}}
                              for u, p in self.parts.items()]}
        if path.endswith("/download-markdown/"):
            assert params == {"images": "zip", "metadata": "keep"}
            part = self.parts.get(path.split("/")[3])
            if part is None or "datasheet.md" not in part["files"]:
                raise ApiError("Component not found.", 404)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("SN74HC00N_x.md", part["files"]["datasheet.md"])
                for name, data in part.get("images", {}).items():
                    archive.writestr(f"images/{name}", data)
            return SimpleNamespace(content=buffer.getvalue())
        if path == "/workspace/mappings/system-categories/":
            return {"categories": [{"id": "c1", "name": "ICs", "parent_id": None, "parts": 1},
                                   {"id": "c2", "name": "Logic", "parent_id": "c1", "parts": 0}]}
        if path == "/workspace/components/":
            return {"components": [], "has_next": False}
        if path == "/workspace/components/cad-export/":
            return {"parts": []}
        if path == "/workspace/sync/log/":
            return {"entries": [], "next_page": None}
        raise ApiError(f"unexpected GET {path}", 404)

    def post(self, path, body=None, **_):
        if path == "/workspace/sync/files/":
            files = self._files()
            return {"files": {p: {"etag": etag(files[p]), "content": files[p]} for p in body["paths"] if p in files},
                    "missing": [p for p in body["paths"] if p not in files]}
        if path == "/mcp/feedback/":
            self.feedback.append(body)
            return {"id": 1}
        if path == "/workspace/sync/push/":
            self.pushes.append(body)
            settings = []
            for name, f in (body.get("settings") or {}).items():
                if etag(self.settings[name]) != f["base_etag"]:
                    settings.append({"path": name, "status": "stale", "errors": [], "files": {}})
                    continue
                self.settings[name] = f["content"]
                settings.append({"path": name, "status": "applied", "errors": [],
                                 "files": {name: {"etag": etag(f["content"]), "content": f["content"]}}})
            out = []
            for item in body["parts"]:
                part = self.parts[item["uuid"]]
                if any(etag(part["files"][n]) != f["base_etag"] for n, f in item["files"].items()):
                    out.append({"uuid": item["uuid"], "status": "stale", "errors": [], "files": {}})
                    continue
                if self.reject:
                    out.append({"uuid": item["uuid"], "status": "rejected", "files": {},
                                "errors": [{"file": "parameters.jsonl", "line": 2, "error": self.reject}]})
                    continue
                for name, f in item["files"].items():
                    part["files"][name] = f["content"]
                out.append({"uuid": item["uuid"], "status": "applied", "errors": [],
                            "files": {f"{part['path']}/{n}": {"etag": etag(t), "content": t}
                                      for n, t in part["files"].items() if n != "datasheet.md"}})
            return {"parts": out, "settings": settings}
        raise ApiError(f"unexpected POST {path}", 404)


@pytest.fixture
def server():
    return FakeServer()


@pytest.fixture
def repo(server, tmp_path):
    sync.clone(server, "7", tmp_path / "wc")
    from datasheets_md.repo import Repo
    return Repo.find(tmp_path / "wc")
