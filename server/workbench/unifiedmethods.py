"""One localised method assembly; installed personal Codex skills are never loaded.

Original benchmark files stay untouched. Their digests, the adaptation and selected
references are recorded in every model input manifest. No published v1 yet.
"""
import hashlib
from pathlib import Path

from .workflow import METHOD_REF

ROOT = Path(__file__).resolve().parents[2] / "resources" / "skills"
SOURCE = ROOT / "yoko-1fa924b4abd6"
LOCAL = ROOT / "unified-film"
SOURCES = {
    "topic": ("film-synopsis/references/source.md",),
    "group": ("film-treatment/references/source.md",),
    "story": ("film-synopsis/references/source.md",),
    "characters": ("film-characters/references/source.md",),
    "world": ("film-worldbuilding/references/source.md",),
    "treatment": ("film-treatment/references/source.md",),
    "script": ("film-screenplay/references/source.md", "ai-film-router/references/screenplay-intake.md"),
    "stress": (),   # 2026-11-11: review uses the local method only; benchmark provenance stays in SOURCES.md
    "asset_design": ("film-lira-assets/references/source.md", "film-lira-assets/references/asset-strategy.md", "film-lira-assets/references/asset-change-control.md"),
    "acting": ("film-acting/references/source.md",),
    "plan": ("film-cinedance/references/shot-planning.md", "film-cinedance/references/director-modes.md", "film-cinedance/references/reference-materials.md"),
    "prompts": ("film-cinedance/references/h3-prompt-format.md", "film-cinedance/references/continuity-compiler.md", "film-cinedance/references/prompt-output-contract.md"),
    "story_review": (),
    "plan_review": (),
    "cold_read": ("film-cinedance/references/visual-continuity-audit.md",),
    "comparison": ("film-cinedance/references/visual-continuity-audit.md",),
    "image_assess": (),
    "product_placement": (),
    "publish": (),
    "assets": ("film-lira-assets/references/asset-strategy.md",),
    "h3_guides": ("film-cinedance/references/h3BaseGuide.txt", "film-cinedance/references/h3ReferenceGuide.txt"),
}


def load():
    files, texts, stages = {}, {}, {}
    def add(path, key, origin):
        raw = path.read_bytes()
        files[key] = dict(path=str(path.relative_to(ROOT)), sha256=hashlib.sha256(raw).hexdigest(),
                          source=dict(path=origin))
        texts[key] = raw.decode("utf-8")
        return key
    common = add(LOCAL / "SKILL.md", "unified_rules", "local adaptation of bundled benchmark")
    for stage, sources in SOURCES.items():
        keys = [] if stage in ("cold_read", "h3_guides") else [common]
        for n, source in enumerate(sources):
            key=add(SOURCE / source, stage + "_source_" + str(n), "yoko-1fa924b4abd6/" + source)
            # Provenance stays in the catalog; adapted execution must not also
            # send obsolete source workflows with contradictory review duties.
            if stage not in ('asset_design','acting','plan','prompts','cold_read','comparison'):
                keys.append(key)
        if stage == "prompts":
            for filename in ("SKILL.md", "references/base-en.txt", "references/ref-en.txt"):
                keys.append(add(ROOT / "h3-prompt-writing" / filename, "official_h3_" + Path(filename).stem,
                                "MiniMax-AI/MiniMax-H3@199b7d0/skills/h3-prompt-writing/" + filename))
        local = LOCAL / (stage + ".md")
        if local.exists():
            keys.append(add(local, stage + "_contract", "local capability and output adapter"))
        stages[stage] = keys
    return dict(id=METHOD_REF, files=files, texts=texts, stages=stages, projectFiles={}, projectTexts={},
                source=dict(benchmark="yoko-1fa924b4abd6", status="internal development",
                            licenseAudit="film-expert-bridge/references/source-audit.md"))
