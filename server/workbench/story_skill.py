"""Freeze the installed story skill and the series materials used for a card kickoff."""
import io
import re
import subprocess
import tarfile
from pathlib import Path

from .errors import Rejected
from .storage import canonical, write_atomic

SKILL_DIR = Path.home() / ".codex/skills/milo-leo-story"


def freeze(db, store, episode_id, asset_root):
    try:
        git = ["git", "-C", str(SKILL_DIR)]
        commit = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
        archive = subprocess.check_output([*git, "archive", "--format=tar", commit, "SKILL.md",
                                           "config.json", "references", "templates/story-draft.md"])
        with tarfile.open(fileobj=io.BytesIO(archive)) as files:
            texts = {"skill/" + f.name: files.extractfile(f).read().decode("utf-8")
                     for f in files if f.isfile()}
        root = Path(asset_root).resolve(strict=True)
        paths = [Path(p) for p in ("系列设定.md", "系列台账.json", "选题池/candidates.json", "剧情机制库/机制库总览.md")]
        recent = []
        for directory in sorted((root / "episodes").iterdir(), reverse=True):
            if not re.match(r"^\d{4}-\d{2}-\d{2}-", directory.name) or "验证" in directory.name or "试稿" in directory.name:
                continue
            for name in ("故事稿.md", "剧本与创作说明.md"):
                if (directory / name).is_file():
                    recent.append(directory / name)
                    break
        if recent:
            cutoff = recent[min(4, len(recent)-1)].parent.name[:10]
            paths.extend(p.relative_to(root) for p in recent if p.parent.name[:10] >= cutoff)
        for relative in paths:
            path = (root / relative).resolve(strict=True)
            if root not in path.parents:
                raise Rejected("故事参考超出项目目录：" + str(relative))
            texts["series/" + relative.as_posix()] = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, subprocess.CalledProcessError, tarfile.TarError) as exc:
        raise Rejected("无法固定本机故事技能或 PUGGY 系列资料：" + str(exc)) from exc
    db.execute("INSERT INTO episode_story_skill VALUES(?,?,?)",
               (episode_id, commit, store.put_blob(canonical(texts))))


def prepare(workdir, entries, trailer):
    paths = []
    for entry in entries:
        if entry["role"] != "story_skill_file":
            continue
        name = Path(entry["name"])
        if name.is_absolute() or ".." in name.parts or name.parts[0] not in {"skill", "series"}:
            raise Rejected("技能输入路径无效")
        write_atomic(workdir / name, entry["text"].encode("utf-8"))
        paths.append(name.as_posix())
    materials = "\n\n".join(f"===== {e['role']} · {e['name']} =====\n{e['text']}" for e in entries if e["role"] != "story_skill_file")
    return ("按本集固定版本的 skill/SKILL.md 执行已选定题卡的 S3–S4 故事起草或返修。"
            "先用只读工具阅读技能正文、故事标准、写法和所需参考。config.json 的 asset_root 对应本目录 series/，"
            "本集采用下列冻结文件。用户已选卡开工。按要求写故事并复读，只返回稿件字段，由工作台保存并另行送审。\n"
            + "\n".join(paths) + "\n\n" + materials + "\n\n" + trailer)
