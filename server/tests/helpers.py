"""Shared fixtures: build episodes through the real service commands with fake executors / image provider."""
import json
import sqlite3

from workbench.executors.fake import FakeExecutor, draft
from workbench.images import FakeImageProvider, png_bytes
from workbench.service import Workbench

SEGMENTS_86 = {"G01": [6000, 7000], "G02": [6000, 7000], "G03": [3000, 2000, 4000, 4000], "G04": [6000, 6000],
               "G05": [6000, 6000], "G06": [6000, 6000], "G07": [5000, 6000]}
PUBLISH = dict(schema_version=1, title_prefix="NIGHT ", title_max_chars=100, body_fixed_lines=["Every night shift.", "More soon."],
               fixed_tags=["dog", "nightshift"], max_extra_tags=2, forbidden_chars=["<", ">"], website="example.invalid")


class Env:
    def __init__(self, tmp_path, image_script=None):
        self.codex, self.claude = FakeExecutor("codex"), FakeExecutor("claude")
        self.ag = FakeExecutor("antigravity", isolation="unverified", reads_images=False)
        self.images = FakeImageProvider("fake-image", image_script)
        self.wb = Workbench(tmp_path / "data", {"codex": self.codex, "claude": self.claude, "antigravity": self.ag},
                            image_providers={"fake-image": self.images})

    def drain(self, run_id):
        queue = [run_id]
        while queue:
            queue += self.wb.execute_run(queue.pop(0))

    def db(self):
        conn = sqlite3.connect(self.wb.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def art(self, eid, stage):
        return next(a for a in self.wb.episode_view(eid)["artifacts"] if a["stage"] == stage)

    # ---------------------------------------------------------------- building blocks
    def new_episode(self, config="_template", name="夜班柴犬", code="E01", project_id=None, idea="一只过分认真的柴犬第一次值夜班",
                    relation="independent", predecessors=(), plan=None,spec_overrides=None):
        wb = self.wb
        pid = project_id or wb.create_project(config, name)["projectId"]
        if config == "_template" and not project_id:
            wb.set_publish_settings(pid, PUBLISH)
        eid = wb.create_episode(pid, code)["episodeId"]
        wb.start_episode(eid, "idea", plan or {"create": {"provider": "codex"}, "review": {"provider": "claude"}},
                         idea_text=idea, relation=relation, predecessor_ids=list(predecessors),spec_overrides=spec_overrides)
        return pid, eid

    def adopt_story(self, eid, cid="s"):
        t = self.wb.create_task(eid, "story", "按已采用的上游成果完成本阶段", command_id=f"{eid}{cid}t")
        self.drain(t["runId"])
        story = self.art(eid, "story")
        v = story["versions"][-1]["id"]
        self.drain(self.wb.submit_review(v, f"{eid}{cid}r")["runId"])
        self.wb.adopt(story["id"], v, story["adopted_version_id"], f"{eid}{cid}a", and_continue=True)
        self.prepare_stage(eid, "script")
        return v

    def save_adopt(self, eid, stage, content, cid=None):
        self.prepare_stage(eid, stage)
        art = self.art(eid, stage)
        v = self.wb.save_manual(art["id"], art["adopted_version_id"], content, cid or f"{eid}{stage}save")["versionId"]
        res = self.wb.adopt(art["id"], v, art["adopted_version_id"], (cid or f"{eid}{stage}") + "adopt", and_continue=True)
        return v, res

    def prepare_stage(self, eid, stage):
        """Populate real intermediate documents required by the unified workflow.

        These are offline fixture inputs, not model output or creative validation.
        Tests still call the service's ordinary save/adopt/skip commands.
        """
        if stage not in ("script", "plan", "prompts", "publish"):
            return
        adopted = {a["stage"]:a["adopted_version_id"] for a in self.wb.episode_view(eid)["artifacts"]}
        if adopted.get("story") and not adopted.get("treatment"):
            fixtures = {
                "characters":dict(characters=[dict(id="afu",name="阿福",identity="值夜班的柴犬",goal="按规章完成交接",behavior="主动检查异常",identityRequired=True)]),
                "treatment":dict(scenes=[dict(id="场01",heading="内景 便利店 夜",event="巡店检查",result="发现抽屉未关"),dict(id="场02",heading="内景 收银台 夜",event="检查并登记",result="交接完成")],durationBudget="按事件预算，最终以镜头合计为准"),
            }
            for key in ("characters", "world", "treatment"):
                if key == "world":
                    self.wb.set_optional_stage(eid,key,True,"现实店铺，无额外世界规则")
                    continue
                aid = self.wb.open_stage(eid,key)["artifactId"]
                art = self.art(eid,key)
                if not art["adopted_version_id"]:
                    vid = self.wb.save_manual(aid,None,fixtures[key],f"fixture:{eid}:{key}:save")["versionId"]
                    self.wb.adopt(aid,vid,None,f"fixture:{eid}:{key}:adopt")
        if stage in ("plan", "prompts", "publish") and adopted.get("script") and not adopted.get("asset_design"):
            self.wb.set_optional_stage(eid,"stress",True,"离线结构夹具不作创作压力测试")
            aid = self.wb.open_stage(eid,"asset_design")["artifactId"]
            vid = self.wb.save_manual(aid,None,dict(strategy="lightweight",assets=[dict(id="store",kind="scene",description="便利店内景",imagePrompt="夜间便利店内景，收银台和冰柜位置清晰。")]),f"fixture:{eid}:assets:save")["versionId"]
            self.wb.adopt(aid,vid,None,f"fixture:{eid}:assets:adopt")
            self.wb.set_optional_stage(eid,"acting",True,"此夹具直接使用剧本已写出的可见行动")
        self.wb.open_stage(eid,stage)

    def script(self, eid):
        vid = self.save_adopt(eid, "script", dict(scenes=[
            dict(id="场01", heading="内景 便利店 夜", action="阿福巡店，推门确认上锁。"),
            dict(id="场02", heading="内景 收银台 夜", action="阿福发现抽屉没关，按规章推回并登记。", dialogue="",
                 sound="冰柜低鸣")]))[0]
        self.prepare_stage(eid, "plan")
        return vid

    def plan86(self, segments=SEGMENTS_86, strategy=None):
        shots, segs, n = [], [], 0
        for order, (sid, durs) in enumerate(segments.items(), 1):
            segs.append(dict(id=sid, order=order, strategy=(strategy or {}).get(sid, "flex")))
            for d in durs:
                n += 1
                shots.append(dict(id=f"S{n:02d}", segment=sid, order=n, duration_ms=d, camera="中景 固定",
                                  action=f"动作 {n}", origin=dict(scene="场02", line="阿福推回抽屉")))
        return dict(shots=shots, segments=segs, notes="")

    def image(self, pid, eid, name="阿福身份图", category="identity", grid=False, size=(64, 64), rgb=(10, 20, 30)):
        return self.wb.register_asset(pid, name, png_bytes(*size, rgb), category, episode_id=eid, grid=grid,
                                      cells=[dict(cell=c, moment=f"t{c}") for c in range(1, 10)] if grid else None)

    def prompts_for(self, plan, version_ids, txt_override=None):
        from workbench import timeline
        out = {}
        for seg in timeline.layout(plan)["segments"]:
            d = seg["duration_ms"]
            end = f"00:{d // 1000:02d}.{d % 1000:03d}"
            txt = (txt_override or {}).get(seg["id"]) or f"【开场】便利店夜。\n00:00.000-{end} 图片1 中的阿福推回抽屉，冰柜低鸣。"
            out[seg["id"]] = dict(refs=[dict(assetVersionId=version_ids[seg["id"]] if isinstance(version_ids, dict)
                                             else version_ids, label="图片1", kind="image", role="阿福身份")],
                                  txt=txt, sound=dict(bgm=False, ambience=["冰柜低鸣"], dialogue=[]))
        return dict(segments=out)

    def review_segments(self, version_id, segments, cid="rv"):
        for seg in segments:
            for p in ("cold_read", "comparison"):
                self.drain(self.wb.start_prompt_review(version_id, seg, p, f"{cid}{version_id}{seg}{p}")["runId"])

    def full_episode(self, config="_template", code="E01", project_id=None, segments=SEGMENTS_86, deliver=True, **kw):
        pid, eid = self.new_episode(config=config, code=code, project_id=project_id, **kw)
        if config == "puggy" and not project_id:
            self.wb.set_publish_settings(pid, PUBLISH)
        self.adopt_story(eid)
        self.script(eid)
        plan = self.plan86(segments)
        self.save_adopt(eid, "plan", plan)
        img = self.image(pid, eid)
        pv, _ = self.save_adopt(eid, "prompts", self.prompts_for(plan, img["versionId"]))
        self.review_segments(pv, list(segments))
        self.save_adopt(eid, "publish", dict(title="First Night Shift", body="Afu takes his first night shift.",
                                             tags=["shiba"], suno_title="Night Shift", suno_style="lofi, quiet, 90 BPM",
                                             suno_exclude="vocals", cues=[dict(at="00:10.000", action="压低")]))
        result = dict(pid=pid, eid=eid, plan=plan, img=img, prompts=pv)
        if deliver:
            result["delivery"] = self.wb.deliver(eid, "full", f"{eid}deliver")
        return result
