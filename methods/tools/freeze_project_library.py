"""Freeze project-specific source material from the two reviewed Git revisions."""
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess

from freeze_sections import PINS, ROOT, STORY, VIDEO

DEST = ROOT / "configs/project-libraries/puggy/v1"


def build(directory):
    files = {}
    sections = []
    products = []
    def source(repo, path):
        return subprocess.check_output(["git", "-C", Path.home()/".codex/skills"/repo, "show", PINS[repo]+":"+path])

    for repo, stem in [(STORY, "user-priority-videos"), (VIDEO, "examples"), (VIDEO, "product-placement"),
                       (STORY, "planning-contract")]:
        original = f"references/{stem}.md"
        data = source(repo, original)
        files[original] = data
        lines = data.decode().splitlines(keepends=True)
        starts = [i for i,line in enumerate(lines) if re.match(r"^#{1,3} ", line)]
        for start, end in zip(starts, [*starts[1:], len(lines)]):
            body = "".join(lines[start:end])
            if not "".join(lines[start+1:end]).strip():
                continue
            sections.append(dict(id=f"{stem}-{start+1}", group=stem, title=lines[start].lstrip("# ").strip(),
                                 path=original, repo=repo, commit=PINS[repo], start=start+1, end=end,
                                 sourceSha256=sha256(data).hexdigest(), sha256=sha256(body.encode()).hexdigest()))

    base = "assets/fourthwall-products/"
    desc = source(VIDEO, base+"商品简介.txt")
    files["商品简介.txt"] = desc
    for match in re.finditer(r"^(\d+)  (.+)\n英文商品名：(.+)\n简介：(.+)\n主图文件：(.+)$", desc.decode(), re.M):
        code, name, english, description, path = match.groups()
        data = source(VIDEO, base+path)
        files[path] = data
        products.append(dict(code=code, name=name, english=english, description=description, path=path,
                             source=base+path, repo=VIDEO, commit=PINS[VIDEO], sha256=sha256(data).hexdigest(),
                             catalogSource=base+"商品简介.txt", catalogSha256=sha256(desc).hexdigest()))
    if len(products) != 16 or len(set(p["code"] for p in products)) != len(products):
        raise ValueError("固定提交的商品介绍与主图未完整解析")
    files["index.json"] = (json.dumps(dict(id="puggy/v1", sections=sections, products=products),
                                      ensure_ascii=False, indent=2)+"\n").encode()
    for name, data in files.items():
        path = directory/name
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError("已有资料版本内容不同，请构建新版本")
        else:
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_bytes(data)
    return len(sections), len(products)


if __name__ == "__main__":
    print(build(DEST))
