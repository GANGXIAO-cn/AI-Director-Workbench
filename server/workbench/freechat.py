"""Project conversations with explicitly selected, versioned inputs."""
from io import BytesIO

from PIL import Image

from .baselineview import block_by_key
from .storage import sha256_bytes
from .custommethods import edition
from .db import connect, dumps, new_id, transaction
from .errors import Conflict, NotFound, Rejected, row
from .executors.base import ExecRequest
from .prompts import prompt_from
from . import topicresearch

REPLY_SCHEMA = {"type":"object", "properties":{"reply":{"type":"string"}}, "required":["reply"], "additionalProperties":False}


class FreeChatMixin(topicresearch.TopicResearchMixin):
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
                    t["methods"] = manifest["methodVersions"]
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
        return dict(assets=assets,canvases=canvases,providers=list(self.executors))

    def send_free_chat(self, project_id, text, provider, thread_id=None, previous=None, methods=(), images=(),
                       canvas=None, canvas_revision=None, command_id=None, baseline=None, episode_id=None, stage=None, source_version=None):
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
        if episode_id is not None or stage is not None or source_version is not None:
            if not episode_id or stage not in ("story","script","plan","prompts","publish"):
                raise Rejected("请选择本集的写作阶段")
            payload.update(episodeId=episode_id, stage=stage, sourceVersion=source_version)
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
                choice = header.get("creator",{})
                header["executorConnection"] = self.run_connection(provider,choice.get("model"),choice.get("effort"))
                digest, rel = self.store.freeze(turn,header,entries)
                db.execute("INSERT INTO free_turn(id,thread_id,text,provider,status,attempt,input_hash,manifest_relpath) "
                           "VALUES(?,?,?,?,'queued',?,?,?)",(turn,tid,text,provider,attempt,digest,rel))
                return dict(threadId=tid,turnId=turn,attemptId=attempt)
            return self._command(db,command_id,"send_free_chat",payload,create)

    def retry_free_chat(self, project_id, turn_id, expected_attempt, command_id=None):
        with transaction(self.db_path) as db:
            def retry():
                turn = self._free_turn(db,project_id,turn_id)
                latest = row(db,"SELECT id FROM free_turn WHERE thread_id=? ORDER BY rowid DESC LIMIT 1",turn["thread_id"])
                if turn["attempt"]!=expected_attempt or latest["id"]!=turn_id or turn["status"] not in {"failed","waiting_quota","stopped"}:
                    raise Conflict("仅可重试对话末尾已停止或失败的那次请求")
                attempt = new_id()
                db.execute("UPDATE free_turn SET status='queued',reason=NULL,attempt=? WHERE id=?",(attempt,turn_id))
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
                    path.write_bytes(data)
                    images.append(path)
            purpose = manifest["purpose"]
            research = purpose == "topic_research"
            guidance = topicresearch.INSTRUCTIONS if research else "根据本次选用的创作方法和资料，直接回答本条消息。将答复放入 reply 字段。"
            prompt = prompt_from(entries, guidance, purpose)
            choice = manifest.get("executorConnection",manifest.get("creator",{}))
            request = ExecRequest(attempt,purpose,purpose,prompt,topicresearch.SCHEMA if research else REPLY_SCHEMA,"new",work,
                                  model=choice.get("model"), effort=choice.get("effort"),connection=choice,
                                  timeout=int(self.get_setting("executor_timeout",600)),images=images)
            out = self.executors[t["provider"]].start(request)
            if research and out.ok:
                reply, status = topicresearch.research_reply(out.structured, entries), "succeeded"
            elif out.ok:
                body = out.structured
                if not isinstance(body,dict) or not isinstance(body.get("reply"),str) or not body["reply"].strip():
                    raise ValueError("执行器没有返回有效的对话正文")
                reply,status = body["reply"],"succeeded"
            else:
                status = "waiting_quota" if out.error_class=="quota" else "failed"
                reason = out.diagnostic or out.error_class
        except Exception as exc:
            reason = str(exc)[:500]
        with transaction(self.db_path) as db:
            db.execute("UPDATE free_turn SET status=?,reply=?,reason=? WHERE id=? AND attempt=? AND status='running'",
                       (status,reply,reason,turn_id,attempt))

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
