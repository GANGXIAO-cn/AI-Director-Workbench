"""Project topic research and director-selected candidates."""
import json

from jsonschema import Draft202012Validator

from .db import connect, dumps, new_id, transaction
from .errors import Conflict, Rejected, row
from .methodpack import load_pack, method_entries
from .prompts import render_settings
from .sourcefiles import production, read_named

FIELDS = ("title", "premise", "mechanism", "motivation", "difference", "source", "quote")
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["candidates"],
          "properties": {"candidates": {"type": "array", "items": {
              "type": "object", "additionalProperties": False, "required": list(FIELDS),
              "properties": {key: {"type": "string"} for key in FIELDS}}}}}
INSTRUCTIONS = ("按本项目的选题研究方法和导演要求，提出一到三张可独立选择的备选卡。写明故事前提、"
                "角色动机、推动事件的机制及相对近期旧题的实质差异。没有旧题时如实说明。"
                "每张卡的 source 填一条 research_source 的完整名称，quote 摘录该条原文。"
                "使用相关的真实记录。资料不足时可从导演给出的研究方向提出假设，标清缺少的依据。")


def research_reply(body, entries):
    if not Draft202012Validator(SCHEMA).is_valid(body):
        raise Rejected("研究结果缺少完整的候选卡字段")
    cards = body["candidates"]
    sources = {e["name"]: e["text"] for e in entries if e["role"] == "research_source"}
    if not 1 <= len(cards) <= 3:
        raise Rejected("研究结果须包含一到三张备选卡")
    for card in cards:
        if not all(card[k].strip() for k in FIELDS):
            raise Rejected("备选卡缺少故事内容或比较依据")
        if card["source"] not in sources or card["quote"] not in sources[card["source"]]:
            raise Rejected("备选卡引用的资料或摘录与本次研究输入不符")
    return dumps(cards)


class TopicResearchMixin:
    def start_topic_research(self, project_id, direction, provider, command_id):
        if not isinstance(direction, str) or not direction.strip() or not isinstance(provider, str) or provider not in self.executors:
            raise Rejected("请填写研究方向并选择执行器")
        with transaction(self.db_path) as db:
            def start():
                project = row(db, "SELECT name,summary,config_id,method_pack FROM project WHERE id=?", project_id)
                pack = load_pack(project["method_pack"])
                entries = method_entries(pack, "topic", project["config_id"])
                catalog_codes = []
                _, settings = self._settings(db, project_id)
                entries.append(dict(role="project_settings", name=project["name"],
                                    text=project["summary"] + "\n" + render_settings(settings)))

                def source(name, text):
                    entries.append(dict(role="research_source", name=name, text=text))

                prod = production(db, project_id)
                for key, field, columns in (
                    ("topicCatalog", "candidates", ("id","title","cn","mechanisms","payoff","status")),
                    ("topicLedger", "episodes", ("id","title","status","publication_status","recency_basis","core_desire",
                                               "comedy_mechanism","ending","remaining_consequence","evidence_scope")),
                ):
                    found = read_named(prod, key)
                    if found:
                        path, data = found
                        document = json.loads(data)
                        if document.get("ordering_note"):
                            entries.append(dict(role="ledger_order", name=path, text=document["ordering_note"]))
                        for item in document[field]:
                            if key == "topicCatalog":
                                catalog_codes.append(item["id"])
                            source(f"{path}#{item['id']}", dumps({k:item[k] for k in columns if k in item}))
                for t in db.execute("SELECT code,premise,mechanism,motivation,status FROM topic WHERE project_id=? "
                                    "ORDER BY created_at DESC,rowid DESC", (project_id,)):
                    source("已入池 · " + t["code"], dumps(dict(t)))
                for item in db.execute("SELECT l.id,l.content,l.evidence,l.source_ref,e.display_code FROM ledger l "
                                       "JOIN episode e ON e.id=l.episode_id WHERE l.project_id=? ORDER BY l.created_at DESC,l.rowid DESC",
                                       (project_id,)):
                    source(f"台账 · {item['display_code']} · {item['id']}", "\n".join([item["content"],item["evidence"],item["source_ref"]]))
                for item in db.execute("SELECT id,title,body FROM knowledge WHERE project_id=? AND status='readable' "
                                       "ORDER BY created_at DESC,rowid DESC", (project_id,)):
                    source(f"知识库 · {item['title']} · {item['id']}", item["body"])
                source("导演的研究方向", direction.strip())
                thread, turn, attempt = new_id(), new_id(), new_id()
                header = dict(purpose="topic_research", projectId=project_id, threadId=thread,
                              methodPack=pack["id"], provider=provider, sourceCodes=catalog_codes)
                digest, rel = self.store.freeze(turn, header, entries)
                db.execute("INSERT INTO free_thread VALUES(?,?,?)", (thread,project_id,direction.strip()[:48]))
                db.execute("INSERT INTO topic_research VALUES(?,?)", (thread, project_id))
                db.execute("INSERT INTO free_turn(id,thread_id,text,provider,status,attempt,input_hash,manifest_relpath) "
                           "VALUES(?,?,?,?,'queued',?,?,?)", (turn,thread,direction.strip(),provider,attempt,digest,rel))
                return dict(turnId=turn, attemptId=attempt)
            return self._command(db, command_id, "start_topic_research", [project_id,direction,provider], start)

    def topic_researches(self, project_id):
        with connect(self.db_path) as db:
            runs = [dict(r) for r in db.execute("SELECT t.id,t.text,t.provider,t.status,t.reason,t.attempt,t.reply "
                    "FROM topic_research r JOIN free_turn t ON t.thread_id=r.thread_id "
                    "WHERE r.project_id=? ORDER BY t.rowid DESC", (project_id,))]
            saved = {r["origin"]:r["code"] for r in db.execute(
                "SELECT origin,code FROM topic WHERE project_id=? AND origin LIKE 'research:%'", (project_id,))}
            for run in runs:
                cards = json.loads(run.pop("reply") or "[]")
                run["candidates"] = [dict(c, savedCode=saved.get(f"research:{run['id']}:{i}")) for i,c in enumerate(cards)]
            return dict(runs=runs, providers=list(self.executors))

    def accept_research_topic(self, project_id, turn_id, index, code, command_id):
        if type(index) is not int or index < 0 or not isinstance(code,str) or not code.strip():
            raise Rejected("请选择一个备选并填写选题编号")
        code = code.strip()
        origin = f"research:{turn_id}:{index}"
        with transaction(self.db_path) as db:
            def accept():
                turn = row(db, "SELECT t.* FROM free_turn t JOIN topic_research r ON r.thread_id=t.thread_id "
                           "WHERE r.project_id=? AND t.id=?", project_id, turn_id)
                cards = json.loads(turn["reply"] or "[]")
                if turn["status"] != "succeeded" or index >= len(cards):
                    raise Rejected("请选择已完成研究中的备选卡")
                prior = db.execute("SELECT id,code FROM topic WHERE project_id=? AND origin=?", (project_id,origin)).fetchone()
                if prior:
                    return dict(topicId=prior["id"],code=prior["code"])
                manifest, digest = self.store.load_manifest(turn["manifest_relpath"])
                if digest != turn["input_hash"]:
                    raise Rejected("研究输入指纹不符")
                if code in manifest["sourceCodes"] or db.execute(
                        "SELECT 1 FROM topic WHERE project_id=? AND code=?", (project_id,code)).fetchone():
                    raise Conflict("该编号已被选题使用，请换一个编号")
                card, tid = cards[index], new_id()
                db.execute("INSERT INTO topic VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (tid,project_id,code,card["premise"],card["mechanism"],card["motivation"],"候选",None,origin,self.clock()))
                snapshot = dict(id=code, **card, researchTurn=turn_id, inputFingerprint=turn["input_hash"])
                digest = self.store.put_blob(dumps(dict(candidates=[snapshot])).encode())
                db.execute("INSERT INTO topic_source VALUES(?,?,?)", (tid,origin,digest))
                return dict(topicId=tid,code=code)
            return self._command(db,command_id,"accept_research_topic",[project_id,turn_id,index,code],accept)
