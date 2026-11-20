"""Persistent H3 dispatch queue. The elected dispatcher owns the live worker threads."""
from contextlib import contextmanager
import fcntl
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import threading
import time

from . import production_steps
from .cloudtask import PollInterrupted, timing
from .videoreview import MediaCallError
from .storage import canonical, file_sha256, sha256_bytes, write_atomic
from .local_credentials import LocalCredentials
from .h3rework import WriterRevisionError
from . import filmedit, productionmusic, videosegment, videobudget, onlineworkflows

SCHEMA = """
CREATE TABLE IF NOT EXISTS account(
 name TEXT PRIMARY KEY, site TEXT, instance TEXT, capacity INTEGER,
 pool TEXT, pool_capacity INTEGER, fingerprint TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS online_workflow(kind TEXT PRIMARY KEY,workflow TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS job(
 delivery TEXT, segment TEXT, state TEXT, deps TEXT, round INTEGER DEFAULT 0,
 changes TEXT DEFAULT '[]', account TEXT, error TEXT DEFAULT '', adopted TEXT,
 PRIMARY KEY(delivery,segment));
CREATE TABLE IF NOT EXISTS adaptive(delivery TEXT,segment TEXT,enabled INTEGER,PRIMARY KEY(delivery,segment));
CREATE TABLE IF NOT EXISTS recovery(delivery TEXT,segment TEXT,round INTEGER,phase TEXT,
 PRIMARY KEY(delivery,segment,round,phase));
CREATE TABLE IF NOT EXISTS cooldown(name TEXT PRIMARY KEY, until REAL);
CREATE TABLE IF NOT EXISTS media_retry(delivery TEXT,segment TEXT,round INTEGER,until REAL,attempts INTEGER,
 PRIMARY KEY(delivery,segment,round));
CREATE TABLE IF NOT EXISTS episode_control(delivery TEXT PRIMARY KEY, paused INTEGER);
"""


def safe_id(name):
    if (
        not isinstance(name, str)
        or not name
        or name in (".", "..")
        or Path(name).name != name
        or "\\" in name
    ):
        raise ValueError("无效的交付或分段名称")
    return name


class VideoQueue:
    def __init__(self, data):
        self.data = Path(data).resolve()
        self.root = self.data / "video-production"
        self.credentials = LocalCredentials(self.data)
        self.running = {}
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.thread = None

    @contextmanager
    def db(self):
        self.root.mkdir(exist_ok=True, parents=True)
        db = sqlite3.connect(self.root / "queue.sqlite", timeout=20)
        db.row_factory = sqlite3.Row
        try:
            db.executescript(SCHEMA + productionmusic.SCHEMA + videobudget.SCHEMA)
            db.execute("BEGIN IMMEDIATE")
            onlineworkflows.initialize(db)
            yield db
            db.commit()
        finally:
            db.close()

    def account(
        self,
        name,
        site,
        instance,
        capacity,
        key="",
        pool="",
        pool_capacity=None,
    ):
        name, pool = name.strip(), pool.strip()
        if not name or site not in (
            "https://www.runninghub.cn",
            "https://www.runninghub.ai",
        ):
            raise ValueError("请填写账户别名和 RunningHub 站点")
        if (
            instance not in ("default", "plus", "ultra")
            or type(capacity) is not int
            or capacity < 1
        ):
            raise ValueError("请填写有效的档位和并发数")
        limit = pool_capacity if pool else capacity
        if type(limit) is not int or limit < 1:
            raise ValueError("共享账户的总并发必须是正整数")
        with self.db() as db:
            old = db.execute("SELECT * FROM account WHERE name=?", (name,)).fetchone()
            if key and (
                not key.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in key)
            ):
                raise ValueError("Key 格式无效")
            fp = (
                sha256_bytes(key.encode())[:16]
                if key
                else old["fingerprint"]
                if old
                else None
            )
            if not fp:
                raise ValueError("请提供本账户的 Key")
            if old and (site, instance, fp) != (
                old["site"],
                old["instance"],
                old["fingerprint"],
            ):
                if db.execute(
                    "SELECT 1 FROM soundtrack WHERE account=? AND state IN ('生成中','待核对','排队中')",
                    (name,),
                ).fetchone():
                    raise ValueError("账户还有已绑定的配乐任务")
                if db.execute(
                    "SELECT 1 FROM job WHERE account=? AND state IN ('排队中','生成中','待核对','需处理')",
                    (name,),
                ).fetchone():
                    raise ValueError("账户还有已绑定的云端任务")
            if db.execute(
                "SELECT 1 FROM account WHERE fingerprint=? AND name<>?", (fp, name)
            ).fetchone():
                raise ValueError("该 Key 已配置")
            db.execute(
                "INSERT INTO account(name,site,instance,capacity,pool,pool_capacity,fingerprint) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET site=excluded.site,instance=excluded.instance,capacity=excluded.capacity,"
                "pool=excluded.pool,pool_capacity=excluded.pool_capacity,fingerprint=excluded.fingerprint",
                (name, site, instance, capacity, pool, limit, fp),
            )
            if pool:
                db.execute(
                    "UPDATE account SET pool_capacity=? WHERE pool=?", (limit, pool)
                )
            if key:
                self.credentials.set("ADW_H3_KEY_" + fp.upper(), key)
        self.wake.set()

    def key(self, account):
        env = "ADW_H3_KEY_" + account["fingerprint"].upper()
        value = (
            self.credentials.get(env)
            or os.getenv(env)
            or os.getenv("RUNNINGHUB_API_KEY", "")
        )
        return (
            value if sha256_bytes(value.encode())[:16] == account["fingerprint"] else ""
        )

    def directory(self, delivery, segment, round):
        return self.root / safe_id(delivery) / safe_id(segment) / f"round-{int(round)}"

    def workflows(self):
        with self.db() as db:
            return onlineworkflows.list_entries(db)

    def set_workflow(self, kind, workflow):
        with self.db() as db:
            onlineworkflows.save(db, kind, workflow)
            return onlineworkflows.list_entries(db)

    def accounts(self):
        with self.db() as db:
            return self.account_profiles(db)

    def account_profiles(self, db):
        return [
            dict(
                name=a["name"],
                site=a["site"],
                instance=a["instance"],
                capacity=a["capacity"],
                pool=a["pool"],
                poolCapacity=a["pool_capacity"],
                connected=bool(self.key(a)),
            )
            for a in db.execute("SELECT * FROM account")
        ]

    def view(self, delivery):
        with self.db() as db:
            self.register_old_deliveries(db)
            budget = videobudget.limits(db, delivery)
            rows = db.execute(
                "SELECT * FROM job WHERE delivery=? ORDER BY rowid", (delivery,)
            ).fetchall()
            accounts = self.account_profiles(db)
            music = db.execute(
                "SELECT state,account,error,audio,handoff,task,attempted FROM soundtrack WHERE delivery=?",
                (delivery,),
            ).fetchone()
            pause = db.execute(
                "SELECT paused FROM episode_control WHERE delivery=?", (delivery,)
            ).fetchone()
        items = []
        for r in rows:
            item = dict(r)
            for k in ("deps", "changes"):
                item[k] = json.loads(item[k])
            folder = (
                self.data / item["adopted"]
                if item["adopted"]
                else self.directory(delivery, item["segment"], item["round"])
            )
            current = self.directory(delivery, item["segment"], item["round"])
            item["cloud"] = timing(current)
            item["action"] = ""
            if item["state"] in ("生成中", "审片中"):
                item["action"] = production_steps.read_step(current).get("label", "")
                if item["state"] == "生成中" and item["cloud"].get("task"):
                    item["action"] = "工作台正在取回视频" if item["cloud"].get("phase") == "下载" else "H3 已接单，等候视频"
                elif not item["action"]:
                    item["action"] = "工作台正在准备本轮视频" if item["state"] == "生成中" else "视频已取回，等待审片"
            ledger = folder / "bindings.json"
            record = json.loads(ledger.read_bytes()) if ledger.is_file() else {}
            item["bindings"] = record.get("bindings", [])
            item["skipped"] = record.get("skipped", [])
            report = folder / "expert/visual-review.json"
            if report.is_file():
                item["review"] = json.loads(report.read_bytes())["visualReview"]
            item["reworks"] = []
            for number, change in enumerate(item["changes"], start=1):
                source = self.directory(delivery, item["segment"], number-1)
                receipt = source / "expert/visual-review.json"
                review = json.loads(receipt.read_bytes())["visualReview"] if receipt.is_file() else {}
                item["reworks"].append(dict(number=number, change=change, review=review))
            item.pop("adopted")
            items.append(item)
        by_segment = {j["segment"]: j for j in items}
        for item in items:
            if item["state"] == "排队中":
                waiting = [by_segment.get(dep["source"], {}) for dep in item["deps"]
                           if by_segment.get(dep["source"], {}).get("state") not in ("已采用", "已出片待选剪")]
                sources = list(dict.fromkeys(j["segment"] + ("（" + j["error"] + "）" if j["error"] else "")
                                             for j in waiting if j))
                item["action"] = "等待 " + "、".join(sources) + " 采用" if sources else item["error"] or "等待派发"
        if music:
            music = dict(music, cloud=timing(self.root / delivery / "music"))
            music["audioReady"] = bool(music.get("audio") and music["state"] == "已就绪")
            music["listened"] = False
            music["action"] = "音频文件已返回（AI 未试听）" if music["audioReady"] else music["state"]
            if music["state"] == "生成中":
                if music["task"]:
                    music["action"] = "工作台正在取回配乐" if music["cloud"].get("phase")=="下载" else "配乐服务已接单"
                else:
                    music["action"] = "配乐提交结果待确认" if music["attempted"] else "工作台正在准备配乐"
        cut = filmedit.status((self.data/music["handoff"]).parent) if music and music["handoff"] else {}
        if cut.get("published") and not (self.data/music["handoff"]).with_name("final.mp4").is_file():
            cut["published"] = None
            if cut.get("state") == "已出片":
                cut["state"] = "等待恢复成片文件"
        return dict(jobs=items, cut=cut, budget=budget, accounts=accounts, paused=bool(pause and pause[0]), music=music)

    def segments(self, delivery):
        safe_id(delivery)
        with sqlite3.connect(
            (self.data / "workbench.sqlite").as_uri() + "?mode=ro", uri=True
        ) as db:
            row = db.execute(
                "SELECT relpath FROM delivery WHERE id=? AND state='deliverable'",
                (delivery,),
            ).fetchone()
        if not row:
            raise ValueError("请选择有效的图文交付")
        data = json.loads((self.data / row[0] / "episode.json").read_bytes())
        return [s["id"] for s in data["segments"]]

    def register_old_deliveries(self, db):
        missing = list(
            db.execute(
                "SELECT DISTINCT j.delivery FROM job j LEFT JOIN video_scope s USING(delivery) "
                "WHERE s.delivery IS NULL"
            )
        )
        for (delivery,) in missing:
            videobudget.register(self, db, delivery, self.segments(delivery))

        videobudget.reconcile(self, db)

    def enqueue(self, delivery, segment, dependencies):
        self.enqueue_batch(delivery, [dict(segment=segment, dependencies=dependencies)])

    def enqueue_batch(self, delivery, items, *, whole_episode=False, respect_pause=False):
        from .videoplan import material

        safe_id(delivery)
        slots, read = material(self.data, delivery)
        music = productionmusic.brief(read("episode.json")) if whole_episode else None
        if whole_episode and set(slots) != {i["segment"] for i in items}:
            raise ValueError("整集派发需要完整分段清单")
        if not items or len({i["segment"] for i in items}) != len(items):
            raise ValueError("请选择要排队的分段")
        with self.db() as db:
            if respect_pause and db.execute("SELECT 1 FROM episode_control WHERE delivery=? AND paused=1",(delivery,)).fetchone():
                raise PollInterrupted()
            self.register_old_deliveries(db)
            videobudget.register(self, db, delivery, slots)
            if music:
                other = db.execute(
                    "SELECT m.delivery FROM soundtrack m JOIN video_scope s USING(delivery) "
                    "WHERE s.episode=(SELECT episode FROM video_scope WHERE delivery=?) AND m.delivery<>?",
                    (delivery, delivery),
                ).fetchone()
                if other:
                    raise ValueError("本集已有整集制作任务与配乐")
                db.execute(
                    "INSERT OR IGNORE INTO soundtrack(delivery,brief,state) VALUES(?,?,'排队中')",
                    (delivery, json.dumps(music, ensure_ascii=False)),
                )
            for item in items:
                segment = item["segment"]
                if (
                    music
                    and db.execute(
                        "SELECT 1 FROM job WHERE delivery=? AND segment=?",
                        (delivery, segment),
                    ).fetchone()
                ):
                    continue
                deps = self.validate_deps(slots, segment, item["dependencies"])
                old = db.execute(
                    "SELECT * FROM job WHERE delivery=? AND segment=?",
                    (delivery, segment),
                ).fetchone()
                frozen = (
                    (
                        self.directory(delivery, segment, old["round"]) / "trial.json"
                    ).exists()
                    if old
                    else False
                )
                if (
                    old
                    and old["deps"] != deps
                    and (old["state"] not in ("需处理", "排队中") or frozen)
                ):
                    raise ValueError(segment + " 已派发，本轮参考已固定")
                if not old or old["state"] == "排队中" and not frozen:
                    db.execute(
                        "INSERT OR REPLACE INTO adaptive VALUES(?,?,?)",
                        (delivery, segment, int(item.get("automatic", False))),
                    )
                db.execute(
                    "INSERT INTO job(delivery,segment,state,deps) VALUES(?,?,'排队中',?) ON CONFLICT(delivery,segment) DO UPDATE SET deps=excluded.deps",
                    (delivery, segment, deps),
                )
        self.wake.set()

    @staticmethod
    def validate_deps(slots_by_segment, segment, dependencies):
        order = list(slots_by_segment)
        if segment not in order or not isinstance(dependencies, list):
            raise ValueError("分段不在这份交付中")
        slots = set()
        for dep in dependencies:
            src, slot, sec = dep["source"], dep["slot"], dep.get("seconds")
            if (
                src not in order[: order.index(segment)]
                or type(slot) is not int
                or not 1 <= slot <= 9
                or slot in slots
            ):
                raise ValueError("请从较早的分段选择来源，且每个替换图位只能绑定一次")
            if sec is not None and (
                type(sec) not in (int, float) or not math.isfinite(sec) or sec < 0
            ):
                raise ValueError("来源帧时间无效")
            if not isinstance(dep["note"], str) or not dep["note"].strip():
                raise ValueError("请说明需要继承的画面状态")
            refs = slots_by_segment[segment]
            if slot <= len(refs) and refs[slot - 1]["protected"]:
                raise ValueError("请保留固定身份图和分镜九宫格")
            dep["seconds"] = sec
            slots.add(slot)
        appended = sorted(
            slot for slot in slots if slot > len(slots_by_segment[segment])
        )
        if appended and appended != list(
            range(len(slots_by_segment[segment]) + 1, appended[-1] + 1)
        ):
            raise ValueError("请从第一个空位开始连续追加状态图")
        return json.dumps(dependencies, ensure_ascii=False, sort_keys=True)

    def adopt_trial(self, delivery, segment):
        if segment not in self.segments(delivery):
            raise ValueError("试片不属于本次交付")
        folder = self.data / "video-trials" / safe_id(delivery) / safe_id(segment)
        report = json.loads((folder / "expert/visual-review.json").read_bytes())
        receipt = json.loads((folder / "trial.json").read_bytes())
        if (
            receipt["binding"]["delivery"] != delivery
            or receipt["binding"]["segment"] != segment
            or report["visualReview"]["decision"] == "局部返工"
            or file_sha256(folder / "result.mp4") != report["evidence"]["sha256"]
            or receipt["sha256"] != report["evidence"]["sha256"]
        ):
            raise ValueError("试片缺少对应版本的通过评审")
        with self.db() as db:
            self.register_old_deliveries(db)
            videobudget.register(self, db, delivery, self.segments(delivery))
            error = videobudget.reserve(db, delivery, segment, 0)
            if error:
                raise ValueError(error)
            videobudget.mark(db,delivery,segment,0,"success")
            db.execute(
                "INSERT OR IGNORE INTO job(delivery,segment,state,deps,adopted) VALUES(?,?,'已采用','[]',?)",
                (delivery, segment, str(folder.relative_to(self.data))),
            )
        self.wake.set()

    def media_folder(self, delivery, segment, version):
        safe_id(delivery)
        safe_id(segment)
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM job WHERE delivery=? AND segment=?", (delivery, segment)
            ).fetchone()
        if not row:
            raise ValueError("找不到该视频段")
        if version == "adopted" and row["adopted"]:
            return self.data / row["adopted"]
        if version.isdecimal() and 0 <= int(version) <= row["round"]:
            return self.directory(delivery, segment, int(version))
        raise ValueError("没有这个版本")

    def reference_frame(self, delivery, segment, slot):
        with self.db() as db:
            row = db.execute(
                "SELECT round,adopted FROM job WHERE delivery=? AND segment=?",
                (delivery, segment),
            ).fetchone()
        if not row:
            raise ValueError("找不到本段")
        folder = self.media_folder(
            delivery, segment, "adopted" if row["adopted"] else str(row["round"])
        )
        ledger = json.loads((folder / "bindings.json").read_bytes())
        binding = next((b for b in ledger["bindings"] if b["slot"] == slot), None)
        if not binding:
            raise ValueError("该位置没有跨段状态图")
        source = self.media_folder(delivery, binding["source"], "adopted")
        for meta in source.glob("state-frame-*.png.json"):
            m = json.loads(meta.read_bytes())
            if (m["imageHash"], m["sourceHash"]) == (
                binding["imageHash"],
                binding["sourceHash"],
            ):
                path = source / safe_id(m["file"])
                if file_sha256(path) == m["imageHash"]:
                    return path
        raise ValueError("找不到该轮实际使用的原帧")

    def adopt_round(self, delivery, segment, round_no):
        folder = self.media_folder(delivery, segment, str(round_no))
        report = json.loads((folder / "expert/visual-review.json").read_bytes())
        if file_sha256(folder / "result.mp4") != report["evidence"]["sha256"]:
            raise ValueError("视频与评审不相符")
        with self.db() as db:
            if not db.execute(
                "UPDATE job SET state='已采用',adopted=?,error='' WHERE delivery=? AND segment=? AND state IN ('待导演决定','需处理','排队中')",
                (str(folder.relative_to(self.data)), delivery, segment),
            ).rowcount:
                raise ValueError("请在当前生成和审片结束后采用")
        self.wake.set()

    def pause(self, delivery, value):
        with self.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO episode_control VALUES(?,?)",
                (delivery, int(value)),
            )
        self.wake.set()

    def update(self, job, state, **fields):
        updates = {"state": state, **fields}
        with self.db() as db:
            db.execute(
                "UPDATE job SET "
                + ", ".join(k + "=?" for k in updates)
                + " WHERE delivery=? AND segment=?",
                (*updates.values(), job["delivery"], job["segment"]),
            )
        self.wake.set()

    def dispatch(self):
        with self.db() as db:
            self.register_old_deliveries(db)
            jobs = [dict(r) for r in db.execute("SELECT * FROM job ORDER BY rowid")]
            paused = {
                r[0]
                for r in db.execute(
                    "SELECT delivery FROM episode_control WHERE paused=1"
                )
            }
            for job in jobs:
                if (job["delivery"] not in paused and job["state"] == "需处理" and (job["error"] or "").startswith("写稿人补修后仍未通过：")
                        and not (self.directory(job["delivery"], job["segment"], job["round"]) / "trial.json").exists()):
                    job.update(state="排队中", error="等待写稿 AI 接续本轮局部返修")
                    db.execute("UPDATE job SET state=?,error=? WHERE delivery=? AND segment=?",
                               (job["state"], job["error"], job["delivery"], job["segment"]))
            stopped = {
                (j["delivery"], j["segment"])
                for j in jobs
                if j["state"] in ("已止损", "承接受阻")
            }
            for j in jobs:
                if j["state"] != "排队中":
                    continue
                blocked = [
                    d["source"]
                    for d in json.loads(j["deps"])
                    if (j["delivery"], d["source"]) in stopped
                ]
                if blocked:
                    j["state"] = "承接受阻"
                    db.execute(
                        "UPDATE job SET state=?,error=? WHERE delivery=? AND segment=?",
                        (
                            j["state"],
                            "来源段未通过审片：" + "、".join(blocked),
                            j["delivery"],
                            j["segment"],
                        ),
                    )
                    stopped.add((j["delivery"], j["segment"]))
            productionmusic.advance(self, db)
            music_jobs = [
                dict(r, segment=None, deps="[]", adopted=None)
                for r in db.execute(
                    "SELECT delivery,state,account FROM soundtrack WHERE state IN ('排队中','生成中','待核对') ORDER BY rowid"
                )
            ]
            jobs = music_jobs + jobs
            accounts = [
                dict(a)
                for a in db.execute(
                    "SELECT name,site,instance,capacity,pool,pool_capacity,fingerprint FROM account ORDER BY name"
                )
            ]
            self.running = {k: v for k, v in self.running.items() if v.is_alive()}
            active = {
                a["name"]: sum(
                    j["state"] in ("生成中", "待核对") and j["account"] == a["name"]
                    for j in jobs
                )
                for a in accounts
            }
            adopted = {
                (j["delivery"], j["segment"]): self.data / j["adopted"]
                for j in jobs
                if j["adopted"]
            }
            cooldown = {
                r["name"]
                for r in db.execute(
                    "SELECT name FROM cooldown WHERE until>?", (time.time(),)
                )
            }
            workflows = dict(db.execute("SELECT kind,workflow FROM online_workflow"))
            deferred={(r["delivery"],r["segment"],r["round"]) for r in db.execute("SELECT * FROM media_retry WHERE until>?",(time.time(),))}
            launches = []
            for job in jobs:
                ident = (job["delivery"], job["segment"])
                if ident[0] in paused or (ident[0],ident[1],job.get("round")) in deferred or ident in self.running or job["state"] not in ("排队中", "生成中", "审片中"):
                    continue
                # If a source produced video but has no usable frame, keep the target's original reference.
                deps = json.loads(job["deps"])
                if any((ident[0], d["source"]) not in adopted for d in deps):
                    continue
                account = None
                if job["state"] == "排队中":
                    free = [
                        a
                        for a in accounts
                        if self.key(a)
                        and a["name"] not in cooldown
                        and (not job["account"] or job["account"] == a["name"])
                        and active[a["name"]] < a["capacity"]
                        and (
                            not a["pool"]
                            or sum(
                                active[b["name"]]
                                for b in accounts
                                if b["pool"] == a["pool"]
                            )
                            < a["pool_capacity"]
                        )
                    ]
                    if not free:
                        continue
                    if job["segment"] is not None:
                        reason = videobudget.reserve(
                            db, ident[0], ident[1], job["round"]
                        )
                        if reason:
                            launches.append((ident,dict(job,limitReason=reason),None,[],{}))
                            continue
                    account = min(free, key=lambda a: active[a["name"]] / a["capacity"])
                    job.update(state="生成中", account=account["name"])
                    if job["segment"] is None:
                        db.execute(
                            "UPDATE soundtrack SET state='生成中',account=?,error='',brief=json_set(brief,'$.workflow',"
                            "COALESCE(json_extract(brief,'$.workflow'),?)) WHERE delivery=?",
                            (account["name"], workflows["suno"], ident[0]),
                        )
                    else:
                        db.execute(
                            "UPDATE job SET state='生成中',account=?,error='' WHERE delivery=? AND segment=?",
                            (account["name"], *ident),
                        )
                    active[account["name"]] += 1
                elif job["state"] == "生成中":
                    account = next(
                        (
                            a
                            for a in accounts
                            if a["name"] == job["account"] and self.key(a)
                        ),
                        None,
                    )
                    if not account:
                        continue
                if account:
                    account = dict(account, key=self.key(account))
                    if job["segment"] is not None:
                        job["workflow"] = workflows["h3"]
                sources = {d["source"]: adopted[(ident[0], d["source"])] for d in deps}
                launches.append((ident, job, account, deps, sources))
        for ident, job, account, deps, sources in launches:
            if job.get("limitReason"):
                t=threading.Thread(target=self.resume_final_decision,args=(job,),daemon=True)
            elif job["segment"] is None:
                t = threading.Thread(
                    target=productionmusic.generate,
                    args=(self, job, account),
                    daemon=True,
                )
            else:
                t = threading.Thread(
                    target=self.execute, args=(job, account, deps, sources), daemon=True
                )
            self.running[ident] = t
            t.start()

    def future_refs(self, delivery, segment):
        from .videoplan import material

        with self.db() as db:
            targets = {
                r[0]: json.loads(r[1])
                for r in db.execute(
                    "SELECT j.segment,j.deps FROM job j JOIN adaptive a USING(delivery,segment) WHERE j.delivery=? AND a.enabled=1 AND j.state='排队中' AND j.account IS NULL",
                    (delivery,),
                )
            }
        if not targets:
            return {}
        slots, _ = material(self.data, delivery)
        later = list(slots)[list(slots).index(segment) + 1 :]
        return {s: dict(references=slots[s], planned=targets[s]) for s in later if s in targets}

    def defer_media(self,job,state,error):
        with self.db() as db:
            key=(job["delivery"],job["segment"],job["round"])
            prior=db.execute("SELECT attempts FROM media_retry WHERE delivery=? AND segment=? AND round=?",key).fetchone()
            tries=prior[0]+1 if prior else 1
            db.execute("INSERT OR REPLACE INTO media_retry VALUES(?,?,?,?,?)",(*key,time.time()+min(300,10*2**min(tries,5)),tries))
            db.execute("UPDATE job SET state=?,error=? WHERE delivery=? AND segment=? AND round=?",
                       (state,"等待自动重试 · "+str(error)[:300],*key))

    def resume_final_decision(self,job):
        try:
            self.decide_final_version(job,job["limitReason"])
        except (MediaCallError,TimeoutError,ConnectionError) as exc:
            self.defer_media(job,"排队中",exc)
        except (ValueError,OSError) as exc:
            self.update(job,"需处理",error=str(exc)[:350])

    def decide_final_version(self, job, reason):
        from .videoreview import media_verdict
        delivery, segment = job["delivery"], job["segment"]
        versions={}
        for number in range(job["round"]+1):
            folder=self.directory(delivery,segment,number)
            path=folder/"expert/visual-review.json"
            if not path.is_file() or not (folder/"result.mp4").is_file():
                continue
            report=json.loads(path.read_bytes())
            if report["evidence"]["sha256"]==file_sha256(folder/"result.mp4"):
                versions[number]=dict(duration=report["media"]["format"]["duration"],review=report["visualReview"],
                                      sha256=report["evidence"]["sha256"],script=report["evidence"]["brief"].get("script",[]))
        if not versions:
            self.update(job,"已止损",adopted=None,error=reason+"，尚无已审原片")
            return
        props=dict(round={"enum":list(versions)},action={"enum":["采用可用区间","保留全段交精剪","素材无法交片"]},reason={"type":"string","minLength":1})
        schema=dict(type="object",properties=props,required=list(props),additionalProperties=False)
        verdict=media_verdict(self.data,delivery,"expert","limit_decision",self.directory(delivery,segment,job["round"])/"expert",
            "返工名额已用尽。对照每版实际审片、时段与剧本选定本段最终去向。有对白、画面可用于交代剧情时可选保留全段交精剪，并交代画面缺陷。无可用画面时明确素材无法交片。"+json.dumps(versions,ensure_ascii=False),schema)["result"]
        folder=self.directory(delivery,segment,verdict["round"])
        path=folder/"expert/visual-review.json"
        report=json.loads(path.read_bytes())
        chosen=dict(report["visualReview"])
        if verdict["action"]=="保留全段交精剪":
            chosen.update(usableStart=0,usableEnd=float(versions[verdict["round"]]["duration"]))
        usable=verdict["action"]!="素材无法交片" and chosen["usableEnd"]>chosen["usableStart"]
        if usable:
            chosen.update(decision="采用并附剪点",cutAdvice=verdict["reason"])
            write_atomic(path,canonical(dict(report,visualReview=chosen,limitDecision=verdict)))
        with self.db() as db:
            db.execute("UPDATE job SET state=?,adopted=?,error=? WHERE delivery=? AND segment=? AND round=?",
                       ("已采用" if usable else "已止损",str(folder.relative_to(self.data)) if usable else None,
                        reason+" · "+verdict["reason"],delivery,segment,job["round"]))

    def finish(self, job, out, review, future):
        delivery, segment = job["delivery"], job["segment"]
        with self.db() as db:
            for suggestion in sorted(review.get("carryForward", []), key=lambda d: d["slot"]):
                target = suggestion["target"]
                if (
                    target not in future
                    or not review["usableStart"]
                    <= suggestion["seconds"]
                    <= review["usableEnd"]
                ):
                    continue
                row = db.execute(
                    "SELECT deps FROM job JOIN adaptive USING(delivery,segment) WHERE delivery=? AND segment=? AND enabled=1 AND state='排队中' AND account IS NULL",
                    (delivery, target),
                ).fetchone()
                if not row:
                    continue
                deps = json.loads(row["deps"])
                if any(d["slot"] == suggestion["slot"] for d in deps):
                    continue
                dep = dict(
                    source=segment,
                    slot=suggestion["slot"],
                    note=suggestion["note"],
                    seconds=suggestion["seconds"],
                )
                # Validate the new position against all existing bindings; their sources may be different.
                order = {d["source"]: [] for d in deps}
                order.update({segment: [], target: future[target]["references"]})
                try:
                    revised = self.validate_deps(order, target, [*deps, dep])
                except ValueError:
                    continue
                db.execute(
                    "UPDATE job SET deps=? WHERE delivery=? AND segment=?",
                    (revised, delivery, target),
                )
            db.execute(
                "UPDATE job SET state='已采用',adopted=?,error='' WHERE delivery=? AND segment=?",
                (str(out.relative_to(self.data)), delivery, segment),
            )
        self.wake.set()

    def check_dispatch(self, delivery):
        # A point read avoids taking the dispatcher's write lock while a model is starting.
        path = self.root / "queue.sqlite"
        if path.is_file():
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
                if db.execute("SELECT 1 FROM episode_control WHERE delivery=? AND paused=1", (delivery,)).fetchone():
                    raise PollInterrupted()

    def execute(self, job, account, deps, sources):
        folder = self.directory(job["delivery"], job["segment"], job["round"])
        def report(label):
            self.check_dispatch(job["delivery"])
            production_steps.save_step(folder, label)
        with production_steps.observe(report):
            self._execute(job, account, deps, sources)

    def _execute(self, job, account, deps, sources):
        delivery, segment = job["delivery"], job["segment"]
        out = self.directory(delivery, segment, job["round"])
        try:
            prepared, bindings = videosegment.prepare(
                self.data,
                delivery,
                segment,
                deps,
                sources,
                out,
                json.loads(job["changes"]),
            )
            if job["state"] == "生成中":
                videosegment.generate(
                    self.data,
                    delivery,
                    segment,
                    out,
                    account,
                    workflow=job["workflow"],
                    prepared=prepared,
                    stop=self.stop,
                )
                with self.db() as db:
                    videobudget.mark(db,delivery,segment,job["round"],"success")
                    production_steps.save_step(out, "视频已取回，等待独立评审")
                    db.execute("UPDATE job SET state='审片中' WHERE delivery=? AND segment=?", (delivery,segment))
            images, continuity = [], []
            for binding in bindings:
                folder = sources[binding["source"]]
                sheets = sorted(
                    (folder / "expert/work").glob("frames-*.jpg"),
                    key=lambda p: int(p.stem.split("-")[-1]),
                )
                if not sheets:
                    raise ValueError("缺少来源段已审画面")
                images.extend(sheets)
                continuity.append(dict(**binding, sheets=len(sheets)))
            future = self.future_refs(delivery, segment)
            review = videosegment.review(
                self.data, delivery, segment, out, continuity, images, future
            )["visualReview"]
            if review["decision"] == "局部返工":
                changes = json.loads(job["changes"])
                next_round = job["round"] + 1
                with self.db() as db:
                    reason = (
                        "专家未给出可执行的局部修改"
                        if not review["localChange"].strip()
                        else videobudget.reserve(db, delivery, segment, next_round)
                    )
                    if not reason:
                        changes.append(review["localChange"])
                        db.execute(
                            "UPDATE job SET state='排队中',round=?,changes=?,account=NULL,error='' "
                            "WHERE delivery=? AND segment=?",
                            (
                                next_round,
                                json.dumps(changes, ensure_ascii=False),
                                delivery,
                                segment,
                            ),
                        )
                if reason:
                    self.decide_final_version(job,reason)
            else:
                self.finish(job, out, review, future)
        except WriterRevisionError as exc:
            self.defer_media(job, "排队中", exc)
        except MediaCallError as exc:
            # A downloaded result can be reviewed again without another cloud submission.
            receipt=out/"trial.json"
            downloaded=receipt.is_file() and json.loads(receipt.read_bytes()).get("status")=="downloaded"
            self.defer_media(job,"审片中" if downloaded else "排队中",exc)
        except PollInterrupted:
            return
        except videosegment.CloudCapacity:
            with self.db() as db:
                videobudget.mark(db,delivery,segment,job["round"],"failed")
                for a in db.execute(
                    "SELECT name FROM account WHERE name=? OR (pool<>'' AND pool=?)",
                    (account["name"], account["pool"]),
                ).fetchall():
                    db.execute(
                        "INSERT OR REPLACE INTO cooldown VALUES(?,?)",
                        (a["name"], time.time() + 30),
                    )
                db.execute(
                    "UPDATE job SET state='排队中',error='等待云端名额' WHERE delivery=? AND segment=?",
                    (delivery, segment),
                )
        except Exception as exc:
            receipt = out / "trial.json"
            status = (
                json.loads(receipt.read_bytes()).get("status")
                if receipt.exists()
                else None
            )
            state = (
                "待核对" if status in ("submitting", "submitted", "query_error") else "需处理"
            )
            msg = str(exc)
            if account and account.get("key"):
                msg = msg.replace(account["key"], "[hidden]")
            # Resume a confirmed original task or review its existing file once.
            # Ambiguous submissions never get another submission.
            resume = (
                "审片中"
                if status == "downloaded"
                else "生成中"
                if status in ("submitted", "query_error")
                and json.loads(receipt.read_bytes()).get("task")
                else None
            )
            with self.db() as db:
                known_failure = status in ("rejected", "cloud_failed") or status is None or status in ("capacity","retry_ready")
                if known_failure:
                    videobudget.mark(db,delivery,segment,job["round"],"failed")
                if status == "cloud_failed" and not (out/"failed-submission.json").exists():
                    # A confirmed failure can retry this creative round once. Keep the cloud receipt.
                    videosegment.reset_failed(out)
                    state = "排队中"
                if (
                    resume
                    and db.execute(
                        "INSERT OR IGNORE INTO recovery VALUES(?,?,?,?)",
                        (delivery, segment, job["round"], resume),
                    ).rowcount
                ):
                    state = resume
                db.execute(
                    "UPDATE job SET state=?,error=? WHERE delivery=? AND segment=?",
                    (state, msg[:600], delivery, segment),
                )
        finally:
            self.wake.set()

    def retry(self, delivery, segment):
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM job WHERE delivery=? AND segment=?", (delivery, segment)
            ).fetchone()
            if not row or row["state"] not in ("待核对", "需处理"):
                raise ValueError("该段没有可恢复的任务")
            path = self.directory(delivery, segment, row["round"]) / "trial.json"
            r = json.loads(path.read_bytes()) if path.exists() else {}
            if r.get("status") == "submitting" and not r.get("task"):
                raise ValueError("云端可能已接单，请先核对原任务号")
            if r.get("status") in ("rejected", "cloud_failed"):
                # An explicit recovery after correcting the provider or workflow stays in this same round.
                previous = path.parent / "failed-submission.json"
                if previous.is_file():
                    stamp = sha256_bytes(path.read_bytes())[:16]
                    archive = path.parent / ("failed-submission-" + stamp + ".json")
                    if not archive.exists():
                        from .storage import write_atomic
                        write_atomic(archive, path.read_bytes())
                videosegment.reset_failed(path.parent)
                r = json.loads(path.read_bytes())
            state = (
                "审片中"
                if r.get("status") == "downloaded"
                else "生成中"
                if r.get("task")
                else "排队中"
            )
            db.execute(
                "UPDATE job SET state=?,error='' WHERE delivery=? AND segment=?",
                (state, delivery, segment),
            )
        self.wake.set()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()

        def loop():
            self.root.mkdir(exist_ok=True, parents=True)
            with (self.root / "dispatch.lock").open("a") as lock:
                while not self.stop.is_set():
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        self.stop.wait(1)
                        continue
                    try:
                        self.dispatch()
                    except Exception:
                        logging.exception("视频任务派发失败")
                    self.wake.wait(2)
                    self.wake.clear()
                for t in self.running.values():
                    t.join()

        self.thread = threading.Thread(target=loop, daemon=True, name="h3-dispatch")
        self.thread.start()
