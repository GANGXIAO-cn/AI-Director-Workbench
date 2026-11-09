"""Read only named project sources; freeze their bytes at explicit points of use."""
import json
from pathlib import Path
from .baselinerevisions import BaselineRevisionMixin
from .baselineview import source_blocks
from .db import connect, dumps, new_id, transaction
from .errors import Rejected, Conflict, row
from .methodpack import load_config
from .roleassets import RoleAssetMixin
from .projectlibrary import ProjectLibraryMixin
from .storage import sha256_bytes

STAGES = {"characterStandard": ("script", "plan", "prompts"), "soundStandard": ("script", "prompts"),
          "promptStandard": ("prompts",)}

def production(db, pid):
    project = row(db, "SELECT production, config_id FROM project WHERE id=?", pid)
    # Older projects keep their configured paths; new source keys come from their own template.
    return {**load_config(project["config_id"]).get("production", {}), **json.loads(project["production"])}

def read_named(prod, key):
    rel = prod.get(key)
    if not rel or not prod.get("assetRoot"):
        return None
    root = Path(prod["assetRoot"]).resolve()
    path = (root / rel).resolve()
    if Path(rel).is_absolute() or root not in path.parents:
        raise Rejected("资料路径超出项目资产目录")
    try:
        data = path.read_bytes()
        data.decode("utf-8")
        if path.suffix == ".json":
            json.loads(data)
    except (OSError, ValueError) as exc:
        raise Rejected(f"项目原始资料不可读：{rel}") from exc
    return rel, data

class SourceFilesMixin(BaselineRevisionMixin, ProjectLibraryMixin, RoleAssetMixin):
    def _current_baseline(self, db, pid, kind, prod=None):
        if kind not in STAGES:
            raise Rejected("此项目来源不支持基准修订")
        last = db.execute("SELECT relpath,sha256,revision FROM baseline_revision WHERE project_id=? AND kind=? ORDER BY revision DESC LIMIT 1",
                          (pid,kind)).fetchone()
        if last:
            return (last["relpath"],self.store.read_blob(last["sha256"])), last["revision"]
        return read_named(prod if prod is not None else production(db,pid),kind), 0

    def _freeze_sources(self, db, eid, pid):
        prod = production(db, pid)
        db.execute("INSERT INTO episode_resource_version VALUES(?,?)", (eid, prod.get("projectLibrary")))
        db.execute("INSERT INTO episode_product_rule VALUES(?,?)", (eid, prod.get("minimumProductPlacements", 0)))
        for key in STAGES:
            found, _ = self._current_baseline(db,pid,key,prod)
            if found:
                rel, data = found
                db.execute("INSERT INTO episode_source VALUES(?,?,?,?)", (eid,key,rel,self.store.put_blob(data)))

    def _source_entries(self, db, eid, stage):
        return [dict(role=r["kind"], name=r["relpath"], text=self._effective_bytes(db,eid,r).decode("utf-8"))
                for r in db.execute("SELECT * FROM episode_source WHERE episode_id=? ORDER BY kind",(eid,))
                if stage in STAGES[r["kind"]]]

    def project_baselines(self, project_id):
        with connect(self.db_path) as db:
            db.execute("BEGIN")
            prod = production(db, project_id)
            frozen = list(db.execute(
                """SELECT s.*,e.display_code,e.state FROM episode_source s JOIN episode e ON e.id=s.episode_id
                   WHERE e.project_id=? ORDER BY e.display_code""", (project_id,)))
            adopted = {}
            for eid,vid,content in db.execute("""SELECT a.episode_id,v.id,v.content FROM artifact a JOIN version v
                    ON v.id=a.adopted_version_id WHERE a.project_id=? AND a.stage='prompts'""", (project_id,)):
                adopted[eid] = (vid,json.loads(content).get("segments",{}))
            documents = []
            for kind, stages in STAGES.items():
                copies = []
                for rec in frozen:
                    if rec["kind"] == kind:
                        copies.append(dict(episodeId=rec["episode_id"],code=rec["display_code"],state=rec["state"],sha256=rec["sha256"],
                                           path=rec["relpath"],effectiveSha256=sha256_bytes(self._effective_bytes(db,rec["episode_id"],rec))))
                history = []
                for rec in db.execute("SELECT * FROM baseline_revision WHERE project_id=? AND kind=? ORDER BY revision DESC", (project_id,kind)):
                    old,new = self._revision_blocks(rec)
                    history.append(dict(id=rec["id"], revision=rec["revision"], key=rec["block_key"], label=new["label"],
                                        before=old["text"], after=new["text"], sha256=rec["sha256"], baseSha256=rec["base_sha256"]))
                if not prod.get(kind) and not copies and not history:
                    continue
                doc = dict(kind=kind, stages=stages, path=prod.get(kind), copies=copies, blocks=[], history=history)
                try:
                    found, revision = self._current_baseline(db,project_id,kind,prod)
                    if found:
                        path, data = found
                        blocks = source_blocks(kind,data)
                        for block in blocks:
                            block["references"] = []
                        for copy in copies:
                            if copy["episodeId"] not in adopted:
                                continue
                            vid, segments = adopted[copy["episodeId"]]
                            snapshot = self._prompt_version_sources(db,vid,copy["episodeId"])
                            basis = snapshot.get(kind,copy["effectiveSha256"])
                            original = {b["key"]: b["text"] for b in source_blocks(kind,self.store.read_blob(basis))}
                            for block in blocks:
                                phrase = original.get(block["key"])
                                for sid,entry in segments.items():
                                    if phrase and phrase in (entry.get("txt") or ""):
                                        block["references"].append(dict(code=copy["code"],episodeId=copy["episodeId"],
                                            segment=sid,versionId=vid,sha256=basis, matchesCurrent=phrase==block["text"]))
                        doc.update(path=path, revision=revision, sha256=sha256_bytes(data), blocks=blocks)
                    else:
                        doc["error"] = "项目尚未配置此来源"
                except Rejected as exc:
                    doc["error"] = str(exc)
                documents.append(doc)
            return documents

    def source_styles(self, project_id):
        with connect(self.db_path) as db:
            prod=production(db,project_id)
            found,_ = self._current_baseline(db,project_id,"promptStandard",prod)
        options=[]
        if found:
            path,data=found
            block=json.loads(data)["blocks"]["visual_style"]
            options.append(dict(id="visual_style",label="项目写实画面标准",text=block["zh"],source=path,sha256=sha256_bytes(data)))
        found=read_named(prod,"sceneBaselines")
        if found:
            path,data=found
            for b in json.loads(data)["baselines"]:
                if b["production_role"]=="scene" and b["review_status"]=="passed":
                    options.append(dict(id=b["library_ref"],label=b["description"],
                                        text=b["fixed_scope"]+" 使用条件："+"；".join(b["conditions"])+"。限制："+"；".join(b["limits"]),
                                        source=path,sha256=sha256_bytes(data)))
        return options

    def source_topics(self, project_id):
        with connect(self.db_path) as db:
            found = read_named(production(db, project_id), "topicCatalog")
        if not found: return []
        rel, data = found
        return [dict(code=c["id"], title=c["title"], premise=c["cn"], status=c["status"], mechanisms=c.get("mechanisms",[]), comparison=c.get("oldref",""), payoff=c.get("payoff",""), path=rel,
                     sha256=sha256_bytes(data)) for c in json.loads(data)["candidates"]]

    def take_source_topic(self, project_id, code, sha256, command_id=None):
        with transaction(self.db_path) as db:
            def run():
                found = read_named(production(db, project_id), "topicCatalog")
                if not found: raise Rejected("本项目没有原始选题池")
                rel, data = found
                if sha256_bytes(data) != sha256: raise Conflict("原始选题池已变更，请刷新后选用")
                card = next((c for c in json.loads(data)["candidates"] if c["id"] == code), None)
                if not card: raise Rejected("原始选题不存在")
                origin = f"{rel}#{code}@{sha256}"
                prior = db.execute("SELECT id,origin,status FROM topic WHERE project_id=? AND code=?", (project_id,code)).fetchone()
                if prior:
                    if prior["origin"] != origin: raise Conflict("本项目有同号不同来源的选题，请先核对")
                    return dict(topicId=prior["id"],status=prior["status"])
                tid = new_id()
                db.execute("INSERT INTO topic VALUES(?,?,?,?,?,?,?,?,?,?)",(tid,project_id,code,card["cn"],
                           "、".join(card.get("mechanisms") or []),card.get("lead",""),"候选",None,origin,self.clock()))
                db.execute("INSERT INTO topic_source VALUES(?,?,?)",(tid,rel,self.store.put_blob(data)))
                return dict(topicId=tid,status="候选")
            return self._command(db,command_id,"take_source_topic",dict(project=project_id,code=code,sha256=sha256),run)

    def _original_card(self, db, topic):
        r = db.execute("SELECT * FROM topic_source WHERE topic_id=?", (topic["id"],)).fetchone()
        if not r: return ""
        cat = json.loads(self.store.read_blob(r["sha256"]))
        card = next(c for c in cat["candidates"] if c["id"] == topic["code"])
        return f"\n来源：{r['relpath']}#{topic['code']} sha256={r['sha256']}\n原卡全文：\n{dumps(card)}"
