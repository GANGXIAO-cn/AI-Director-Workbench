"""Read-only import of historical production records, with explicit check coverage."""
import json
from pathlib import Path
import re

from . import publishing, archivefiles, archivechecks, promptreceipt, referencereports
from .db import connect, dumps, new_id, transaction
from .errors import NotFound, Rejected, row
from .sourcefiles import production
from .specs import check_generation_aspect
from .storage import canonical, sha256_bytes

MANIFEST = "episode.json"
PAGE = "正式交付/00_图文对照.html"
HANDOFF = "正式交付/03_精剪交接.md"
PACK = "正式交付/01_发布包.md"
FILES = (MANIFEST, PAGE, HANDOFF, PACK)


def outcome(state, rule, message):
    return dict(state=state, rule=rule, message=message)


def reject(rule, message):
    return outcome("failed", rule, message)


def check_manifest(data):
    if type(data) is not dict or type(data.get("schema_version")) is not int or data["schema_version"] != 2:
        return reject("schema_version", "须声明 schema_version=2")
    if {"asset_snapshot", "video_prompt_standard", "video_prompt_rules"}.intersection(data):
        return reject("legacy_snapshot", "清单不使用每集标准快照字段")
    if data.get("h3_rules") != "v1":
        return reject("h3_rules", "须声明 h3_rules=v1")
    ratio = data.get("aspect_ratio")
    try:
        check_generation_aspect(data.get("generation_settings"), ratio)
        for segment in data.get("segments", []):
            check_generation_aspect(segment.get("generation_settings"), ratio)
    except (ValueError, TypeError, AttributeError) as exc:
        return reject("aspect_ratio", str(exc))
    title = data.get("english_title")
    if not isinstance(title, str) or not re.search("[A-Za-z]", title) or re.search("[\u3400-\u9fff]", title):
        return reject("english_title", "缺少本集英文视频标题，或混入中文标题")
    try:
        total = archivechecks.number(data.get("total_duration_s"))
    except (ValueError, OverflowError) as exc:
        return reject("duration_value", str(exc))
    policy = data.get("duration_policy", "over_60")
    if total <= 0 or policy not in ("story_driven", "over_60"):
        return reject("duration_policy", "非法片长或duration_policy")
    if policy == "over_60" and total <= 60:
        return reject("over_60", "over_60总时长必须严格大于60秒")
    return outcome("incomplete", "production_assets", "基础字段通过；素材、角色、段落、审核和原图仍须完整核验")


def check_files(files, cfg, material=None):
    try:
        manifest = check_manifest(json.loads(files[MANIFEST]))
    except (KeyError, ValueError, UnicodeError):
        manifest = reject("manifest_read", "集清单缺失或内容无法解析")
    if manifest["state"] == "incomplete" and material:
        for key in ("cast","depictions","assets","inputs","prompts","h3Sound","rhythm","appearance","reviews","promptReview","imageInventory","aspect","shotReferences"):
            check = material.get(key,{})
            if check.get("state") == "failed":
                manifest = reject(key,check["message"])
                break
    try:
        handoff = files.get(HANDOFF, b"").decode().strip()
        if not handoff:
            page = reject("finecut_handoff", "完整交付缺少有效精剪交接说明：" + HANDOFF)
        elif manifest["state"] == "failed":
            page = dict(manifest)
        elif not files.get(PAGE):
            page = reject("delivery_page", "缺少图文交付页")
        elif material and material.get("pageContent",{}).get("state") == "failed":
            page = reject("pageContent",material["pageContent"]["message"])
        else:
            page = outcome("incomplete", "delivery_contents", "须核对成套交付引用及页面内容")
    except UnicodeError:
        page = reject("finecut_handoff", "精剪交接无法按 UTF-8 解码")
    pack = reject("missing_pack", "规定位置没有发布包")
    if PACK in files and cfg.get("copy_format") != "instrumental_v1":
        pack = outcome("incomplete", "publish_format", "该项目未启用历史配乐发布包格式")
    elif PACK in files:
        try:
            publishing.check_copy_pack(files[PACK].decode(), cfg)
            pack = outcome("passed", "publish_format", "发布包格式通过")
        except (Rejected, UnicodeError) as exc:
            pack = reject("publish_format", str(exc))
    return dict(manifest=manifest, delivery=page, publish=pack)


class HistoryMixin:
    def _history_root(self, project_id):
        with connect(self.db_path) as db:
            prod = production(db, project_id)
        return Path(prod["assetRoot"]).resolve() / "episodes" if prod.get("assetRoot") else None

    def historical_episodes(self, project_id):
        root = self._history_root(project_id)
        available = {p.name for p in root.iterdir() if not p.is_symlink() and p.is_dir() and (p/MANIFEST).is_file()} if root and root.is_dir() else set()
        revisions = {}
        with connect(self.db_path) as db:
            for item in db.execute("SELECT id,folder,created_at FROM historical_episode WHERE project_id=? "
                                   "ORDER BY created_at DESC,rowid DESC", (project_id,)):
                revisions.setdefault(item["folder"],[]).append(dict(id=item["id"],createdAt=item["created_at"]))
        return [dict(folder=name, available=name in available, versions=revisions.get(name,[]),
                     receipt=revisions[name][0]["id"] if name in revisions else None)
                for name in sorted(available | revisions.keys())]

    def import_historical_episode(self, project_id, folder, command_id=None, include_image_inventory=False):
        if type(include_image_inventory) is not bool:
            raise Rejected("是否盘点图片须为布尔值")
        command_args = [project_id,folder] + ([True] if include_image_inventory else [])
        if command_id:
            with connect(self.db_path) as db:
                cached = self._existing_command(db,command_id,"import_historical_episode",command_args)
                if cached:
                    return cached
        root = self._history_root(project_id)
        if not root or not isinstance(folder, str) or Path(folder).name != folder or folder in ("", ".", ".."):
            raise Rejected("请选择本项目已登记的历史集目录")
        if (root/folder).is_symlink():
            raise Rejected("历史集目录不能是符号链接")
        directory = (root/folder).resolve()
        if directory.parent != root or not (directory/MANIFEST).is_file():
            raise Rejected("历史集目录不存在或超出项目")
        files = {}
        for rel in FILES:
            path = (directory / rel).resolve()
            if directory not in path.parents:
                raise Rejected("历史材料路径超出本集范围")
            if path.is_file():
                files[rel] = path.read_bytes()
        try:
            roles = self.project_role_assets(project_id)["roles"]
            fixed_error = None
        except Rejected as exc:
            roles, fixed_error = [], str(exc)
        with connect(self.db_path) as db:
            prod = production(db, project_id)
        hashes, archive = archivefiles.capture(directory, files, self.store, roles, root.parent, prod,
                                               include_image_inventory=include_image_inventory)
        if fixed_error:
            archive["fixedError"] = fixed_error
        cfg = self.publish_settings(project_id)
        material_checks = self._archive_checks(hashes, archive)
        checks = check_files(files, cfg, material_checks)
        payload = dict(project=project_id, folder=folder, files=hashes, settings=cfg, checks=checks,
                       archive=archive, materialChecks=material_checks)
        digest = sha256_bytes(canonical(payload))
        with transaction(self.db_path) as db:
            def run():
                old = db.execute("SELECT id FROM historical_episode WHERE fingerprint=?", (digest,)).fetchone()
                if old:
                    return dict(receipt=old[0], **payload)
                receipt = new_id()
                db.execute("INSERT INTO historical_episode VALUES(?,?,?,?,?,?,?,?)",
                           (receipt, project_id, folder, digest, dumps(hashes), dumps(cfg), dumps(checks), self.clock()))
                db.execute("INSERT INTO historical_archive VALUES(?,?,?)",(receipt,dumps(archive),dumps(material_checks)))
                return dict(receipt=receipt, **payload)
            return self._command(db, command_id, "import_historical_episode", command_args, run)

    def _archive_checks(self, hashes, metadata):
        archive = archivefiles.ArchiveFiles(hashes, metadata, self.store)
        try:
            manifest = json.loads(archive.read(MANIFEST))
        except (ValueError, KeyError, OSError) as exc:
            return {"manifest":reject("manifest_read",str(exc))}
        return archivechecks.assess(archive, manifest)

    def _historical_files(self, project_id, receipt):
        with connect(self.db_path) as db:
            record = row(db, "SELECT e.files,a.metadata FROM historical_episode e JOIN historical_archive a "
                         "ON a.receipt_id=e.id WHERE e.project_id=? AND e.id=?", project_id, receipt,
                         missing="此历史版本没有保存送审材料")
        return archivefiles.ArchiveFiles(json.loads(record["files"]), json.loads(record["metadata"]), self.store)

    def historical_report_file(self, project_id, receipt, index, name):
        archive = self._historical_files(project_id,receipt)
        reports = archive.metadata.get("referenceReports",[])
        if index<0 or index>=len(reports):
            raise NotFound("该版本没有这份逐镜报告")
        report = reports[index]
        digest = report["resources"].get(name)
        if report.get("error") or not (digest or name in {"preview","report.zip"}):
            raise NotFound("文件未通过该回执的指纹核对")
        try:
            if name == "preview":
                return referencereports.preview(report, self.store)
            if name == "report.zip":
                return referencereports.packet(report, self.store)
            return self.store.read_blob(digest)
        except (FileNotFoundError,ValueError) as exc:
            raise Rejected("逐镜报告副本缺失或指纹不符") from exc

    def historical_contact_sheet(self, project_id, receipt):
        from . import contactsheet
        archive = self._historical_files(project_id, receipt)
        rules = archive.metadata.get("contactRules")
        if rules is None:
            with connect(self.db_path) as db:
                config = production(db, project_id)
            rules = dict(roles=config.get("roleAssets",{}).get("roles",[]),outfits=config.get("contactOutfits",{}))
        try:
            result = contactsheet.build(json.loads(archive.read(MANIFEST)), archive, rules)
            if "contactRules" not in archive.metadata:
                result["rulesNote"] = "旧回执按当前项目的角色与服装规则排图，原图取自该回执。"
            return result
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise Rejected(str(exc)) from exc

    def export_historical_contact_sheet(self, project_id, receipt, directory):
        from . import contactsheet
        result = self.historical_contact_sheet(project_id,receipt)
        try:
            return contactsheet.export(result,self.store,directory)
        except (ValueError,OSError) as exc:
            raise Rejected(str(exc)) from exc

    def historical_review_inputs(self, project_id, receipt):
        archive = self._historical_files(project_id, receipt)
        try:
            data = json.loads(archive.read(MANIFEST))
            return dict(record=promptreceipt.pending_record(data, archive.read, archive.identity_bytes))
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise Rejected(str(exc)) from exc

    def historical_receipt(self, project_id, receipt):
        with connect(self.db_path) as db:
            rec = row(db, "SELECT * FROM historical_episode WHERE project_id=? AND id=?", project_id, receipt)
            snapshot = db.execute("SELECT metadata,checks FROM historical_archive WHERE receipt_id=?", (receipt,)).fetchone()
        files = json.loads(rec["files"])
        copied = {}
        damaged = {}
        for rel in FILES:
            if rel in files:
                try:
                    copied[rel] = self.store.read_blob(files[rel])
                except (OSError, ValueError) as exc:
                    damaged[files[rel]] = str(exc)
        result = dict(receipt=receipt, folder=rec["folder"], files=files, checks=json.loads(rec["checks"]),
                      rechecked=check_files(copied,json.loads(rec["settings"])), fingerprint=rec["fingerprint"])
        if snapshot:
            metadata, checks = map(json.loads,snapshot)
            digests = set(files.values())
            digests.update(r["sha256"] for r in metadata["fixed"].values())
            digests.update(r["sha256"] for r in metadata["standards"].values())
            digests.update(metadata.get("sceneRefs",{}).values())
            digests.update(referencereports.digests(metadata))
            issues = [dict(sha256=digest,problem=reason) for digest,reason in damaged.items()]
            for digest in sorted(digests - {files[rel] for rel in FILES if rel in files}):
                try:
                    self.store.read_blob(digest)
                except (OSError, ValueError) as exc:
                    issues.append(dict(sha256=digest, problem=str(exc)))
            result["integrity"] = dict(checked=len(digests), issues=issues, ok=not issues)
            rechecked = self._archive_checks(files,metadata) if not issues else {
                key:outcome("incomplete","integrity","副本校验失败，无法重现此项检查") for key in checks}
            result.update(archive=metadata,materialChecks=checks,materialRechecked=rechecked,
                          rechecked=check_files(copied,json.loads(rec["settings"]),rechecked))
        elif damaged:
            result["integrity"] = dict(checked=len(copied)+len(damaged),ok=False,
                                       issues=[dict(sha256=k,problem=v) for k,v in damaged.items()])
        return result
