"""Project conversations with explicitly selected, versioned inputs."""
import json
from dataclasses import replace
from io import BytesIO

from PIL import Image

from .baselineview import block_by_key
from .storage import sha256_bytes
from .custommethods import edition
from .db import connect, dumps, new_id, transaction
from .errors import Conflict, NotFound, Rejected, row
from .executors.base import ExecRequest
from .prompts import prompt_from
from . import topicresearch, filmedit
from .stages import WRITABLE

REPLY_SCHEMA = {"type":"object", "properties":{"reply":{"type":"string"}}, "required":["reply"], "additionalProperties":False}


class FreeChatMixin(topicresearch.TopicResearchMixin):
    def _chat_project_context(self, db, project_id):
        overview = self.project_overview(project_id)
        project = overview["project"]
        episodes = sorted(overview["episodes"], key=lambda ep: ep["created_at"], reverse=True)
        todos = overview["todos"]
        candidates = db.execute(
            "SELECT code, premise, count(*) OVER () AS total FROM research_topics "
            "WHERE project_id=? AND status='候选' AND episode_id IS NULL AND id NOT IN (SELECT topic_id FROM topic_publication) ORDER BY created_at DESC LIMIT 20",
            (project_id,),
        ).fetchall()
        return dict(
            项目=project["name"], 简介=project["summary"][:1000],
            候选选题数=candidates[0]["total"] if candidates else 0,
            最近候选选题=[dict(编号=t["code"], 前提=t["premise"][:240]) for t in candidates],
            剧集数=len(episodes),
            最近创建的剧集=[dict(集号=e["display_code"], 标题=e["title"][:160], 状态=e["label"],
                             已采纳阶段=[k for k,v in e["adopted"].items() if v]) for e in episodes[:20]],
            项目页待办数=len(todos), 项目页待办=[t["title"][:320] for t in todos[:20]],
        )

    def free_chat(self, project_id, thread_id=None, episode_id=None, stage=None):
        with connect(self.db_path) as db:
            row(db, "SELECT id FROM project WHERE id=?", project_id)
            if episode_id is not None:
                self._episode_in(db, episode_id, project_id)
                threads = [dict(r) for r in db.execute("SELECT f.id,f.title FROM free_thread f JOIN episode_stage_thread s "
                           "ON s.thread_id=f.id WHERE f.project_id=? AND s.episode_id=? AND s.stage=?", (project_id,episode_id,stage))]
                if threads and thread_id is None:
                    thread_id = threads[0]["id"]
            else:
                threads = [dict(r) for r in db.execute("SELECT f.id,f.title FROM free_thread f WHERE project_id=? "
                           "AND NOT EXISTS(SELECT 1 FROM episode_stage_thread s WHERE s.thread_id=f.id) AND NOT EXISTS(SELECT 1 FROM topic_research r WHERE r.thread_id=f.id) ORDER BY f.rowid DESC",(project_id,))]
            if thread_id and not any(t["id"] == thread_id for t in threads):
                raise NotFound("当前工作区找不到这段对话")
            turns = []
            if thread_id:
                turns = [dict(r) for r in db.execute("SELECT id,text,reply,status,reason,provider,input_hash,attempt,manifest_relpath "
                                                     "FROM free_turn WHERE thread_id=? ORDER BY rowid",(thread_id,))]
                for t in turns:
                    manifest, digest = self.store.load_manifest(t.pop("manifest_relpath"))
                    if digest != t["input_hash"]:
                        raise Rejected("自由对话输入清单指纹不符")
                    connection = manifest.get("executorConnection") or manifest.get("creator") or {}
                    t["model"], t["effort"] = connection.get("model"), connection.get("effort")
                    t["methods"] = manifest["methodVersions"]
                    if stage == "finecut":
                        edit = db.execute("SELECT edits FROM finecut_edit WHERE turn=?", (t["id"],)).fetchone()
                        t["editQueued"] = bool(edit)
                        t["replanned"] = bool(edit and any(op["action"]=="replan" for op in json.loads(edit[0])))
                    if episode_id:
                        t["basis"] = manifest["stageBasis"]
                    t["sources"] = [i for i in manifest["inputs"] if i["role"] in {"project_method","image","canvas","baseline"}]
            return dict(threads=threads, turns=turns)

    def free_chat_options(self, project_id):
        with connect(self.db_path) as db:
            row(db,"SELECT id FROM project WHERE id=?",project_id)
            assets = [dict(r) for r in db.execute("SELECT v.id,a.name,v.version_no,v.sha256 FROM asset_version v JOIN asset a "
                      "ON a.id=v.asset_id WHERE a.project_id=? AND a.kind='image' ORDER BY a.name,v.version_no DESC",(project_id,))]
            canvases = [dict(r) for r in db.execute("SELECT e.id,e.display_code,coalesce(c.revision,0) AS revision FROM episode e LEFT JOIN canvas c "
                        "ON c.episode_id=e.id JOIN kickoff k ON k.episode_id=e.id WHERE e.project_id=? ORDER BY e.display_code",(project_id,))]
        return dict(assets=assets,canvases=canvases,providers=[p for p in self.executors if p!='claude'])

    def film_chat_context(self, episode_id, stage, delivery_id=None):
        if stage not in ("package","finecut"):
            raise Rejected("未知制作阶段")
        with connect(self.db_path) as db:
            return self._film_context(db, episode_id, stage, delivery_id)

    def _film_context(self, db, eid, stage, delivery_id):
        from .videoqueue import VideoQueue
        ep = self._episode_in(db,eid)
        start = db.execute("SELECT state,delivery,error FROM film_start WHERE episode_id=?", (eid,)).fetchone()
        if delivery_id or (start and start["delivery"]):
            d = row(db,"SELECT id,segments,versions FROM delivery WHERE episode_id=? AND id=?",eid,delivery_id or start["delivery"])
        else:
            d = db.execute("SELECT id,segments,versions FROM delivery WHERE episode_id=? ORDER BY rowid DESC LIMIT 1",(eid,)).fetchone()
        name = "精剪" if stage=="finecut" else "生成视频与配乐"
        context = dict(集号=ep["display_code"],标题=ep["title"],阶段=name,自动开工=start["state"] if start else None,
                       开工说明=start["error"] if start else "",规则="能剪就采用。每段可首发一次，另可返工最多三次。整集返工上限为分段数的三倍，首发另计。BGM一首直接使用。")
        if d:
            status = VideoQueue(self.store.root).view(d["id"])
            jobs = {j["segment"]:j for j in status["jobs"]}
            context.update(分段=[dict(段=s,进度=jobs.get(s,{}).get("state","待派发"),
                                当前动作=jobs.get(s,{}).get("action") or jobs.get(s,{}).get("state"),返工=jobs.get(s,{}).get("round",0),审片=jobs.get(s,{}).get("review"),
                                返工记录=jobs.get(s,{}).get("reworks",[]),
                                错误=jobs.get(s,{}).get("error"),承接=jobs.get(s,{}).get("deps"))
                                for s in json.loads(d["segments"])],
                           配乐=(status["music"] or {}).get("action"),精剪动作=status["cut"].get("action") or status["cut"].get("state"),精剪已接收=bool((status["music"] or {}).get("handoff")),
                           额度=status["budget"],暂停=status["paused"])
        versions = json.loads(d["versions"]) if d else dict(db.execute(
            "SELECT stage,adopted_version_id FROM artifact WHERE episode_id=?", (eid,)))
        def content(stage):
            value = db.execute("SELECT v.content FROM version v JOIN artifact a ON a.id=v.artifact_id "
                               "WHERE a.episode_id=? AND a.stage=? AND v.id=?", (eid,stage,versions.get(stage))).fetchone()
            return json.loads(value[0]) if value else {}
        if stage=="finecut" and d:
            context["剪辑修改记录"]=filmedit.notes(db,d["id"])
            context["精剪成片"]=status["cut"]
            context["支持专家重排"] = True
            if status["cut"].get("state") == "已出片":
                edit = self.store.root/"video-production"/d["id"]/"finecut/精剪过程/edit.json"
                if edit.is_file():
                    context["当前采用剪点"] = json.loads(edit.read_bytes()).get("clips", [])
        context["交付版本"] = d["id"] if d else None
        context["故事梗概"] = content("story").get("synopsis","")
        script = content("script")
        context["分段剧本"] = script.get("shootingSegments",[])
        if not context["分段剧本"]:
            context["分场剧本"] = script.get("scenes",[])
        context["采用分镜"] = [{k:s[k] for k in ("id","segment","action","cut") if k in s}
                              for s in content("plan").get("shots",[])]
        publication = content("publish")
        context["发布与配乐要求"] = {k:publication[k] for k in ("title","body","suno_title","suno_style","suno_exclude","cues") if k in publication}
        context["导演决定"] = [dict(r) for r in db.execute("SELECT code,text,scope_kind,scope_value FROM director_decision "
                                                         "WHERE episode_id=? AND state='active' ORDER BY code",(eid,))]
        messages = self._episode_messages(db,eid,limit=24)["messages"]
        requests = self._episode_messages(db,eid,limit=8,user_only=True)["messages"]
        recent = {m["id"]:m for m in [*requests,*messages] if m["text"].strip()}
        context["近期制作对话"] = [dict(role=m["role"],text=m["text"][:1400]) for m in
                                sorted(recent.values(),key=lambda m:(m["created_at"],m["seq"]))]
        peer = db.execute("SELECT s.stage,t.text,t.reply FROM free_turn t JOIN episode_stage_thread s ON s.thread_id=t.thread_id "
                          "WHERE s.episode_id=? AND s.stage<>? AND t.status='succeeded' ORDER BY t.rowid DESC LIMIT 10",(eid,stage)).fetchall()
        context["其他阶段讨论"] = [dict(stage=r["stage"],text=r["text"][:1200],reply=r["reply"][:2200]) for r in reversed(peer)]
        return context

    def _film_chat_inputs(self, db, eid, stage, delivery_id):
        context = self._film_context(db,eid,stage,delivery_id)
        from . import filmroles
        expert = self.active_selection(filmroles.selections(db, eid, stage)["expert"])
        header = dict(episodeId=eid,stage=stage,stageBasis=dict(label="本集"+context["阶段"]+"制作依据",deliveryId=context["交付版本"]),
                      creator=expert)
        return header,[dict(role="film_context",name="本次提问时的实际制作记录",text=dumps(context))]

    def send_free_chat(self, project_id, text, provider, thread_id=None, previous=None, methods=(), images=(),
                       canvas=None, canvas_revision=None, command_id=None, baseline=None, episode_id=None, stage=None, source_version=None, delivery_id=None, finecut_mode=None, model=None, effort=None):
        if provider=='claude':provider=self.active_selection(dict(provider=provider))['provider']
        if not isinstance(text,str) or not text.strip():
            raise Rejected("请输入对话内容")
        if not isinstance(provider,str) or provider not in self.executors:
            raise Rejected("请选择本机执行器")
        for ids in (methods, images):
            if not isinstance(ids,(tuple,list)) or not all(isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):
                raise Rejected("引用列表含无效或重复版本")
        if len(images)>6 or images and not self.executors[provider].reads_images:
            raise Rejected("最多可附六张原图，请选择支持图片的执行器")
        payload = dict(project=project_id,thread=thread_id,previous=previous,text=text,methods=methods,images=images,
                       canvas=canvas,canvasRevision=canvas_revision,provider=provider)
        if model or effort: payload.update(model=model, effort=effort)
        if episode_id is not None or stage is not None or source_version is not None:
            if not episode_id or (stage not in WRITABLE and stage not in ("package","finecut")):
                raise Rejected("请选择本集的写作阶段")
            payload.update(episodeId=episode_id, stage=stage, sourceVersion=source_version)
        if delivery_id is not None:
            if stage not in ("package","finecut"):
                raise Rejected("交付版本仅用于视频或精剪讨论")
            payload["deliveryId"] = delivery_id
        if finecut_mode is not None:
            if stage != "finecut" or finecut_mode not in ("revise","discuss"):
                raise Rejected("请选择精剪对话方式")
            payload["finecutMode"] = finecut_mode
        if baseline is not None:
            payload["baseline"] = baseline
        with transaction(self.db_path) as db:
            def create():
                row(db,"SELECT id FROM project WHERE id=?",project_id)
                tid = thread_id or new_id()
                stage_header = {}
                stage_entries = []
                if episode_id is not None:
                    self._episode_in(db, episode_id, project_id)
                    existing = db.execute("SELECT thread_id FROM episode_stage_thread WHERE episode_id=? AND stage=?",
                                          (episode_id,stage)).fetchone()
                    if (existing[0] if existing else None) != thread_id:
                        raise Conflict("本阶段的对话已变化，请刷新")
                    if stage in ("package", "finecut"):
                        stage_header, stage_entries = self._film_chat_inputs(db, episode_id, stage, delivery_id)
                        if finecut_mode:
                            stage_header["finecutMode"] = finecut_mode
                            if finecut_mode == "revise" and not stage_header["stageBasis"]["deliveryId"]:
                                raise Rejected("本集还没有可修改的交付版本")
                    else:
                        stage_header, stage_entries = self._stage_chat_inputs(db, episode_id, stage, source_version, tid)
                    if provider != stage_header["creator"]["provider"]:
                        raise Conflict("本集执行器已变化，请刷新")
                    if any("data" in e for e in stage_entries) and not self.executors[provider].reads_images:
                        raise Rejected("本次参考图需要支持图片的执行器")
                elif thread_id and db.execute("SELECT 1 FROM episode_stage_thread WHERE thread_id=?", (tid,)).fetchone():
                    raise Rejected("请在对应单集阶段继续这段对话")
                if thread_id and db.execute("SELECT 1 FROM topic_research WHERE thread_id=?", (tid,)).fetchone():
                    raise Rejected("请在选题池中查看研究结果")
                if thread_id:
                    row(db,"SELECT id FROM free_thread WHERE project_id=? AND id=?",project_id,tid)
                else:
                    db.execute("INSERT INTO free_thread VALUES(?,?,?)",(tid,project_id,text.strip()[:48]))
                    if episode_id:
                        db.execute("INSERT INTO episode_stage_thread VALUES(?,?,?)", (tid,episode_id,stage))
                history = list(db.execute("SELECT id,status,text,reply,manifest_relpath,input_hash FROM free_turn WHERE thread_id=? ORDER BY rowid",(tid,)))
                if previous != (history[-1]["id"] if history else None):
                    raise Conflict("对话已有新内容，请刷新后发送")
                if history and history[-1]["status"] in {"queued","running"}:
                    raise Conflict("请等待上一条回复，或先停止该次请求")
                entries = stage_entries
                if episode_id is None:
                    context = self._chat_project_context(db, project_id)
                    entries.append(dict(role="project_context", name="发送时的项目概况", text=dumps(context)))
                if baseline is not None:
                    if not isinstance(baseline,dict) or set(baseline) != {"kind","key","sha256","revision"}:
                        raise Rejected("基准引用缺少固定块或版本")
                    found, revision = self._current_baseline(db,project_id,baseline["kind"])
                    if not found or revision != baseline["revision"] or sha256_bytes(found[1]) != baseline["sha256"]:
                        raise Conflict("制作基准已更新，请重新选择原文")
                    block = block_by_key(baseline["kind"],found[1],baseline["key"])
                    entries.append(dict(role="baseline",name=f"{found[0]}#{block['key']}@{baseline['sha256']}",text=block["text"]))
                method_ids = set()
                for vid in methods:
                    v = edition(db,project_id,vid)
                    if v["method_id"] in method_ids:
                        raise Rejected("同一技能请选择一个版本")
                    method_ids.add(v["method_id"])
                    entries.append(dict(role="project_method",name=f"{v['title']} · R{v['number']}",text=self._uploaded_text(v)))
                if canvas:
                    ep = self._episode_in(db,canvas,project_id)
                    view = self.canvas_view(canvas)
                    if view["revision"] != canvas_revision:
                        raise Conflict("画布已更新，请重新确认版本")
                    entries.append(dict(role="canvas",name=f"{ep['display_code']} · R{canvas_revision}",text=dumps(view["doc"])))
                for vid in images:
                    v = row(db,"SELECT v.sha256,a.name,v.version_no FROM asset_version v JOIN asset a ON v.asset_id=a.id "
                               "WHERE a.kind='image' AND a.project_id=? AND v.id=?",project_id,vid)
                    entries.append(dict(role="image",name=f"{v['name']} · R{v['version_no']}",data=self.store.read_blob(v["sha256"])))
                for h in history:
                    if h["status"]=="succeeded":
                        if episode_id:
                            prior, prior_hash = self.store.load_manifest(h["manifest_relpath"])
                            if prior_hash != h["input_hash"]:
                                raise Rejected("先前试写的输入清单指纹不符")
                            entries.append(dict(role="trial_basis", name="此前回复的依据", text=dumps(prior["stageBasis"])))
                        entries.extend([dict(role="user",name="此前提问",text=h["text"]),dict(role="assistant",name="此前回复",text=h["reply"])])
                entries.append(dict(role="request",name="本条消息",text=text))
                turn, attempt = new_id(), new_id()
                header = dict(projectId=project_id,threadId=tid,purpose="free_chat",provider=provider,methodVersions=list(methods),
                              assetVersions=list(images),canvasEpisode=canvas,canvasRevision=canvas_revision, **stage_header)
                choice = header.get("creator",dict(model=model, effort=effort))
                header["executorConnection"] = self.run_connection(provider,choice.get("model"),choice.get("effort"))
                digest, rel = self.store.freeze(turn,header,entries)
                db.execute("INSERT INTO free_turn(id,thread_id,text,provider,status,attempt,input_hash,manifest_relpath) "
                           "VALUES(?,?,?,?,'queued',?,?,?)",(turn,tid,text,provider,attempt,digest,rel))
                db.execute('INSERT INTO storage_chat VALUES(?,?,?,NULL)', (attempt,turn,self.clock()))
                return dict(threadId=tid,turnId=turn,attemptId=attempt)
            return self._command(db,command_id,"send_free_chat",payload,create)

    def retry_free_chat(self, project_id, turn_id, expected_attempt, command_id=None):
        with connect(self.db_path) as db:
            turn = self._free_turn(db,project_id,turn_id)
            if db.execute("SELECT 1 FROM topic_research WHERE thread_id=?",(turn["thread_id"],)).fetchone():
                if turn["attempt"] != expected_attempt or turn["status"] not in ("failed","waiting_quota","stopped"):
                    raise Conflict("请选择已失败或停止的研究")
                manifest,digest = self.store.load_manifest(turn["manifest_relpath"])
                if digest != turn["input_hash"]:
                    raise Rejected("无法核对原研究资料")
                note = next((e for e in self.store.manifest_texts(manifest) if e["role"]=="reference_note"),None)
                reference = json.loads(note["text"]) if note else None
                return self.start_topic_research(project_id,turn["text"],turn["provider"],command_id,reference,manifest.get("chosenCount"))
        with transaction(self.db_path) as db:
            def retry():
                turn = self._free_turn(db,project_id,turn_id)
                latest = row(db,"SELECT id FROM free_turn WHERE thread_id=? ORDER BY rowid DESC LIMIT 1",turn["thread_id"])
                if turn["attempt"]!=expected_attempt or latest["id"]!=turn_id or turn["status"] not in {"failed","waiting_quota","stopped"}:
                    raise Conflict("仅可重试对话末尾已停止或失败的那次请求")
                attempt = new_id()
                db.execute('UPDATE storage_chat SET ended_at=? WHERE turn_id=? AND ended_at IS NULL', (self.clock(),turn_id))
                db.execute("UPDATE free_turn SET status='queued',reason=NULL,attempt=? WHERE id=?",(attempt,turn_id))
                db.execute('INSERT INTO storage_chat VALUES(?,?,?,NULL)', (attempt,turn_id,self.clock()))
                return dict(turnId=turn_id,attemptId=attempt)
            return self._command(db,command_id,"retry_free_chat",[project_id,turn_id,expected_attempt],retry)

    @staticmethod
    def _free_turn(db,project_id,turn_id):
        return row(db,"SELECT t.* FROM free_turn t JOIN free_thread f ON t.thread_id=f.id WHERE f.project_id=? AND t.id=?",project_id,turn_id)

    def stop_free_chat(self,project_id,turn_id,expected_attempt):
        with transaction(self.db_path) as db:
            t = self._free_turn(db,project_id,turn_id)
            if t["attempt"]!=expected_attempt or t["status"] not in {"queued","running","stopped"}:
                raise Conflict("此请求状态已改变")
            db.execute("UPDATE free_turn SET status='stopped' WHERE id=?",(turn_id,))
        if t["status"]=="running":
            self.executors[t["provider"]].cancel(expected_attempt)
        return dict(turnId=turn_id)

    def execute_free_chat(self,turn_id,attempt):
        with transaction(self.db_path) as db:
            if not db.execute("UPDATE free_turn SET status='running' WHERE id=? AND attempt=? AND status='queued'",(turn_id,attempt)).rowcount:
                return
            t = row(db,"SELECT * FROM free_turn WHERE id=?",turn_id)
        edits = []
        reply, reason, status = None, None, "failed"
        try:
            manifest,digest = self.store.load_manifest(t["manifest_relpath"])
            if digest!=t["input_hash"]:
                raise ValueError("输入清单指纹不符")
            entries = self.store.manifest_texts(manifest)
            work = self.store.run_dir(attempt)/"work"
            work.mkdir(parents=True,exist_ok=True)
            images = []
            for e in entries:
                if e.get("binary"):
                    data = self.store.read_blob(e["sha256"])
                    with Image.open(BytesIO(data)) as image:
                        extension = image.format.lower()
                    path = work/f"{len(images)+1}.{extension}"
                    self.store.materialize_blob(e["sha256"], path)
                    images.append(path)
            purpose = manifest["purpose"]
            research = purpose == "topic_research"
            guidance = topicresearch.INSTRUCTIONS if research else "根据本次选用的创作方法和资料，直接回答本条消息。将答复放入 reply 字段。"
            if any(e["role"] == "project_context" for e in entries):
                guidance += ("你是本项目的导演助手。讨论创意时直接接着聊。用户询问进度或下一步时，以本轮项目概况为准，"
                             "结合待办、剧集状态和候选选题，给出具体可做的下一步。空项目可以先聊想法，"
                             "也可以在选题池加入候选，或在项目页新增一集。把建议与已发生的事实分清。"
                             "对话中的建议不会自动创建选题、开工或改稿。")
            if manifest.get("stage") in ("package","finecut"):
                guidance += "按最新制作记录回答。区分待生成、已审、已交接与已出成片。正常流程由专家自动推进。"
            schema = REPLY_SCHEMA
            edit_delivery=manifest.get("stageBasis",{}).get("deliveryId") if manifest.get("stage")=="finecut" else None
            mode = manifest.get("finecutMode")
            if mode == "discuss":
                edit_delivery = None
                guidance += "本条只讨论，不安排改片。"
            if edit_delivery:
                schema=filmedit.SCHEMA
                guidance+=filmedit.GUIDANCE
                if mode == "revise":
                    guidance += "导演选择了修改成片，请安排实际修改。"
            if research:
                count = manifest.get("candidateCount") or topicresearch.requested_count(t["text"])
                schema = topicresearch.result_schema(count)
                if manifest.get("chosenCount") is not None:
                    guidance += "导演已在数量选项中确认本次数量，以该选项为准。"
                guidance += f"本次必须恰好生成 {count} 张新选题卡，参考故事不计入数量。"
                history = [json.loads(e["text"])["premise"] for e in entries
                           if e["role"] == "research_source" and e["name"].startswith(("已发布作品 · ", "已拍作品 · ", "正式候选 · "))]
                entries, citations = topicresearch.research_inputs(entries)
            prompt = prompt_from(entries, guidance, purpose)
            choice = manifest.get("executorConnection",manifest.get("creator",{}))
            request = ExecRequest(attempt,purpose,purpose,prompt,schema,"new",work,
                                  model=choice.get("model"), effort=choice.get("effort"),connection=choice,
                                  timeout=int(self.get_setting("executor_timeout",600)),images=images)
            out = self.executors[t["provider"]].start(request)
            if research and out.ok and isinstance(out.structured,dict):
                cards = out.structured.get("candidates")
                if isinstance(cards,list) and len(cards) <= count:
                    topicresearch.research_reply(out.structured, citations, len(cards))
                    cards = topicresearch.distinct_cards(cards, history)
                if isinstance(cards,list) and len(cards) < count:
                    missing = count - len(cards)
                    with connect(self.db_path) as db:
                        active=db.execute("SELECT 1 FROM free_turn WHERE id=? AND attempt=? AND status='running'",(turn_id,attempt)).fetchone()
                    if not active:
                        return
                    extra = replace(request, attempt=2, schema=topicresearch.result_schema(missing),
                        prompt=prompt + f"\n本次为补充，已有 {len(cards)} 个选题，只返回另外 {missing} 个。避开这些标题和故事：" + dumps(cards))
                    out = self.executors[t["provider"]].start(extra)
                    if out.ok:
                        topicresearch.research_reply(out.structured, citations, missing)
                        combined = cards + out.structured["candidates"]
                        if len(topicresearch.distinct_cards(combined, history)) != count:
                            raise ValueError("补充的选题仍与旧题或本轮其他卡重复")
                        out = replace(out, structured={"candidates":combined})
            if research and out.ok:
                reply, status = topicresearch.research_reply(out.structured, citations, count), "succeeded"
            elif out.ok:
                body = out.structured
                if not isinstance(body,dict) or not isinstance(body.get("reply"),str) or not body["reply"].strip():
                    raise ValueError("执行器没有返回有效的对话正文")
                if edit_delivery:
                    basis=json.loads(next(e["text"] for e in entries if e["role"]=="film_context"))
                    edits=filmedit.operations(body.get("edits",[]),[item["段"] for item in basis["分段"]])
                    if mode == "revise" and not edits:
                        if not basis.get("支持专家重排"):
                            raise ValueError("没有生成可执行的修改，请指明希望调整的分段或声音")
                        edits = [dict(action="replan", segment="", value=0)]
                        body = dict(reply="将按这条反馈和采用剧本重新编排剪点。")
                    if any(op["action"] == "replan" for op in edits) and not basis.get("支持专家重排"):
                        raise ValueError("本项目可修复指定分段的对白、剪点、速度和音量，请指明修改位置")
                reply,status = body["reply"],"succeeded"
                if edits:
                    reply += "\n\n剪辑修改已排队，素材齐备后自动渲染。"
            else:
                status = "waiting_quota" if out.error_class=="quota" else "failed"
                reason = out.diagnostic or out.error_class
        except Exception as exc:
            reason = str(exc)[:500]
        with transaction(self.db_path) as db:
            if status == 'succeeded':
                db.execute('UPDATE storage_chat SET ended_at=? WHERE attempt_id=?', (self.clock(),attempt))
            updated=db.execute("UPDATE free_turn SET status=?,reply=?,reason=? WHERE id=? AND attempt=? AND status='running'",
                       (status,reply,reason,turn_id,attempt))
            if updated.rowcount and status=="succeeded" and edits:
                db.execute("INSERT INTO finecut_edit VALUES(?,?,?)",(turn_id,edit_delivery,dumps(edits)))

    def add_reply_to_canvas(self,project_id,turn_id,episode_id,expected_revision,command_id=None):
        with transaction(self.db_path) as db:
            def add():
                self._episode_in(db,episode_id,project_id)
                t = self._free_turn(db,project_id,turn_id)
                if t["status"] != "succeeded":
                    raise Rejected("请选择已收到的对话回复")
                view = self.canvas_view(episode_id)
                doc = view["doc"]
                node_id = "free-chat-"+turn_id
                if any(n["id"] == node_id for n in doc["nodes"]):
                    return dict(nodeId=node_id,revision=view["revision"])
                y = max((n["y"] for n in doc["nodes"]),default=-250)+320
                doc["nodes"].append(dict(id=node_id,type="text",x=270,y=y,title=t["text"][:32],
                                         data=dict(body=t["reply"],sourceTurnId=turn_id)))
                saved = self._write_canvas(db,episode_id,doc,expected_revision)
                return dict(nodeId=node_id,revision=saved["revision"])
            return self._command(db,command_id,"add_reply_to_canvas",[project_id,turn_id,episode_id,expected_revision],add)
