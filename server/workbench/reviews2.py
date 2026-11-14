"""Two-pass prompt review per segment (cold read → comparison) with partial re-review."""
import json

from .decisions import decision_entries
from . import supplements, prompts, timeline, acting, imagechoices, shotrefs, promptsignals
from .db import connect, dumps, new_id, transaction
from .errors import Rejected, NotFound, loads, row
from .storage import canonical, sha256_bytes
from .stages import render
from .images import image_attachment
from .specs import creative_spec
from .imageassess import ImageAssessmentMixin
from .progress_expert import provisional_image_issues


class PromptReviewMixin(ImageAssessmentMixin):
    def _segment_material(self, db, version_id, segment):
        v = loads(row(db, "SELECT * FROM version WHERE id=?", version_id), "content")
        art = row(db, "SELECT * FROM artifact WHERE id=?", v["artifact_id"])
        if art["stage"] != "prompts":
            raise Rejected("两遍独审针对提示词版本")
        entry = (v["content"].get("segments") or {}).get(segment)
        if not entry:
            raise Rejected(f"版本中没有 {segment}")
        refs = []
        for ref in entry.get("refs", []):
            av = self.asset_version(ref["assetVersionId"], db)
            refs.append(dict(ref, sha256=av["sha256"], name=av["name"]))
        accepted=[dict(issue) for ref in refs for issue in db.execute("SELECT * FROM accepted_image_issue WHERE asset_version_id=?",(ref["assetVersionId"],))]
        provisional=[i for ref in refs for i in provisional_image_issues(db,art["episode_id"],ref["assetVersionId"])]
        bindings = [(r["label"], r.get("role") or "用途未写", r["sha256"]) for r in refs]
        ids = [r["assetVersionId"] for r in refs]
        txt = entry.get("txt", "")
        own = sha256_bytes(canonical(dict(txt=txt, refs=bindings, versions=ids)))
        if accepted:own=sha256_bytes(canonical(dict(contentHash=own,acceptedImageIssues=accepted)))
        # Pre-versioned scopes can only be reused if every matching historical
        # review read this same ordered set of images. Mixed-media runs used to
        # send sound/video as PNG attachments and must be repeated.
        if provisional:own=sha256_bytes(canonical(dict(contentHash=own,provisionalImageIssues=provisional)))
        if not accepted and not provisional and all(r["kind"] == "image" for r in refs):
            old = sha256_bytes(canonical(dict(txt=txt, refs=bindings)))
            prior = db.execute("SELECT DISTINCT v.content FROM review r JOIN version v ON v.id=r.version_id "
                               "WHERE v.artifact_id=? AND r.scope=? AND r.scope_hash=? AND r.pass='cold_read'",
                               (art["id"], segment, old)).fetchall()
            if prior and all([r["assetVersionId"] for r in json.loads(v[0])["segments"][segment]["refs"]] == ids for v in prior):
                own = old
        return v, art, entry, refs, own

    def _script_scene_packets(self, db, episode, plan):
        script = db.execute("SELECT v.id,v.display_no,v.content FROM artifact a JOIN version v ON v.id=a.adopted_version_id "
                            "WHERE a.episode_id=? AND a.stage='script'", (episode,)).fetchone()
        content = json.loads(script["content"]) if script else {}
        scenes = {scene["id"]: scene for scene in content.get("scenes", [])}
        segments = timeline.layout(plan)["segments"]
        budgets = timeline.script_budget(content,plan,segments)["segments"]
        return {segment["segment"]: dict(
            scriptVersion=script["id"] if script else None, scriptRevision=script["display_no"] if script else None,
            scenes=[dict(sceneId=g["scene"], original=scenes.get(g["scene"]), shots=g["shots"],
                         unverifiedQuotes=g["unverifiedQuotes"]) for g in segment["scenes"]])
            for segment in budgets}

    def prompt_scene_sources(self, episode_id):
        with connect(self.db_path) as db:
            return self._script_scene_packets(db, episode_id, self._adopted_plan(db, episode_id) or {})

    def _review_context(self, db, art, version):
        episode = art["episode_id"]
        kickoff = self._kickoff(db, episode)
        story = self._adopted_content(db, episode, "story") or {}
        plan = self._adopted_content(db, episode, "plan") or {}
        segments = timeline.layout(plan)["segments"]
        order = [seg["id"] for seg in segments]
        pack, profile = self._episode_method(db, episode)
        settings_text = prompts.stage_settings(pack, "comparison", profile, prompts.render_settings(kickoff["settings_snapshot"]))
        shared = [
            dict(role="adopted_story", name="story.md", text=render("story", story)),
            dict(role="project_settings", name="settings.txt", text=settings_text),
            dict(role="intent", name="intent.txt", text=kickoff["goal"] or "（未写）"),
        ]
        shared.append(dict(role="requirements", name="开工原文.txt",
                           text=prompts.render_kickoff(kickoff, self._source_text(db, kickoff))))
        characters = (self._adopted_content(db, episode, "characters") or {}).get("characters", [])
        designs = (self._adopted_content(db, episode, "asset_design") or {}).get("assets", [])
        performances = (self._adopted_content(db, episode, "acting") or {}).get("scenes", [])
        shared += self._stage_reference_entries(episode, "prompts")
        spec = dict(role="episode_spec", name="spec.json", text=canonical(creative_spec(kickoff["spec"])).decode())
        sources = []
        for source in db.execute("SELECT * FROM episode_source WHERE episode_id=? ORDER BY kind", (episode,)):
            changes = self._episode_block_history(db,episode,source)
            sources.append((source,changes))
        dynamic = any(changes for _,changes in sources)
        names = self._cast_names(db,episode) if dynamic else {}
        if not dynamic:
            shared += self._source_entries(db,episode,"prompts") + [spec]
        context = {}
        matrix = self._cast_matrix(db, episode)
        cells = {r["segment"]: r["cells"] for r in matrix["rows"]}
        required = imagechoices.requirements(db, episode)
        scene_packets = self._script_scene_packets(db, episode, plan)
        for i, seg in enumerate(segments):
            sid = seg["id"]
            shots = [shot for shot in plan.get("shots", []) if shot["segment"] == sid]
            neighbours = [order[j] for j in (i - 1, i + 1) if 0 <= j < len(order)]
            plan_data = dict(segment=seg, shots=shots)
            if shotrefs.plan_enabled(plan) and sid in version["content"].get("segments", {}):
                try:
                    plan_data["shotReferences"] = shotrefs.segment_packet(self,db,episode,plan,matrix,sid,version["content"]["segments"][sid])
                except (ValueError, KeyError, TypeError, NotFound, Rejected) as exc:
                    plan_data["shotReferenceError"] = str(exc)
            if required:
                refs = version["content"].get("segments", {}).get(sid, {}).get("refs", [])
                actual = {r[0] for ref in refs if ref.get("kind") == "image" for r in
                          db.execute("SELECT sha256 FROM asset_version WHERE id=?", (ref.get("assetVersionId"),))}
                shot_ids = {s["id"] for s in shots}
                rules = [r for r in required if sid in r["targetSegments"] or shot_ids.intersection(r["shotIds"])
                         or r["sha256"] in actual]
                if rules:
                    plan_data["requiredImages"] = rules
            if cells.get(sid):
                plan_data["castBindings"] = cells[sid]
                selected = {r: matrix["roster"][r] for r, cell in cells[sid].items()
                            if cell["status"] != "absent" and matrix["roster"][r].get("assetVersionId")}
                if selected:
                    plan_data["roleBaselines"] = selected
            if any(shot.get("dialogueCues") for shot in shots):
                plan_data["dialogueTiming"] = acting.report(plan, {sid})
            plan_text = canonical(plan_data).decode()
            scoped = self._review_baselines(seg,version["content"]["segments"].get(sid,{}),
                                            cells.get(sid,{}),names,sources) + [spec] if dynamic else []
            context[sid] = shared + scoped + [dict(role="script_scene_source", name=f"{sid}-剧本原文.json", text=canonical({"scenes": scene_packets[sid]["scenes"]}).decode()),
                                               dict(role="plan_segment", name=f"{sid}-plan.json", text=plan_text)]
            scenes = {item["sceneId"] for item in scene_packets[sid]["scenes"]}
            context[sid].extend(supplements.entries(db,episode,scenes))
            context[sid].extend(decision_entries(db, episode, "review", scenes, segment=sid))
            roles = {role for shot in shots for role in shot.get("visibleRoles", [])}
            states=[r for r in matrix["roleStates"] if r["role"] in roles]
            if states:
                context[sid].append(dict(role="role_states",name=f"{sid}-角色状态.json",text=canonical(states).decode()))
            asset_ids = {key for shot in shots for key in shot.get("propStates", {})}
            for ref in version["content"].get("segments", {}).get(sid, {}).get("refs", []):
                asset_ids.update(ref.get(key) for key in ("assetId", "assetVersionId") if ref.get(key))
                if ref.get("assetVersionId"):
                    asset_ids.add(self.asset_version(ref["assetVersionId"], db)["asset_id"])
            for role, rows in (
                ("adopted_characters", [r for r in characters if r["id"] in roles]),
                ("adopted_asset_design", [r for r in designs if r["id"] in asset_ids or r.get("roleId") in roles]),
                ("adopted_acting", [r for r in performances if r["sceneId"] in scenes]),
            ):
                if rows:
                    context[sid].append(dict(role=role, name=f"{sid}-{role}.json", text=canonical(rows).decode()))
            for neighbour in neighbours:
                txt = version["content"]["segments"].get(neighbour, {}).get("txt", "")
                context[sid].append(dict(role="neighbour_script_scene_source", name=f"{neighbour}-接点原文.json",
                                         text=canonical({"scenes":scene_packets[neighbour]["scenes"]}).decode()))
                context[sid].append(dict(role="neighbour_txt", name=f"{neighbour}.txt", text=txt))
        return context

    def _comparison_inputs(self, db, artifact_id, segment, own, context):
        cold = db.execute("SELECT observations FROM review WHERE version_id IN "
                          "(SELECT id FROM version WHERE artifact_id=?) AND pass='cold_read' AND scope=? "
                          "AND scope_hash=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                          (artifact_id, segment, own)).fetchone()
        if not cold:
            return None
        return [dict(role="cold_read_observations", name="pass1.json", text=cold["observations"]), *context]

    def _segment_hashes(self, db, version_id, context=None):
        version = loads(row(db, "SELECT * FROM version WHERE id=?", version_id), "content")
        art = row(db, "SELECT * FROM artifact WHERE id=?", version["artifact_id"])
        if context is None:
            context = self._review_context(db, art, version)
        own, comp = {}, {}
        for segment, entries in context.items():
            if segment not in version["content"].get("segments", {}):
                continue
            if self._prompt_reference_errors(db, art["episode_id"], {segment: version["content"]["segments"][segment]}):
                continue
            own[segment] = self._segment_material(db, version_id, segment)[4]
            extra = self._comparison_inputs(db, art["id"], segment, own[segment], entries)
            comp[segment] = sha256_bytes(canonical([own[segment], extra])) if extra else None
        return list(context), own, comp

    def _review_task(self, db, art):
        r = db.execute("SELECT id FROM task WHERE artifact_id=? AND goal='两遍独审' ORDER BY created_at DESC", (art["id"],)).fetchone()
        if r:
            return r[0]
        kickoff = self._kickoff(db, art["episode_id"])
        tid, thread = new_id(), new_id()
        db.execute("INSERT INTO task(id,project_id,episode_id,artifact_id,stage,goal,max_rounds,executor_plan,created_at) "
                   "VALUES(?,?,?,?,?,?,?,?,?)", (tid, art["project_id"], art["episode_id"], art["id"], "prompts", "两遍独审",
                                                 2, dumps({**kickoff["executor_plan"], **self._stage_executors(db,art["episode_id"],"prompts")}), self.clock()))
        db.execute("INSERT INTO thread VALUES(?,?,?)", (thread, tid, f"prompt-review:{art['id']}"))
        return tid

    def start_prompt_review(self, version_id, segment, pass_name, command_id, provider=None, model=None, effort=None):
        if pass_name not in ("cold_read", "comparison"):
            raise Rejected("遍次只能是 cold_read 或 comparison")
        payload = dict(v=version_id, s=segment, p=pass_name, pr=provider)
        if model or effort:
            payload.update(model=model, effort=effort)
        with transaction(self.db_path) as db:
            return self._command(db, command_id, "prompt_review", payload, lambda: self._queue_segment_review(
                db,version_id,segment,pass_name,provider,model,effort))

    def _queue_segment_review(self, db, version_id, segment, pass_name, provider=None, model=None, effort=None, context=None):
        whole_pass = context is not None
        if not whole_pass:
            version = loads(row(db, "SELECT * FROM version WHERE id=?", version_id), "content")
            artifact = row(db, "SELECT episode_id FROM artifact WHERE id=?", version["artifact_id"])
            errors = self._prompt_reference_errors(db, artifact["episode_id"], {segment: version["content"].get("segments", {}).get(segment)})
            if errors:
                raise Rejected("；".join(errors))
        v, art, entry, refs, own = self._segment_material(db, version_id, segment)
        tid = self._review_task(db, art)
        plan_exec = json.loads(row(db, "SELECT executor_plan FROM task WHERE id=?", tid)["executor_plan"])
        review_slot = self._stage_executors(db,art["episode_id"],"prompts").get("review",plan_exec["review"])
        prov = provider or review_slot["provider"]
        if prov not in self.executors or self._isolation_status(prov) == "unverified":
            raise Rejected("审核执行器的会话隔离未验证")
        if refs and not getattr(self.executors[prov], "reads_images", True):
            raise Rejected(f"{prov} 在当前隔离配置下不能读图，请选择能读图的审核执行器")
        images = [image_attachment(r["label"], self.store.read_blob(r["sha256"])) for r in refs if r["kind"] == "image"]
        txt = dict(role="segment_txt", name=f"{segment}.txt", text=entry.get("txt", ""))
        order_note = dict(role="reference_order", name="order.txt",
                          text="\n".join(f"{r['label']}：{r.get('role') or '用途未写'}" for r in refs))
        if any(r["kind"] != "image" for r in refs):
            media = "视频和音频仅列出输入标签，本次文本审查未审看视频或试听音频。"
            order_note["text"] += "\n" + media
        else:
            media = ""
        if pass_name == "cold_read":
            # Author notes can describe the intended result. Cold readers see labels and pixels.
            labels = dict(role="reference_order",name="order.txt",text="\n".join([r["label"] for r in refs] + ([media] if media else [])))
            entries, scope_hash = [txt, labels, *images], own
        else:
            if context is None:
                context = self._review_context(db, art, v)
            if segment not in context:
                raise Rejected("已采用方案中没有该段")
            extra = self._comparison_inputs(db, art["id"], segment, own, context[segment])
            if not extra:
                raise Rejected("须先完成本段冷读并保存观察")
            entries = [txt, order_note, *images, *extra]
            plan_entry = next(e for e in extra if e["role"] == "plan_segment")
            uploaded_images = {r["sha256"] for r in refs if r["kind"] == "image"}
            for n, (role, baseline) in enumerate(json.loads(plan_entry["text"]).get("roleBaselines", {}).items(), 1):
                if baseline["sha256"] not in uploaded_images:
                    att = image_attachment(f"角色基准{n}", self.store.read_blob(baseline["sha256"]))
                    entries.append(dict(att, role="cast_baseline_image"))
                    entries.append(dict(role="cast_baseline_label", name=f"角色基准{n}.txt",
                                        text=f"角色基准{n}是 {role} 的独立对照图。本段实际上传图片以 order.txt 为准。"))
            scope_hash = sha256_bytes(canonical([own, extra]))
        if pass_name == "comparison":
            from .materialcontext import RULE, material_context
            inherited=material_context(db,art['episode_id'],refs)
            if inherited:
                entries.append(dict(role='material_use_conclusions',name='已评估素材及当前用途.json',text=dumps(inherited)))
                entries.append(dict(role='request',name='素材采用结论接续规则',text=RULE))
            entries.append(dict(role='request',name='独审职责',text='仅审核最终提示词的翻译、组装与既定方案是否一致。检查漏写、错引、编号及动作声音表达。小问题直接放行：不改变主要动作结果、角色与素材引用、采用台词的视线顺序、表情细节、形容词或措辞差异，只记普通建议，不归为source、format、decision或必需设定违反，不要求返修。只有实际错引、缺失关键动作结果或台词、明确违背导演决定等影响制作的错误才报告阻断。继承已采用素材用途，不重新评选图片；静态环境图不用证明运行或声音，身份图不用演出动作。图片固有的旧差异不作为新增问题；若发现真实新增上游矛盾，指出来源阶段与变化证据，不要求提示词换图或重写上游。'))
            accepted=[dict(issue) for ref in refs for issue in db.execute('SELECT * FROM accepted_image_issue WHERE asset_version_id=?',(ref['assetVersionId'],))]
            if accepted:
                entries.append(dict(role='accepted_image_issue',name='用户已接受的已知问题.json',text=dumps(accepted)))
                entries.append(dict(role='request',name='已知问题处理规则',text='清单中的问题不再列为 issues，只在 observations 中注明仍然存在。其他未接受的问题照常审查。'))
            provisional=[i for ref in refs for i in provisional_image_issues(db,art['episode_id'],ref['assetVersionId'])]
            if provisional:
                entries.append(dict(role='provisional_image_issue',name='AI暂用的未解决图片问题.json',text=dumps(provisional)))
                entries.append(dict(role='request',name='暂用问题处理规则',text='仅清单中的同一图片问题已决定暂用，不重复要求修图；在 observations 中保留未解决说明，不称为用户接受或质量通过。其他问题照常审查。'))
        if not whole_pass and (segment,pass_name,scope_hash) in self._active_prompt_reviews(db,art["id"]):
            raise Rejected(f"{segment} 此遍相同输入已在排队或审读")
        run_id = self._queue_run(db, tid, pass_name, None, "", target_version_id=version_id, provider=prov, model=model, effort=effort,
                                 entries=entries, extra_header=dict(scope=segment, scopeHash=scope_hash))
        return dict(runId=run_id, taskId=tid, scopeHash=scope_hash)

    def _active_prompt_reviews(self, db, artifact_id):
        active = {}
        for run in db.execute("SELECT r.id,r.status,r.purpose,r.manifest_relpath FROM run r "
                              "JOIN version v ON v.id=r.target_version_id WHERE v.artifact_id=? "
                              "AND r.purpose IN ('cold_read','comparison') AND r.status IN ('queued','running')",
                              (artifact_id,)):
            manifest, _ = self.store.load_manifest(run["manifest_relpath"])
            key = (manifest["scope"], run["purpose"], manifest["scopeHash"])
            active[key] = dict(runId=run["id"],state=run["status"])
        return active

    def start_prompt_review_batch(self, version_id, pass_name, command_id, provider=None, model=None, effort=None):
        if pass_name not in ("cold_read","comparison"):
            raise Rejected("请选择第一遍冷读或第二遍对照")
        with transaction(self.db_path) as db:
            def enqueue():
                version = loads(row(db,"SELECT * FROM version WHERE id=?",version_id),"content")
                art = row(db,"SELECT * FROM artifact WHERE id=?",version["artifact_id"])
                if art["stage"] != "prompts":
                    raise Rejected("整版送审须选择提示词版本")
                context = self._review_context(db,art,version)
                entries = version["content"].get("segments",{})
                if not context or set(entries) != set(context):
                    raise Rejected("提示词段号与已采用制作方案不一致，请核对整版段落")
                errors = self._prompt_reference_errors(db,art["episode_id"],entries)
                if errors:
                    raise Rejected("；".join(errors))
                order, own, comp = self._segment_hashes(db,version_id,context)
                active = self._active_prompt_reviews(db,art["id"])
                if pass_name == "comparison" and any(not comp[sid] or (sid,"cold_read",own[sid]) in active for sid in order):
                    raise Rejected("须先完成整版各段冷读并保存观察")
                queued = {}
                for sid in order:
                    digest = (own if pass_name == "cold_read" else comp)[sid]
                    if (sid,pass_name,digest) in active:
                        continue
                    state = self._seg_review_state(db,art["id"],sid,pass_name,digest)
                    if state["state"] == "needed":
                        queued[sid] = self._queue_segment_review(db,version_id,sid,pass_name,provider,model,effort,context=context)["runId"]
                return dict(runIds=list(queued.values()),segments=queued)
            payload = dict(version=version_id, passName=pass_name)
            payload.update({key:value for key,value in dict(provider=provider,model=model,effort=effort).items() if value is not None})
            return self._command(db,command_id,"prompt_review_batch",payload,enqueue)

    def _seg_review_state(self, db, artifact_id, segment, pass_name, scope_hash):
        r = db.execute("SELECT r.* FROM review r JOIN version v ON v.id=r.version_id WHERE v.artifact_id=? AND r.pass=? "
                       "AND r.scope=? AND r.scope_hash=? ORDER BY r.created_at DESC,r.rowid DESC", (artifact_id, pass_name, segment,
                                                                                       scope_hash)).fetchone()
        if not r:
            return dict(state="needed")
        issues = [dict(i) for i in db.execute(
            "SELECT i.*, (SELECT reason FROM waiver w WHERE w.issue_id=i.id AND w.scope_hash=?) AS waiver_reason "
            "FROM issue i WHERE review_id=? ORDER BY ordinal", (scope_hash, r["id"]))]
        blocking = [i["id"] for i in issues if i["blocking"] and not i["waiver_reason"]]
        open_ = [i["id"] for i in issues if not i["blocking"] and not i["waiver_reason"]]
        state = ("passed" if r["original_decision"] == "pass" else "blocked" if blocking
                 else "pending_decision" if open_ else "waived")
        obs = json.loads(r["observations"])
        return dict(state=state, reviewId=r["id"], runId=r["run_id"], decision=r["original_decision"], issues=issues, reviewer=r["reviewer"],
                    versionId=r["version_id"], observation=obs[0]["evidence"] if obs else None)

    def prompt_signals(self, version_id):
        with connect(self.db_path) as db:
            db.execute("BEGIN")
            version = row(db,"SELECT v.content,v.display_no,a.episode_id,a.stage FROM version v "
                          "JOIN artifact a ON a.id=v.artifact_id WHERE v.id=?",version_id)
            if version["stage"] != "prompts":
                raise Rejected("请选择要通读的提示词版本")
            try:
                standards = {kind:json.loads(self.store.read_blob(digest)) for kind,digest in
                             self._prompt_version_sources(db,version_id,version["episode_id"]).items() if kind != "soundStandard"}
                segments, versions = [], set()
                for sid,entry in json.loads(version["content"]).get("segments",{}).items():
                    text = entry.get("txt","") if isinstance(entry,dict) else ""
                    # Use the headings in this saved TXT, even if the plan has since changed.
                    strategy = next((kind for kind,heading in (("grid","【分镜参考】"),("flex","【参考素材】"))
                                     if isinstance(text,str) and heading in text),None)
                    segments.append(dict(id=sid,strategy=strategy,txt=text,
                                         prompt_format=entry.get("prompt_format") if isinstance(entry,dict) else None))
                    if isinstance(entry,dict) and isinstance(entry.get("refs"),list):
                        versions.update(r["assetVersionId"] for r in entry["refs"] if isinstance(r,dict) and isinstance(r.get("assetVersionId"),str))
                ids = {}
                for asset in db.execute("SELECT a.id asset_id,v.id version_id FROM asset a JOIN asset_version v ON v.asset_id=a.id "
                                        "WHERE a.episode_id=? AND NOT EXISTS (SELECT 1 FROM fixed_role_asset f "
                                        "WHERE f.episode_id=a.episode_id AND f.asset_version_id=v.id) ORDER BY a.rowid,v.rowid",
                                        (version["episode_id"],)):
                    if asset["version_id"] in versions:
                        ids.update(dict.fromkeys((asset["asset_id"],asset["version_id"])))
                return dict(versionId=version_id,displayNo=version["display_no"],**promptsignals.report(segments,standards,ids))
            except (ValueError,TypeError,OSError) as exc:
                raise Rejected(f"无法读取此版本的通读材料：{exc}") from exc

    def prompt_review_status(self, version_id):
        with connect(self.db_path) as db:
            db.execute("BEGIN")
            order, own, comp = self._segment_hashes(db, version_id)
            art_id = row(db, "SELECT artifact_id FROM version WHERE id=?", version_id)["artifact_id"]
            active = self._active_prompt_reviews(db,art_id)
            out = {}
            for seg in order:
                if seg not in own:
                    continue
                cold = self._seg_review_state(db, art_id, seg, "cold_read", own[seg])
                cmp_ = self._seg_review_state(db, art_id, seg, "comparison", comp[seg])
                for result, name, digest in ((cold,"cold_read",own[seg]),(cmp_,"comparison",comp[seg])):
                    running = active.get((seg,name,digest))
                    if running:
                        result["activeRun"] = running
                ok = cold["state"] in ("passed", "waived") and cmp_["state"] in ("passed", "waived")
                out[seg] = dict(hash=own[seg], coldRead=cold, comparison=cmp_, ok=ok,
                                waived=cold["state"] == "waived" or cmp_["state"] == "waived")
        return out

    def waive_segment_issue(self, issue_id, reason="", command_id=None, *, accepted_issue=None, provisional_issue=None, usage_issue=None):
        with transaction(self.db_path) as db:
            def run():
                issue = row(db, "SELECT * FROM issue WHERE id=?", issue_id)
                rv = row(db, "SELECT * FROM review WHERE id=?", issue["review_id"])
                if not rv["scope"]:
                    raise Rejected("不是分段审查意见")
                accepted=False
                if accepted_issue:
                    _,_,entry,_,_=self._segment_material(db,rv['version_id'],rv['scope'])
                    vid=accepted_issue.get('assetVersionId');key=accepted_issue.get('issueKey')
                    accepted=any(ref['assetVersionId']==vid for ref in entry.get('refs',[])) and bool(db.execute('SELECT 1 FROM accepted_image_issue WHERE asset_version_id=? AND issue_key=?',(vid,key)).fetchone())
                    if not accepted:raise Rejected('该段引用的图没有对应用户接受记录')
                if provisional_issue:
                    _,art,entry,_,_=self._segment_material(db,rv['version_id'],rv['scope'])
                    vid=provisional_issue.get('assetVersionId');key=provisional_issue.get('issueKey')
                    accepted=any(ref['assetVersionId']==vid for ref in entry.get('refs',[])) and any(i['issue_key']==key for i in provisional_image_issues(db,art['episode_id'],vid))
                    if not accepted:raise Rejected('该段引用的图没有对应 AI 暂用记录')
                if usage_issue:
                    from .materialcontext import usage_clearances
                    _,art,entry,_,_=self._segment_material(db,rv['version_id'],rv['scope'])
                    records=usage_clearances(db,art['episode_id'],rv['scope'],entry)
                    keys=('assetVersionId','assessmentId','observationId','sourceIssue')
                    quote=usage_issue.get('quote')
                    accepted=any(all(r[k]==usage_issue.get(k) for k in keys) for r in records) and isinstance(quote,str) and bool(quote.strip()) and quote in entry.get('txt','')
                    if not accepted or self._prompt_reference_errors(db,art['episode_id'],{rv['scope']:entry}):
                        raise Rejected('缺少当前用途核对记录或落实限制的正文依据')
                if issue["blocking"] and not accepted:
                    raise Rejected("阻断问题不能放行，请改稿，或修改对应的决定、补充或项目设定")
                if not isinstance(reason, str):
                    raise Rejected("放行备注须为文字")
                wid = new_id()
                db.execute("INSERT INTO waiver(id,issue_id,version_id,reason,created_at,scope_hash) VALUES(?,?,?,?,?,?)",
                           (wid, issue_id, rv["version_id"], reason.strip() or "导演按本版放行（未填备注）", self.clock(), rv["scope_hash"]))
                return dict(waiverId=wid)
            return self._command(db, command_id, "waive_seg", dict(i=issue_id, r=reason, **({"acceptedIssue":accepted_issue} if accepted_issue else {}), **({"provisionalIssue":provisional_issue} if provisional_issue else {})), run)

    def _after_review(self, db, task, run, review_id, result):
        rv = row(db, "SELECT * FROM review WHERE id=?", review_id)
        if rv["pass"] in ("cold_read", "comparison") and result["decision"] != "pass":
            self._mark_deliveries_stale(db, task["episode_id"], "提示词独审出现新意见", reviewed=rv["version_id"], segment=rv["scope"])
        if rv["pass"] == "comparison" and result["decision"] == "pass":
            seg = rv["scope"]
            ep = task["episode_id"]
            # A comparison can finish after its inputs change. Only a current verdict clears the worklist.
            current = db.execute("SELECT adopted_version_id FROM artifact WHERE id=?", (task["artifact_id"],)).fetchone()[0]
            if current:
                _, _, hashes = self._segment_hashes(db, current)
                if hashes.get(seg) != rv["scope_hash"]:
                    return []
            db.execute("UPDATE impact SET state='resolved' WHERE episode_id=? AND state='pending' AND "
                       "(target_ref=? OR (target_ref LIKE 'joint:%' AND (target_ref LIKE ? OR target_ref LIKE ?)))",
                       (ep, f"prompts:{seg}", f"joint:{seg}-%", f"joint:%-{seg}"))
        return []

    def _resolve_adopted_prompt_impacts(self,db,eid,version_id):
        from .promptbasis import resolve
        resolve(self,db,eid,version_id)
