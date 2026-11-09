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

    return inline(copy.deepcopy(defs[BRANCH[kind]]))


def wrapped_schema() -> dict:
    """{result: draft | clarification} for runs where the assistant may ask first."""
    return {"type": "object", "properties": {"result": {"anyOf": [branch_schema("draft"), branch_schema("clarification")]}},
            "required": ["result"], "additionalProperties": False}


def stage_dialogue_schema():
    schema = branch_schema("draft")
    fields = schema["properties"]
    fields["kind"] = {"enum":["discussion","draft"]}
    fields["changes"]["minItems"] = 0
    fields["reply"] = {"type":"string"}
    fields["directorRequest"] = {"type":"string"}
    schema["required"].extend(["reply","directorRequest"])
    return schema
