from __future__ import annotations
import json
import re
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4
from typing import Any

class StructureRegistry:
    def __init__(self, root: str = "generated"):
        self.root=Path(root)
        self.root.mkdir(parents=True,exist_ok=True)
    @staticmethod
    def host(url: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+","_",urlparse(url).netloc.lower().split(":",1)[0]) or "unknown-host"
    def path(self, structure_id: str, host: str) -> Path:
        d=self.root/self.host(host)/"structures"; d.mkdir(parents=True,exist_ok=True)
        return d/f"{structure_id}.json"
    def save(self, structure: dict[str, Any], *, structure_id: str|None=None) -> dict[str, Any]:
        sid=structure_id or structure.get("structure_id") or f"structure_{uuid4().hex[:10]}"
        url=str(structure.get("url") or structure.get("start_url") or "")
        path=self.path(sid,url)
        structure=dict(structure); structure["structure_id"]=sid
        path.write_text(json.dumps(structure,ensure_ascii=False,indent=2),encoding="utf-8")
        return {"structure_id":sid,"domain":path.parent.parent.name,"path":str(path)}
    def list(self)->list[dict[str,Any]]:
        out=[]
        for domain in sorted(self.root.glob("*/structures")):
            if not domain.is_dir(): continue
            for p in sorted(domain.glob("*.json"),key=lambda x:x.stat().st_mtime,reverse=True):
                try: data=json.loads(p.read_text(encoding="utf-8"))
                except Exception: data={}
                out.append({"structure_id":p.stem,"domain":domain.parent.name,"url":data.get("url") or data.get("start_url") or "","method":data.get("method",""),"path":str(p.relative_to(self.root)).replace("\\","/"),"modified":p.stat().st_mtime})
        return out
    def get(self, structure_id: str)->dict[str,Any]|None:
        for p in self.root.glob(f"*/structures/{structure_id}.json"):
            try: return json.loads(p.read_text(encoding="utf-8"))
            except Exception: return None
        return None
