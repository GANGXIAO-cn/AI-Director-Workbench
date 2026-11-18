"""Episode image preparation from an adopted script-level asset list."""
import json
import re

from .db import connect, dumps, new_id, transaction
from .errors import Conflict, Rejected, row


CATEGORIES = {"character":"identity", "identity":"identity", "scene":"scene", "location":"scene",
              "prop":"prop", "product":"product", "state":"state", "costume":"costume"}


def asset_product_code(asset):
    if asset.get('productCode'):
        return asset['productCode']
    codes = set(re.findall(r"\bproductCode\s*[:：=]\s*[\"']?([A-Za-z0-9_-]+)", str(asset.get('description', ''))))
    return next(iter(codes)) if len(codes) == 1 else None


def dependency_errors(assets):
    graph = {}
    for item in assets:
        key = item.get("id")
        dependencies = item.get("dependsOn", [])
        if not isinstance(key, str) or not key:
            return ["素材清单条目须有唯一 id"]
        if not isinstance(dependencies, list) or any(not isinstance(d, str) for d in dependencies):
            return [f"{key} 的 dependsOn 须为素材编号数组"]
        if len(set(dependencies)) != len(dependencies):
            return [f"{key} 含重复依赖"]
        graph[key] = dependencies
    missing = [(key, d) for key, deps in graph.items() for d in deps if d not in graph]
    if missing:
        return [f"{key} 依赖的素材不存在：{d}" for key, d in missing]
    pending = dict(graph)
    while pending:
        ready = [key for key, deps in pending.items() if not any(d in pending for d in deps)]
        if not ready:
            return ["素材依赖存在循环：" + "、".join(pending)]
        for key in ready:
            pending.pop(key)
    return []


class WorksheetMixin:
    def _sync_asset_needs(self, db, episode_id, version_id):
        version = row(db, "SELECT content FROM version WHERE id=?", version_id)
        assets = json.loads(version["content"])["assets"]
        errors = dependency_errors(assets)
        if errors:
            raise Rejected("；".join(errors))
        existing = {r["need_key"]:r for r in db.execute("SELECT * FROM asset_need WHERE episode_id=?", (episode_id,))}
        now = self.clock()
        for position, item in enumerate(assets):
            key = item["id"]
            old = existing.pop(key, None)
            prompt = item.get("imagePrompt") or ""
            title = item.get("title") or item.get("name") or key
            fields = (item["kind"], item.get("roleId"), item.get("stateId"), dumps(item.get("sceneIds", [])),
                      title, item["description"], dumps(item.get("dependsOn", [])), version_id, position)
            if old:
                custom = old["image_prompt"] != old["source_prompt"]
                changed = custom and (old["prompt_changed"] or prompt != old["source_prompt"])
                db.execute("""UPDATE asset_need SET kind=?,role_id=?,state_id=?,scene_ids=?,title=?,description=?,
                    depends_on=?,source_version_id=?,sort=?,source_prompt=?,image_prompt=?,prompt_changed=?,
                    removed=0,revision=revision+1,updated_at=? WHERE id=?""",
                    (*fields, prompt, old["image_prompt"] if custom else prompt, bool(changed), now, old["id"]))
                continue
            fixed = None
            if CATEGORIES.get(item["kind"]) == "identity" and item.get("stateId") in (None, "", "default", "base"):
                fixed=db.execute("""SELECT f.asset_version_id,v.asset_id FROM episode_role_baseline f
                    JOIN asset_version v ON v.id=f.asset_version_id WHERE f.episode_id=? AND f.role=?""",
                    (episode_id,item.get('roleId'))).fetchone()
                # 实测 122：项目已登记该角色固定身份图时，建表即采用登记原图，不让导演再选图。
                try:
                    if fixed is None and item.get("roleId"): self._use_fixed_role_db(db, episode_id, item["roleId"])
                except Rejected:
                    pass
                fixed = fixed or db.execute("""SELECT f.asset_version_id,v.asset_id FROM fixed_role_asset f
                    JOIN asset_version v ON v.id=f.asset_version_id WHERE f.episode_id=? AND f.role=? ORDER BY f.rowid DESC LIMIT 1""",
                    (episode_id, item.get("roleId"))).fetchone()
            db.execute("""INSERT INTO asset_need(id,episode_id,need_key,kind,role_id,state_id,scene_ids,title,description,
                depends_on,source_version_id,sort,source_prompt,image_prompt,decision,adopted_version_id,asset_id,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (new_id(), episode_id, key, *fields, prompt, prompt, "reuse" if fixed else "generate" if prompt.strip() else "pending",
                 fixed["asset_version_id"] if fixed else None, fixed["asset_id"] if fixed else None, now, now))
            if not fixed and prompt.strip():
                db.execute("UPDATE asset_need SET generation_requested=1 WHERE episode_id=? AND need_key=?", (episode_id, key))
        for old in existing.values():
            if not old["removed"]:
                db.execute("UPDATE asset_need SET removed=1,revision=revision+1,updated_at=? WHERE id=?", (now, old["id"]))

    def initialize_asset_worksheet(self, episode_id, version_id, command_id):
        with transaction(self.db_path) as db:
            def run():
                self._require_stress_clearance(db,episode_id)
                adopted = row(db, "SELECT adopted_version_id FROM artifact WHERE episode_id=? AND stage='asset_design'", episode_id)
                if not version_id or adopted["adopted_version_id"] != version_id:
                    raise Conflict("素材清单采用版已变化")
                if not db.execute("SELECT 1 FROM asset_need WHERE episode_id=?", (episode_id,)).fetchone():
                    self._sync_asset_needs(db, episode_id, version_id)
                return dict(versionId=version_id)
            return self._command(db, command_id, "initialize_asset_worksheet", [episode_id,version_id], run)

    def asset_worksheet(self, episode_id):
        with connect(self.db_path) as db:
            db.execute("BEGIN")
            self._episode_in(db, episode_id)
            art = db.execute("SELECT adopted_version_id FROM artifact WHERE episode_id=? AND stage='asset_design'", (episode_id,)).fetchone()
            rows = []
            for entry in db.execute("SELECT * FROM asset_need WHERE episode_id=? AND removed=0 ORDER BY sort", (episode_id,)):
                item = dict(entry)
                for key in ("depends_on", "scene_ids"):
                    item[key] = json.loads(item[key])
                item["candidates"] = [r[0] for r in db.execute("SELECT version_id FROM asset_need_candidate WHERE need_id=? ORDER BY rowid", (entry["id"],))]
                item["request"] = self._open_need_request(db, entry["id"])
                rows.append(item)
            indexed = {r["need_key"]:r for r in rows}
            for item in rows:
                item["missing"] = [key for key in item["depends_on"] if not indexed.get(key, {}).get("adopted_version_id")]
            return dict(versionId=art[0] if art else None, rows=rows,
                        complete=bool(rows) and all(r["adopted_version_id"] or r["decision"]=="text_only" for r in rows))

    def _open_need_request(self, db, need_id):
        current = db.execute("""SELECT b.id AS batchId,b.state AS batchState,r.state FROM asset_need_request n
            JOIN image_request r ON r.id=n.request_id JOIN image_batch b ON b.id=r.batch_id
            WHERE n.need_id=? AND b.state!='cancelled' AND r.state IN ('pending','submitted','unknown')
            ORDER BY r.rowid DESC LIMIT 1""", (need_id,)).fetchone()
        return dict(current) if current else None

    def _land_asset_need(self, db, eid, key, result):
        need = row(db, "SELECT id FROM asset_need WHERE episode_id=? AND need_key=?", eid, key)
        db.execute("INSERT OR IGNORE INTO asset_need_candidate VALUES(?,?)", (need["id"], result["versionId"]))
        db.execute("""UPDATE asset_need SET asset_id=?,generation_requested=0,revision=revision+1,updated_at=? WHERE id=?""",
                   (result["assetId"], self.clock(), need["id"]))

    def set_asset_need(self, episode_id, need_key, revision, action, command_id, prompt=None, reason="", entry_id=None, version_id=None, product_code=None):
        with transaction(self.db_path) as db:
            def apply():
                self._require_stress_clearance(db,episode_id)
                need = row(db, "SELECT * FROM asset_need WHERE episode_id=? AND need_key=? AND removed=0", episode_id, need_key)
                if need["revision"] != revision or (self._open_need_request(db, need["id"]) and action not in ("edit_prompt","use_source","keep_prompt")):
                    raise Conflict("此项已更新或有进行中的请求，请刷新核对")
                changes = {}
                if action in ("edit_prompt", "use_source", "keep_prompt"):
                    value = need["source_prompt"] if action=="use_source" else need["image_prompt"] if action=="keep_prompt" else prompt
                    if not isinstance(value, str):
                        raise Rejected("提示词须为文字")
                    changes.update(image_prompt=value, prompt_changed=0)
                elif action == "text_only":
                    if not isinstance(reason, str) or not reason.strip():
                        reason_value = "导演选择只用文字"
                    else:
                        reason_value = reason.strip()
                    dependents = [r["title"] for r in db.execute("SELECT title,depends_on,decision FROM asset_need WHERE episode_id=? AND removed=0",(episode_id,))
                                  if r["decision"]!="text_only" and need_key in json.loads(r["depends_on"])]
                    if dependents:
                        raise Rejected("以下条目需要这张参考图，请先调整依赖："+"、".join(dependents))
                    changes.update(decision=action, decision_reason=reason_value, adopted_version_id=None, base_version_id=None,generation_requested=0)
                elif action in ("reuse","generate"):
                    base = None
                    if product_code is not None:
                        if action != "reuse" or need["kind"] not in ("prop", "product"):
                            raise Rejected("商品主图可直接用于道具或商品条目")
                        base = self._register_product_reference(db, episode_id, product_code)["versionId"]
                    elif entry_id:
                        source = row(db, "SELECT asset_version_id FROM library_entry WHERE id=?", entry_id)
                        image = self.asset_version(source["asset_version_id"], db)
                        expected = CATEGORIES.get(need["kind"], need["kind"])
                        if image["category"] != expected:
                            raise Rejected("素材类别与本项不一致，请选择同类素材或出新图")
                        if expected == "identity" and (not need["role_id"] or need["role_id"] not in image["roles"]):
                            raise Rejected("身份图的角色编号与本项不一致，请出新图")
                        copied = self._copy_library_image(db, entry_id, episode_id)
                        base = copied["versionId"]
                    elif version_id:
                        candidate = db.execute("SELECT version_id FROM asset_need_candidate WHERE need_id=? AND version_id=?",
                                               (need['id'],version_id)).fetchone()
                        baseline=db.execute('SELECT asset_version_id FROM episode_role_baseline WHERE episode_id=? AND role=?',
                                            (episode_id,need['role_id'])).fetchone() if CATEGORIES.get(need['kind'])=='identity' else None
                        if not candidate and not (baseline and baseline[0]==version_id):
                            raise Rejected('这张图不是本项候选或已登记身份基准')
                        base=version_id
                    if action=="reuse" and not base:
                        raise Rejected("请先选择复用图片")
                    if action=="reuse":
                        v = self._episode_image(db, episode_id, base)
                        self._adopt_episode_image(db, episode_id, base, v, self._image_references(db,v))
                        db.execute("INSERT OR IGNORE INTO asset_need_candidate VALUES(?,?)", (need["id"],base))
                        changes["asset_id"] = v["asset_id"]
                    changes.update(decision=action,decision_reason=reason,adopted_version_id=base if action=="reuse" else None,
                                   base_version_id=base if action=="generate" else None,generation_requested=int(action=="generate"))
                    if prompt is not None:
                        if not isinstance(prompt,str) or not prompt.strip():
                            raise Rejected("请填写出图提示词")
                        changes.update(image_prompt=prompt,prompt_changed=0)
                else:
                    raise Rejected("未知的备图操作")
                assignments = ",".join(k+"=?" for k in changes)
                db.execute(f"UPDATE asset_need SET {assignments},revision=revision+1,updated_at=? WHERE id=?",
                           (*changes.values(),self.clock(),need["id"]))
                return dict(needKey=need_key)
            return self._command(db, command_id, "set_asset_need", [episode_id,need_key,revision,action,prompt,reason,entry_id,version_id] + ([product_code] if product_code is not None else []), apply)

    def prepare_asset_batch(self, episode_id, needs, provider, size, command_id, model=None, quality=None, budget_cents=None):
        with transaction(self.db_path) as db:
            def prepare():
                self._require_stress_clearance(db,episode_id)
                if not isinstance(needs,dict) or not needs:
                    raise Rejected("请勾选待出图条目")
                rows = {r["need_key"]:r for r in db.execute("SELECT * FROM asset_need WHERE episode_id=? AND removed=0", (episode_id,))}
                items = []
                for key, revision in needs.items():
                    r = rows.get(key)
                    if not r or r["revision"] != revision or r["decision"]!="generate" or not r["generation_requested"]:
                        raise Conflict("请刷新后重新核对待出图条目：" + key)
                    if self._open_need_request(db,r["id"]):
                        raise Conflict(r["title"]+" 有进行中或待核对请求")
                    deps = [rows.get(d) for d in json.loads(r["depends_on"])]
                    if any(not dep or not dep["adopted_version_id"] for dep in deps):
                        raise Rejected(r["title"]+" 的依赖图尚未采用")
                    deps.sort(key=lambda d: {"identity":0,"scene":1}.get(CATEGORIES.get(d["kind"]),2))
                    inputs = list(dict.fromkeys([v for v in [r["base_version_id"],*(dep["adopted_version_id"] for dep in deps)] if v]))
                    design=json.loads(row(db,'SELECT content FROM version WHERE id=?',r['source_version_id'])['content'])
                    product=next((asset_product_code(item) for item in design['assets'] if item['id']==key),None)
                    if product:
                        ref=db.execute('SELECT version_id FROM episode_product WHERE episode_id=? AND code=?',(episode_id,product)).fetchone()
                        if not ref:raise Rejected('请先登记本条目的商品主图')
                        if ref[0] not in inputs:inputs.append(ref[0])
                    target = dict(name=r["title"], category=CATEGORIES.get(r["kind"],"reference"), needKey=key,
                                  roles=[r["role_id"]] if r["role_id"] else [])
                    items.append(dict(prompt=r["image_prompt"], inputs=inputs, target=target, needRevision=revision,
                                      needVersion=r["source_version_id"],segments=[]))
                result = self._create_image_batch(db,episode_id,provider,"generate",items,size,model,quality,budget_cents,None)
                for request in db.execute("SELECT id,ordinal FROM image_request WHERE batch_id=?", (result["batchId"],)):
                    need = rows[items[request["ordinal"]-1]["target"]["needKey"]]
                    db.execute("INSERT INTO asset_need_request VALUES(?,?)", (request["id"],need["id"]))
                return result
            return self._command(db,command_id,"prepare_asset_batch",[episode_id,needs,provider,size,model,quality,budget_cents],prepare)

    def adopt_asset_needs(self, episode_id, selections, command_id, bulk=False):
        with transaction(self.db_path) as db:
            def apply():
                self._require_stress_clearance(db,episode_id)
                if not isinstance(selections,dict) or not selections:
                    raise Rejected("请选择要采用的候选图")
                for key, choice in selections.items():
                    need = row(db,"SELECT * FROM asset_need WHERE episode_id=? AND need_key=? AND removed=0",episode_id,key)
                    if (bulk or len(selections)>1) and CATEGORIES.get(need["kind"])=="identity":
                        raise Rejected("身份图须逐张采用")
                    if need["revision"] != choice["revision"] or self._open_need_request(db,need["id"]):
                        raise Conflict("条目已更新，请重新核对")
                    vid = choice["versionId"]
                    row(db,"SELECT 1 FROM asset_need_candidate WHERE need_id=? AND version_id=?",need["id"],vid,missing="候选图不属于本项")
                    v = self._episode_image(db,episode_id,vid)
                    if v["category"] != CATEGORIES.get(need["kind"], need["kind"]):
                        raise Rejected("候选图类别与本项不一致，请重新生图")
                    self._adopt_episode_image(db,episode_id,vid,v,self._image_references(db,v))
                    db.execute("""UPDATE asset_need SET adopted_version_id=?,asset_id=?,generation_requested=0,base_version_id=NULL,
                        revision=revision+1,updated_at=? WHERE id=?""",(vid,v["asset_id"],self.clock(),need["id"]))
                return dict(adopted=list(selections))
            return self._command(db,command_id,"adopt_asset_needs",[episode_id,selections,bulk],apply)
