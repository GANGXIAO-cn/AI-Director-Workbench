"""Normalized model-result contract (v1) and vendor-shaped branch schemas."""
import copy
import json

from jsonschema import Draft202012Validator

from .methodpack import CONTRACT

SCHEMA = json.loads(CONTRACT.read_text())
VALIDATOR = Draft202012Validator(SCHEMA)
BRANCH = {"draft": "draft", "review": "review", "proposal": "proposal", "image_assessment": "imageAssessment", "clarification": "clarification", "cut_explanation": "cutExplanation"}


def validate(result) -> list[str]:
    return [e.message for e in VALIDATOR.iter_errors(result)][:5]


def branch_schema(kind: str) -> dict:
    """Single branch with $refs inlined, for CLIs that accept a JSON Schema subset."""
    defs = SCHEMA["$defs"]

    def inline(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return inline(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            return {k: inline(v) for k, v in node.items()}
        if isinstance(node, list):
            return [inline(v) for v in node]
        return node

    schema = inline(copy.deepcopy(defs[BRANCH[kind]]))
    if kind == "review":
        # Strict structured output requires every declared key on newly generated
        # results. The ingestion contract still accepts old issues without this key.
        schema["properties"]["issues"]["items"]["required"].append("decisionId")
    return schema


def wrapped_schema() -> dict:
    """{result: draft | clarification} for runs where the assistant may ask first."""
    return {"type": "object", "properties": {"result": {"anyOf": [branch_schema("draft"), branch_schema("clarification")]}},
            "required": ["result"], "additionalProperties": False}


def stage_dialogue_schema(track_removals=False, read_only=False):
    schema = branch_schema("draft")
    fields = schema["properties"]
    fields["kind"] = {"enum":["discussion"] if read_only else ["discussion","draft"]}
    fields["changes"]["minItems"] = 0
    fields["reply"] = {"type":"string"}
    fields["directorRequest"] = {"type":"string"}
    schema["required"].extend(["reply","directorRequest"])
    if track_removals:
        fields["removedIds"] = {"type":"array","items":{"type":"object","properties":{
            "field":{"type":"string"},"id":{"type":"string"}},"required":["field","id"],"additionalProperties":False}}
        schema["required"].append("removedIds")
    return schema


def omit_properties(schema, *keys):
    for key in keys:
        schema["properties"].pop(key, None)
    schema["required"] = [key for key in schema["required"] if key not in keys]


def content_schema(kind):
    schema = branch_schema(kind)
    omit_properties(schema, "schemaVersion", "kind", "runId", "inputFingerprint")
    if kind == "proposal":
        omit_properties(schema["properties"]["items"]["items"]["properties"]["source"], "versionId")
    if kind == "cut_explanation":
        omit_properties(schema, "atMs")
    return schema


def review_content_schema(pass_name):
    schema = content_schema("review")
    omit_properties(schema, "pass", "reviewedVersionId")
    props = schema["properties"]
    observation = props["observations"]["items"]
    location = props["issues"]["items"]["properties"]["location"]
    omit_properties(observation, "versionId")
    omit_properties(location, "versionId")
    if pass_name not in ("plan", "prompts", "script"):
        omit_properties(location, "objectId")
    if pass_name == "cold_read":
        omit_properties(observation, "objectId")
    return schema


def writing_schema(dialogue=None, clarify=False):
    """Model-facing subset. The complete internal contract still validates before saving."""
    schema = (stage_dialogue_schema(dialogue.get("auto"), bool(dialogue.get("internalJudgment")))
              if dialogue else wrapped_schema() if clarify else branch_schema("draft"))
    branches = schema["properties"]["result"]["anyOf"] if clarify else [schema]
    for branch in branches:
        fixed = {"schemaVersion", "runId", "inputFingerprint", "baseVersionId"}
        if not (dialogue or clarify):
            fixed.add("kind")
        props = branch["properties"]
        omit_properties(branch, *fixed)
        if "changes" in props:
            item = props["changes"]["items"]
            omit_properties(item, "targetId")
    return schema
