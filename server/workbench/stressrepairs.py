"""Version-bound story review decisions and repair worklists."""
import json

from . import workflow
from .db import connect, dumps, new_id, transaction
from .errors import Conflict, Rejected, row

EARLY = ("story","characters","world","treatment","script")


def adopted(db, eid):
    return {r["stage"]:r["adopted_version_id"] for r in db.execute(
        "SELECT stage,adopted_version_id FROM artifact WHERE episode_id=? AND adopted_version_id IS NOT NULL", (eid,))}


def issues_of(db, report):
    return json.loads(row(db,"SELECT content FROM version WHERE id=?",report)["content"]).get("issues",[])


def issue_stage(value):
    if not isinstance(value, str):
        return None
    value = ''.join(value.split())
    for stage in EARLY:
        label = ''.join(workflow.LABELS[stage].split())
        aliases = {'story': ('梗概', '故事梗概', '01梗概', '01故事梗概'),
                   'world': ('世界观', '03世界观')}.get(stage, ())
        if value in (stage, label, label[:2], label[2:], *aliases):
            return stage
    return None


def upstream_basis(db, eid, stages=EARLY):
    versions=adopted(db,eid)
    return {stage:versions[stage] for stage in stages if stage in versions}


def pending_changes(db,eid):
    return [dict(r,stage=r["target_ref"][9:]) for r in db.execute("""SELECT id,target_ref,reason FROM impact
        WHERE episode_id=? AND kind='workflow_change' AND state='pending' ORDER BY created_at,rowid""",(eid,))
        if r["target_ref"].startswith("workflow:") and r["target_ref"][9:] in EARLY]


class StressRepairMixin:
    def _stress_blockers(self,db,eid):
        blockers=[]
        report=adopted(db,eid).get("stress")
        if report:
            basis=upstream_basis(db,eid)
            saved=db.execute("SELECT value FROM app_setting WHERE key=?",("stress-disposition:"+report,)).fetchone()
            grades=json.loads(saved[0]) if saved else {}
            resolved={r["issue_id"] for r in db.execute("SELECT * FROM stress_resolution WHERE report_version_id=?",(report,))
                      if r["disposition"]=="carry" or json.loads(r["basis"])==basis}
            for issue in issues_of(db,report):
                key=issue.get("id")
                if (issue.get("severity") or grades.get(key,{}).get("severity"))!="normal" and key not in resolved:
                    blockers.append(dict(type="issue",id=key,stage="stress",text=f"{key}：{issue.get('problem','审查问题待处理')}"))
        return blockers+[dict(type="impact",id=r["id"],stage=r["stage"],text=f"{workflow.LABELS[r['stage']]}：{r['reason']}")
                         for r in pending_changes(db,eid)]

    def _require_stress_clearance(self,db,eid):
        blockers=self._stress_blockers(db,eid)
        if blockers:
            raise Rejected("进入 07 前请先处理："+"；".join(r["text"] for r in blockers),detail=blockers)

    def stress_worklist(self,episode_id):
        with connect(self.db_path) as db:
            db.execute("BEGIN")
            self._episode_in(db,episode_id)
            plans=[]
            for r in db.execute("SELECT * FROM repair_plan WHERE episode_id=? ORDER BY created_at DESC,rowid DESC",(episode_id,)):
                plan=dict(r,issues=json.loads(r["issues"]),steps=json.loads(r["steps"]))
                for step in plan["steps"]:
                    if step.get("runId"):
                        run=row(db,"SELECT status,diagnostic FROM run WHERE id=?",step["runId"])
                        step.update(runStatus=run["status"],diagnostic=run["diagnostic"])
                plans.append(plan)
            return dict(blockers=self._stress_blockers(db,episode_id),plans=plans)

    def carry_stress_issue(self,episode_id,report_version_id,issue_id,quote,scope,command_id):
        with transaction(self.db_path) as db:
            def apply():
                if adopted(db,episode_id).get("stress")!=report_version_id:
                    raise Conflict("报告采用版已变化")
                if not all(isinstance(s,str) for s in (quote,scope)):
                    raise Rejected("备注与适用范围须为文本")
                issue=next((i for i in issues_of(db,report_version_id) if i["id"]==issue_id),None)
                if not issue or issue.get("severity")=="normal":
                    raise Rejected("请选择本报告中需要处理的问题")
                if issue.get("category") in ("source","decision","required_setting","format"):
                    raise Rejected("与原文、决定、必守设定冲突或格式错误的问题须修正")
                self._resolve_stress(db,report_version_id,issue_id,"carry",quote,scope,upstream_basis(db,episode_id))
                return dict(issueId=issue_id)
            return self._command(db,command_id,"carry_stress_issue",[episode_id,report_version_id,issue_id,quote,scope],apply)

    def _resolve_stress(self,db,report,key,disposition,note,scope,basis):
        db.execute("""INSERT INTO stress_resolution VALUES(?,?,?,?,?,?,?) ON CONFLICT(report_version_id,issue_id)
            DO UPDATE SET disposition=excluded.disposition,note=excluded.note,scope=excluded.scope,basis=excluded.basis,created_at=excluded.created_at""",
            (report,key,disposition,note,scope,dumps(basis),self.clock()))
        if disposition=='verified':
            self._settle_verified_report_impacts(db,report,basis)

    def _settle_verified_report_impacts(self,db,report,basis):
        art=db.execute('SELECT a.episode_id,a.adopted_version_id FROM version v JOIN artifact a ON a.id=v.artifact_id WHERE v.id=?',(report,)).fetchone()
        if not art or art['adopted_version_id']!=report:return
        eid=art['episode_id']
        if basis!=upstream_basis(db,eid):return
        if any(i['type']=='issue' for i in self._stress_blockers(db,eid)):return
        verified=db.execute("SELECT 1 FROM stress_resolution WHERE report_version_id=? AND disposition='verified' AND basis=?",(report,dumps(basis))).fetchone()
        if not verified:return
        for stage,vid in basis.items():
            db.execute("UPDATE impact SET state='resolved' WHERE episode_id=? AND kind='workflow_change' AND target_ref='workflow:stress' AND source_ref=? AND state='pending'",(eid,stage+':'+vid))
        return True

    def start_repair_plan(self,episode_id,mode,command_id,report_version_id=None,choices=None,confirm_new_round=False,max_rounds=3):
        with transaction(self.db_path) as db:
            def apply():
                versions=adopted(db,episode_id)
                selected=[]
                if mode=="report":
                    if not report_version_id or versions.get("stress")!=report_version_id:
                        raise Conflict("请先采用当前报告，再选择返修问题")
                    if not isinstance(choices,dict) or not choices:
                        raise Rejected("请选择问题及对应的返修方案")
                    report=json.loads(row(db,"SELECT content FROM version WHERE id=?",report_version_id)["content"])
                    for key, index in choices.items():
                        issue=next((i for i in report["issues"] if i["id"]==key),None)
                        options=[o for o in report["options"] if o["issueId"]==key]
                        if not issue or not (type(index) is int and 0<=index<len(options) or isinstance(index,dict) and isinstance(index.get("proposal"),str) and index["proposal"].strip()):
                            raise Rejected(f"{key} 的问题或方案不存在")
                        stage=issue_stage(issue.get("stage"))
                        if stage is None:
                            raise Rejected(f"{key} 的返修阶段无法识别：{issue.get('stage')}")
                        if stage not in versions:
                            raise Rejected(f"{key} 的目标阶段没有可返修的采用稿")
                        selected.append(dict(id=key,stage=stage,issue=issue,option=options[index] if type(index) is int else dict(issueId=key,proposal=index["proposal"],changesCore=False,repairScope="连续制作本轮返修")))
                    prior=[json.loads(r[0]) for r in db.execute("SELECT steps FROM repair_plan WHERE episode_id=? AND report_version_id=?",(episode_id,report_version_id))]
                    for key in choices:
                        rounds=sum(any(x.get("status")=="仍存在" and x["id"]==key for step in steps
                                       for x in step.get("findings",[])) for steps in prior)
                        if rounds>=max_rounds and not confirm_new_round:
                            raise Rejected(f"{key} 连续三轮仍存在。请核对各轮差异，再由导演明确决定是否继续。")
                    first=min(EARLY.index(i["stage"]) for i in selected)
                    steps=[dict(stage=s,action="revise" if n==first else "check") for n,s in enumerate(EARLY) if n>=first and s in versions]
                    steps.append(dict(stage="stress",action="verify"))
                elif mode=="verify":
                    if not report_version_id or versions.get("stress")!=report_version_id:
                        raise Conflict("当前采用的审查报告已变化")
                    selected=[dict(id=i['id'],stage=issue_stage(i.get('stage')),issue=i) for i in issues_of(db,report_version_id) if i['id'] in (choices or {})]
                    if not selected or len(selected)!=len(choices):
                        raise Rejected("请选择原报告中的问题")
                    steps=[dict(stage="stress",action="verify")]
                elif mode=="upstream":
                    impacted={r["stage"] for r in pending_changes(db,episode_id)}
                    if choices:
                        if versions.get("stress") != report_version_id or not report_version_id:
                            raise Conflict("当前采用的审查报告已变化")
                        report=json.loads(row(db,"SELECT content FROM version WHERE id=?",report_version_id)["content"])
                        for issue in report["issues"]:
                            if issue["id"] in choices:
                                stage=issue_stage(issue["stage"])
                                selected.append(dict(id=issue["id"],stage=stage,issue=issue,
                                                     options=[o for o in report["options"] if o["issueId"]==issue["id"]]))
                                impacted.add(stage)
                        if len(selected)!=len(choices) or any(i["stage"] not in versions for i in selected):
                            raise Rejected("待核对问题缺少有效阶段或采用稿")
                    steps=[dict(stage=s,action="check",**({"scopeIssues":True} if selected else {}))
                           for s in EARLY if s in impacted and s in versions]
                    if not steps:
                        raise Rejected("没有待核对的上游变更")
                elif mode=="early":
                    if not versions.get("treatment"):
                        raise Rejected("04 分场采用后可做结构体检")
                    steps=[dict(stage="stress",action="early")]
                else:
                    raise Rejected("返修类型无效")
                if db.execute("SELECT 1 FROM repair_plan WHERE episode_id=? AND state='open'",(episode_id,)).fetchone():
                    raise Conflict("本集已有进行中的返修或核对，请从进度条继续")
                for step in steps:
                    step.update(state="pending",versionId=None,note="")
                pid=new_id()
                db.execute("INSERT INTO repair_plan VALUES(?,?,?,?,?,'open',?)",(pid,episode_id,report_version_id,dumps(selected),dumps(steps),self.clock()))
                return self._advance_repair(db,pid)
            return self._command(db,command_id,"start_repair_plan",[episode_id,mode,report_version_id,choices,confirm_new_round,max_rounds],apply)

    def advance_repair_plan(self,plan_id,command_id):
        with transaction(self.db_path) as db:
            return self._command(db,command_id,"advance_repair_plan",plan_id,lambda:self._advance_repair(db,plan_id))

    def _advance_repair(self,db,pid):
        from .methodpack import method_entries
        plan=row(db,"SELECT * FROM repair_plan WHERE id=?",pid)
        if plan["state"]!="open":
            raise Conflict("本单已结束")
        steps=json.loads(plan["steps"])
        step=next(s for s in steps if s["state"]!="done")
        if step["state"]=="running":
            status=row(db,"SELECT status FROM run WHERE id=?",step["runId"])["status"]
            if status not in ("failed","stopped","waiting_quota"):
                raise Conflict("当前步骤正在运行")
        if step["state"]=="adopt":
            raise Rejected("请先采用本步待确认的新稿")
        eid, stage, action = plan["episode_id"],step["stage"],step["action"]
        versions=adopted(db,eid)
        if plan["report_version_id"] and versions.get("stress")!=plan["report_version_id"]:
            raise Conflict("报告采用版已变化")
        scope=EARLY[:4] if action=="early" else EARLY if action=="verify" else EARLY[:EARLY.index(stage)+1]
        basis=upstream_basis(db,eid,scope)
        base=versions.get(stage) if action in ("check","revise") else None
        ep=self._episode_in(db,eid)
        aid=self._ensure_artifact(db,ep,stage)
        executor={**self._kickoff(db,eid)["executor_plan"],**self._stage_executors(db,eid,stage)}
        issues=json.loads(plan["issues"])
        impacts=[r for r in pending_changes(db,eid) if r["stage"]==stage]
        if step.get("scopeIssues"):
            issues=[i for i in issues if i["stage"]==stage]
        keys=[i["id"] for i in issues] or [r["id"] for r in impacts]
        if action=="revise":
            goal="按选定问题、原文依据和方案，基于当前采用稿返修，提交本阶段完整新稿。"
        elif action=="check":
            goal="逐项核对返修事项与上游采用稿。如须修改，提交本阶段完整新稿。无需修改时用 discussion，reply 填 JSON 数组，每项 id、note（核对内容）、quote（本阶段采用稿逐字引文）。"
        elif action=="verify":
            goal="只复核本单的问题编号，逐条给出结论。用 discussion，reply 填 JSON 数组，每项 id、status（已解决或仍存在）、note（依据和本轮变化）。"
        else:
            goal="用故事审查方法做结构体检，只读 01–04。用 discussion，reply 填 JSON 对象，字段与 06 审查报告一致，scope 标明未含剧本。"
        extra=[dict(role="repair_scope",name="本轮核对事项",text=dumps(dict(issues=issues,impacts=impacts)))]
        meta=dict(planId=pid,action=action,keys=keys,basis=basis,stage=stage)
        header={"repairStep":meta}
        if action!="revise":
            header["stageDialogue"]=dict(request=goal,revision=row(db,"SELECT revision FROM artifact WHERE id=?",aid)["revision"],
                                         readOnly=action!="check",repair=meta)
        entries=None
        if action in ("verify","early"):
            pack,profile=self._episode_method(db,eid)
            entries=method_entries(pack,"stress",profile)
            entries.extend(dict(role="adopted_"+s,name=s+":"+vid,text=self._adopted_stage_text(db,eid,s)) for s,vid in basis.items())
            entries.append(dict(role="stage_contract",name="审查范围",text=workflow.CONTRACTS["stress"]+"\n"+goal))
        tid,_=self._draft_thread(db,ep["project_id"],eid,aid,stage,goal,executor)
        rid=self._queue_run(db,tid,"revise" if base else "create",base,goal,entries=entries,
                            extra_header=header,message_entries=extra,discussion_only=action in ("early","verify"))
        db.execute("INSERT INTO repair_run VALUES(?,?,?)",(rid,pid,dumps(basis)))
        step.update(state="running",basis=basis,impactIds=[r["id"] for r in impacts],runId=rid,note="")
        db.execute("UPDATE repair_plan SET steps=? WHERE id=?",(dumps(steps),pid))
        return dict(planId=pid,runId=rid)

    def _repair_reply_errors(self,meta,result):
        if result["kind"]=="draft":
            return [] if meta["action"]=="check" else ["此步只提交核对结论"]
        try:
            answer=json.loads(result["reply"])
        except ValueError:
            return ["核对结论须为有效 JSON"]
        if meta["action"]=="early":
            if not isinstance(answer,dict) or "未含剧本" not in str(answer.get("scope","")):
                return ["结构体检须标明未含剧本"]
            return workflow.check("stress",answer)
        if not isinstance(answer,list) or not all(isinstance(i,dict) and isinstance(i.get("id"),str) for i in answer):
            return ["核对结果须按本单编号逐条给出"]
        if sorted(i["id"] for i in answer)!=sorted(meta["keys"]):
            return ["核对编号与本单不符"]
        def strings(value):
            if isinstance(value,str): yield value
            if isinstance(value,(list,dict)):
                for child in value.values() if isinstance(value,dict) else value:
                    yield from strings(child)
        with connect(self.db_path) as db:
            text=[]
            if meta["action"]=="check":
                vid=meta["basis"][meta["stage"]]
                text=list(strings(json.loads(row(db,"SELECT content FROM version WHERE id=?",vid)["content"])))
            for finding in answer:
                if not isinstance(finding.get("note"),str) or not finding["note"].strip():
                    return [finding["id"]+" 缺少核对依据"]
                if meta["action"]=="verify" and finding.get("status") not in ("已解决","仍存在"):
                    return [finding["id"]+" 缺少复核结论"]
                if meta["action"]=="check":
                    quote=finding.get("quote")
                    if not isinstance(quote,str) or not quote.strip() or not any(quote in s for s in text):
                        return [finding["id"]+" 引文须来自当前阶段采用稿"]
        return []

    def _repair_result(self,db,run,kind,result,vid=None):
        manifest,_=self.store.load_manifest(run["manifest_relpath"])
        meta=manifest.get("repairStep")
        if not meta:return []
        plan=row(db,"SELECT * FROM repair_plan WHERE id=?",meta["planId"])
        steps=json.loads(plan["steps"])
        step=next((s for s in steps if s.get("runId")==run["id"]),None)
        if not step or plan["state"]!="open":
            return []
        eid=plan["episode_id"]
        current=upstream_basis(db,eid,EARLY[:4] if meta["action"]=="early" else EARLY if meta["action"]=="verify" else EARLY[:EARLY.index(meta["stage"])+1])
        if meta["basis"]!=current or (plan["report_version_id"] and adopted(db,eid).get("stress")!=plan["report_version_id"]):
            step.update(state="pending",note="采用稿已变化，请以最新版本重新核对")
        elif kind=="draft":
            step.update(state="adopt",versionId=vid,note="待导演采用")
        else:
            answer=json.loads(result["reply"])
            step.update(state="done",note="已核对",result=answer)
            if meta["action"]=="check":
                db.executemany("UPDATE impact SET state='resolved' WHERE id=? AND state='pending'",[(i,) for i in step["impactIds"]])
            elif meta["action"]=="verify":
                step["findings"]=answer
                for finding in answer:
                    if finding["status"]=="已解决":
                        self._resolve_stress(db,plan["report_version_id"],finding["id"],"verified",finding["note"],"",current)
        return self._continue_repair(db,plan,steps)

    def _continue_repair(self,db,plan,steps):
        state="open"
        if all(s["state"]=="done" for s in steps):
            state="unresolved" if any(f["status"]=="仍存在" for s in steps for f in s.get("findings",[])) else "done"
        db.execute("UPDATE repair_plan SET steps=?,state=? WHERE id=?",(dumps(steps),state,plan["id"]))
        if state=="open" and next(s for s in steps if s["state"]!="done")["state"]=="pending":
            first=next(s for s in steps if s["state"]!="done")
            if not first.get("runId"):
                db.execute("SAVEPOINT advance_next_repair")
                try:
                    result=self._advance_repair(db,plan["id"])
                except (Rejected,Conflict) as exc:
                    db.execute("ROLLBACK TO advance_next_repair")
                    first["note"]=str(exc)
                    db.execute("UPDATE repair_plan SET steps=? WHERE id=?",(dumps(steps),plan["id"]))
                    return []
                finally:
                    db.execute("RELEASE advance_next_repair")
                return [result["runId"]]
        return []

    def _guard_repair_adoption(self,db,art,vid):
        binding = db.execute("SELECT r.basis,p.state,p.steps,v.source_run_id FROM version v "
                             "JOIN repair_run r ON r.run_id=v.source_run_id JOIN repair_plan p ON p.id=r.plan_id "
                             "WHERE v.id=?", (vid,)).fetchone()
        if not binding:
            return
        basis=upstream_basis(db,art["episode_id"],EARLY[:EARLY.index(art["stage"])+1])
        current=any(step.get("versionId")==vid and step.get("runId")==binding["source_run_id"]
                    and step["state"]=="adopt" for step in json.loads(binding["steps"]))
        if binding["state"]!="open" or not current or json.loads(binding["basis"])!=basis:
            raise Conflict("返修稿所依据的采用版本已变化，请从返修单重新核对")

    def _repair_retried(self,db,meta,old,new):
        plan=row(db,"SELECT * FROM repair_plan WHERE id=?",meta["planId"])
        steps=json.loads(plan["steps"])
        step=next((s for s in steps if s.get("runId")==old and s["state"]=="running"),None)
        if not step or plan["state"]!="open":
            raise Conflict("返修单已经更新，请从当前步骤继续")
        db.execute("INSERT INTO repair_run VALUES(?,?,?)",(new,plan["id"],dumps(meta["basis"])))
        step["runId"]=new
        db.execute("UPDATE repair_plan SET steps=? WHERE id=?",(dumps(steps),plan["id"]))

    def _repair_after_adoption(self,db,art,vid):
        for plan in db.execute("SELECT * FROM repair_plan WHERE episode_id=? AND state='open'",(art["episode_id"],)):
            if art["stage"]=="stress" and plan["report_version_id"] and plan["report_version_id"]!=vid:
                db.execute("UPDATE repair_plan SET state='abandoned' WHERE id=?",(plan["id"],))
                continue
            steps=json.loads(plan["steps"])
            step=next(s for s in steps if s["state"]!="done")
            if step["state"]=="adopt" and step["versionId"]!=vid and art["stage"] in step["basis"] and step["basis"][art["stage"]]!=vid:
                step.update(state="pending",note="依据版本已变化，请重新核对")
                db.execute("UPDATE repair_plan SET steps=? WHERE id=?",(dumps(steps),plan["id"]))
            if step["state"]=="adopt" and step["versionId"]==vid:
                step.update(state="done",note="已采用返修稿")
                return self._continue_repair(db,plan,steps)
        return []
