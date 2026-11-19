"""The creative length recommendation recorded when a topic is proposed."""
import json

from jsonschema import Draft202012Validator

from .db import dumps
from .errors import Rejected

RUNTIME_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["form", "minSeconds", "maxSeconds"],
    "properties": {
        "form": {"type": "string", "enum": ["短篇", "长篇"]},
        "minSeconds": {"type": ["integer", "null"], "minimum": 1},
        "maxSeconds": {"type": ["integer", "null"], "minimum": 1},
    },
}
_VALIDATOR = Draft202012Validator(RUNTIME_SCHEMA)


def validate_runtime(target):
    if not _VALIDATOR.is_valid(target):
        raise Rejected("请标明长篇或短篇，并以正整数秒填写成片时长")
    low, high = target["minSeconds"], target["maxSeconds"]
    if (low is None and high is None) or (low is not None and high is not None and high < low):
        raise Rejected("请标明长篇或短篇，并填写有效的成片时长，范围从短到长。")


def save_runtime(db, topic_id, target):
    validate_runtime(target)
    db.execute("INSERT INTO topic_runtime(topic_id,runtime) VALUES(?,?)", (topic_id, dumps(target)))


def read_runtime(db, topic_id):
    record = db.execute("SELECT runtime FROM topic_runtime WHERE topic_id=?", (topic_id,)).fetchone()
    return json.loads(record[0]) if record else None
