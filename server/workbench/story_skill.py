"""Read provenance for archived experimental story-skill runs."""
def historical_method(entry, manifest):
    if entry["role"] == "story_skill_file" and entry["name"].startswith("skill/"):
        return dict(path=entry["name"], source=dict(path=entry["name"], commit=manifest.get("storySkillCommit")))
    return None
