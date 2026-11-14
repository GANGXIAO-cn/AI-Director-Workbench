import json
import re

from . import workflow
from .db import connect, transaction, new_id, dumps
from .errors import Conflict, Rejected, row
from .storage import canonical

STAGES = ("script","asset_design","acting","plan","prompts")
CONTRACT = ("权威来源是已采用的上游成果和开工资料。supplements 只写本稿新增的可见事实，无新增填空数组。已登记的补充在正文遵守，禁止在输出中重复登记。"
            "subject 用对象.属性，statement 一句话，scenes 写涉及的场号，reason 写原因。如需变更已有说法，在 notes 指出，交导演处理。"
            "新增关键道具、改触发条件、改变结果或靠新行为修补因果须回上游，由导演决定，不得写入本稿。")


def active(db,eid):
    return [dict(r, scenes=json.loads(r["scenes"])) for r in db.execute(
        "SELECT * FROM production_supplement WHERE episode_id=? AND state IN ('confirmed','proposed') ORDER BY rowid",(eid,))]


def entries(db,eid,scenes=None):
    values=[{k:r[k] for k in ("code","subject","statement","scenes","reason","state")} for r in active(db,eid)
            if scenes is None or scenes.intersection(r["scenes"])]
    return [dict(role="production_supplements",name="制作补充.json",text=canonical(values).decode())] if values else []


def errors(db,eid,content,check_registered=True):
    rows=content.get("supplements",[])
    if not isinstance(rows,list):
        return ["supplements 须为数组"]
    known={r["subject"]:r for r in active(db,eid)}
    problems=[]
    for index, item in enumerate(rows, 1):
        if not isinstance(item,dict):
            problems.append(f"制作补充第 {index} 条：须填写完整条目")
            continue
        label = item.get("code") or f"第 {index} 条"
        invalid = []
        if not isinstance(item.get("subject"),str) or not re.fullmatch(r"[^.\s]+\.[^.\s]+",item["subject"]):
            invalid.append("对象.属性")
        for key, name in (("statement","说法"),("reason","原因")):
            if not isinstance(item.get(key),str) or not item[key].strip():
                invalid.append(name)
        scenes = item.get("scenes")
        if not isinstance(scenes,list) or not scenes or not all(isinstance(s,str) and s.strip() for s in scenes):
            invalid.append("涉及场号")
        if invalid:
            problems.append(f"制作补充 {label}（{item.get('subject') or '对象未填写'}）：请补全{'、'.join(invalid)}")
            continue
        existing=known.setdefault(item["subject"],dict(item,code="本稿"))
        if check_registered and existing["statement"]!=item["statement"]:
            problems.append(f"与已登记的制作补充 {existing['code']}（{item['subject']}：{existing['statement']}）说法不同")
    return problems


class SupplementMixin:
    def production_supplements(self,episode_id):
        with connect(self.db_path) as db:
            return [dict(r,scenes=json.loads(r["scenes"])) for r in db.execute(
                "SELECT s.*,v.display_no FROM production_supplement s LEFT JOIN version v ON s.origin_version_id=v.id WHERE s.episode_id=? ORDER BY s.rowid",(episode_id,))]

    def _insert_supplement(self,db,eid,item,stage,vid,state="proposed",replaces=None):
        code="S%02d" % (1+db.execute("SELECT count(*) FROM production_supplement WHERE episode_id=?",(eid,)).fetchone()[0])
        fields=(new_id(),eid,code,item["subject"],item["statement"],dumps(item["scenes"]),item["reason"],stage,vid,state,replaces,self.clock(),self.clock() if replaces else None)
        db.execute("INSERT INTO production_supplement VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",fields)
        return dict(id=fields[0],code=code)

    def _register_supplements(self,db,art,vid):
        content=json.loads(row(db,"SELECT content FROM version WHERE id=?",vid)["content"])
        known={r["subject"] for r in active(db,art["episode_id"])}
        for item in content.get("supplements",[]):
            if item["subject"] not in known:
                self._insert_supplement(db,art["episode_id"],item,art["stage"],vid)
                known.add(item["subject"])

    def decide_supplement(self,supplement_id,action,statement="",command_id=None):
        with transaction(self.db_path) as db:
            def run():
                r=dict(row(db,"SELECT * FROM production_supplement WHERE id=?",supplement_id))
                if r["state"] not in ("proposed","confirmed"):
                    raise Conflict("这条制作补充已失效")
                states={"confirm":"confirmed","replace":"superseded","reject":"rejected"}
                if action not in states or (action=="replace" and not statement.strip()):
                    raise Rejected("请选择确认、拒绝或填写替代说法")
                db.execute("UPDATE production_supplement SET state=?,decided_at=? WHERE id=?",(states[action],self.clock(),r["id"]))
                result=dict(id=r["id"])
                if action=="replace":
                    result=self._insert_supplement(db,r["episode_id"],dict(r,statement=statement.strip(),scenes=json.loads(r["scenes"])),r["origin_stage"],r["origin_version_id"],"confirmed",r["id"])
                if states[action] != r["state"]:
                    self._mark_deliveries_stale(db, r["episode_id"], "制作补充 " + r["code"] + " 已变更")
                if action!="confirm":
                    stages=workflow.ORDER[workflow.ORDER.index(r["origin_stage"]):]
                    eid=r["episode_id"]
                    changes=[dict(target="workflow:"+s,reason="制作补充 "+r["code"]+" 已变更，须核对") for (s,) in db.execute(
                        "SELECT stage FROM artifact WHERE episode_id=? AND adopted_version_id IS NOT NULL",(eid,)) if s in stages]
                    self._record_impacts(db,dict(episode_id=eid,project_id=self._episode_in(db,eid)["project_id"]),changes,"supplement_change",r["id"])
                return result
            return self._command(db,command_id,"decide_supplement",[supplement_id,action,statement],run)
