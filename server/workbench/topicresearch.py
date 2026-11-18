"""Project topic research and director-selected candidates."""
import json
import re

from jsonschema import Draft202012Validator

from .db import connect, dumps, new_id, transaction
from .errors import Conflict, Rejected, row
from .methodpack import load_pack, method_entries
from .prompts import render_settings

FIELDS = ("title", "premise", "mechanism", "motivation", "difference", "reference_id")
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["candidates"],
          "properties": {"candidates": {"type": "array", "items": {
              "type": "object", "additionalProperties": False, "required": list(FIELDS),
              "properties": {key: {"type": "string"} for key in FIELDS}}}}}
INSTRUCTIONS = ("按本项目的选题研究方法和导演要求，按本轮指定数量提出可独立选择的备选卡。写明故事前提、"
                "角色动机、推动事件的机制及相对最相似旧题的实质差异。逐一对照全部候选、已拍及已发布作品和本轮其余选题。即使标题、道具或动机不同，若主要事件推进和收尾仍重合，先换故事再交付。差异说明点名最接近的旧题编号及关键场面，不能只比较最近五条，也不能把候选称为已拍。没有旧题时如实说明。"
                "每张卡的 reference_id 填一条实际借鉴的 research_source 段号，例如 R1。"
                "有导演选定的共享知识库片段时，优先借鉴该片段。资料不足时标清缺少的依据。")


_NUMBER = r"(?:[0-9]+|[一二两三四五六七八九十百千零]+)"
_COUNT_REQUEST = re.compile(
    rf"(?:制作|创作|生成|给我|给出|提供|提出|设计|构思|推荐|想要|需要|要求|再来|补充|再写|写|做|要|来|想)"
    rf"\s*(?:出|共|总共|一共)?\s*({_NUMBER})\s*(?:个|条|张|组|种|款|篇)?"
    rf"\s*(?:新的|新|不同的|独立的|备选|候选|系列|长篇|短篇|短剧|故事|短视频)*选题"
)
_COUNT_ONLY = re.compile(rf"^\s*({_NUMBER})\s*(?:个|条|张)?\s*(?:选题)?[。！!\s]*$")


def requested_count(direction):
    match = (_COUNT_REQUEST.search(direction) or _COUNT_ONLY.fullmatch(direction)
             or re.search(rf"(?:^|[，。；：、！？,;:\n])\s*({_NUMBER})\s*(?:个|条|张)?\s*(?:新)?选题",direction)
             or re.search(rf"(?:生成|给我|再写|再来|要求|想要|需要|数量[：:为]?|写)\s*({_NUMBER})\s*(?:个|条|张)(?=[，。；！？,;!\s]|$)",direction))
    if not match:
        return 3
    value = match.group(1)
    if value.isdecimal():
        count = int(value)
    else:
        digits = dict(zip("零一二三四五六七八九两", [0,1,2,3,4,5,6,7,8,9,2]))
        total = digit = 0
        for char in value:
            if char in digits:
                digit = digits[char]
            else:
                total += (digit or 1) * {"十":10,"百":100,"千":1000}[char]
                digit = 0
        count = total + digit
    if count < 1:
        raise Rejected("选题数量须至少为 1")
    return count


def result_schema(count):
    candidates = dict(SCHEMA["properties"]["candidates"], minItems=count, maxItems=count)
    return dict(SCHEMA, properties={"candidates":candidates})


def research_inputs(entries):
    """Assign stable handles to exact slices of the frozen source texts, including on retry."""
    inputs, citations = [], {}
    for entry in entries:
        if entry["role"] != "research_source":
            inputs.append(entry)
            continue
        for line in entry["text"].splitlines():
            for start in range(0, len(line), 240):
                quote = line[start:start + 240].strip()
                if not quote:
                    continue
                ref = f"R{len(citations) + 1}"
                citations[ref] = (entry["name"], quote)
                inputs.append(dict(role="research_source", name=f"{ref} · {entry['name']}", text=quote))
    return inputs, citations


def distinct_cards(cards, history):
    """Keep one copy of each title and premise; the executor compares story meaning."""
    titles = set()
    premises = {"".join(text.split()).casefold() for text in history}
    kept = []
    for card in cards:
        title = "".join(card["title"].split()).casefold()
        premise = "".join(card["premise"].split()).casefold()
        if title not in titles and premise not in premises:
            kept.append(card)
            titles.add(title)
            premises.add(premise)
    return kept


def research_reply(body, citations, count):
    if not Draft202012Validator(SCHEMA).is_valid(body):
        raise Rejected("研究结果缺少完整的候选卡字段")
    cards = body["candidates"]
    if len(cards) != count:
        raise Rejected(f"本次要求 {count} 个选题，实际返回 {len(cards)} 个，请重新研究")
    result = []
    for index, card in enumerate(cards, 1):
        if not all(card[k].strip() for k in FIELDS):
            raise Rejected(f"第 {index} 张备选卡缺少故事内容或依据")
        ref = card["reference_id"].strip()
        if ref not in citations:
            raise Rejected(f"第 {index} 张备选卡引用了资料清单以外的段号 {ref[:40]}")
        source, quote = citations[ref]
        result.append(dict({k:v for k,v in card.items() if k != "reference_id"}, source=source, quote=quote))
    return dumps(result)


class TopicResearchMixin:
    def start_topic_research(self, project_id, direction, provider, command_id, reference=None):
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

                if db.execute("SELECT 1 FROM project_archive WHERE project_id=? AND archived",(project_id,)).fetchone():
                    raise Rejected("该项目已归档，恢复后可以研究新题")
                for t in db.execute("SELECT code,premise,mechanism,motivation,status,id,id IN (SELECT id FROM expert_filmed_topics) AS filmed FROM topic_comparisons WHERE project_id=? ORDER BY code",(project_id,)):
                    published = db.execute("SELECT platform,title,published_at FROM topic_publication WHERE topic_id=? ORDER BY published_at",(t["id"],)).fetchall()
                    if not published and not t["filmed"] and t["status"]=="已制作":continue
                    label = "已发布作品" if published else "已拍作品" if t["filmed"] else "正式候选"
                    source(label + " · " + t["code"], dumps(dict(premise=t["premise"],mechanism=t["mechanism"],motivation=t["motivation"],publication=[dict(r) for r in published])))
                if reference is not None:
                    if (not isinstance(reference, dict) or not isinstance(reference.get("path"), str)
                            or not isinstance(reference.get("sha256"), str) or len(reference["sha256"]) != 64
                            or type(reference.get("offset")) is not int or reference["offset"] < 0
                            or not isinstance(reference.get("reason", ""), str)):
                        raise Rejected("请重新选择要引用的知识库原文")
                    part = self.script_excerpt(reference["path"], reference["offset"], expected_sha256=reference["sha256"])
                    length = reference.get("spanLength", len(part["text"]))
                    if type(length) is not int or not 0 < length <= len(part["text"]):
                        raise Rejected("所选原文范围已变化，请重新选段")
                    part["text"] = part["text"][:length]
                    if not part["text"].strip() or part["offset"] != reference["offset"]:
                        raise Rejected("所选段落为空，请重新选择")
                    source(f"共享知识库 · {part['title']} · {part['path']} · {part['offset']}", part["text"])
                    entries.append(dict(role="reference_note", name="选题素材取用依据", text=dumps(reference)))
                source("导演的研究方向", direction.strip())
                thread, turn, attempt = new_id(), new_id(), new_id()
                header = dict(purpose="topic_research", projectId=project_id, threadId=thread,
                              candidateCount=requested_count(direction), methodPack=pack["id"], provider=provider, sourceCodes=catalog_codes)
                header["executorConnection"] = self.run_connection(provider)
                digest, rel = self.store.freeze(turn, header, entries)
                db.execute("INSERT INTO free_thread VALUES(?,?,?)", (thread,project_id,direction.strip()[:48]))
                db.execute("INSERT INTO topic_research VALUES(?,?)", (thread, project_id))
                db.execute("INSERT INTO free_turn(id,thread_id,text,provider,status,attempt,input_hash,manifest_relpath) "
                           "VALUES(?,?,?,?,'queued',?,?,?)", (turn,thread,direction.strip(),provider,attempt,digest,rel))
                return dict(turnId=turn, attemptId=attempt)
            payload = [project_id,direction,provider]
            if reference is not None:
                payload.append(reference)
            return self._command(db, command_id, "start_topic_research", payload, start)

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
        if type(index) is not int or index < 0 or not isinstance(code,str):
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
                chosen_code = code
                if not chosen_code:
                    reserved = set(manifest["sourceCodes"])
                    reserved.update(r[0] for r in db.execute("SELECT code FROM topic WHERE project_id=?", (project_id,)))
                    number = 1
                    while f"研{number:03d}" in reserved:
                        number += 1
                    chosen_code = f"研{number:03d}"
                if chosen_code in manifest["sourceCodes"] or db.execute(
                        "SELECT 1 FROM topic WHERE project_id=? AND code=?", (project_id,chosen_code)).fetchone():
                    raise Conflict("该编号已被选题使用，请换一个编号")
                card, tid = cards[index], new_id()
                db.execute("INSERT INTO topic VALUES(?,?,?,?,?,?,?,?,?,?)",
                           (tid,project_id,chosen_code,card["premise"],card["mechanism"],card["motivation"],"候选",None,origin,self.clock()))
                snapshot = dict(id=chosen_code, **card, researchTurn=turn_id, inputFingerprint=turn["input_hash"])
                digest = self.store.put_blob(dumps(dict(candidates=[snapshot])).encode())
                db.execute("INSERT INTO topic_source VALUES(?,?,?)", (tid,origin,digest))
                return dict(topicId=tid,code=chosen_code)
            return self._command(db,command_id,"accept_research_topic",[project_id,turn_id,index,code],accept)
