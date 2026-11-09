"""Local CLI connection preferences. Credentials remain under the native CLI profile."""
import os
import json
from urllib.parse import urlsplit

from .db import transaction, dumps
from .errors import Conflict, Rejected


def proxy_url(value):
    if value in ("system", "off"):
        return value
    try:
        url = urlsplit(value)
        if (url.scheme in {"http","https"} and url.hostname in {"localhost","127.0.0.1","::1"} and url.port
                and url.username is None and url.password is None and url.path in ("","/") and not url.query and not url.fragment):
            return value
    except (TypeError, ValueError, AttributeError):
        pass
    raise Rejected("请填写本机 HTTP 或 HTTPS 代理地址和端口")


def launch_env(provider, connection):
    key = {"codex":"CODEX_HOME","claude":"CLAUDE_CONFIG_DIR"}.get(provider)
    env = {key:os.environ[key]} if key and os.environ.get(key) else {}
    proxy = proxy_url(connection.get("proxy", "system"))
    if proxy != "system":
        target = "" if proxy == "off" else proxy
        env.update({k:target for k in ("HTTPS_PROXY","https_proxy","HTTP_PROXY","http_proxy")})
    return env


class ConnectionsMixin:
    def executor_connection(self, provider):
        if provider not in self.executors:
            raise Rejected("未配置的执行器")
        return self.get_setting("connection:"+provider, dict(revision=0,proxy="system",model=None,effort=None))

    def save_executor_connection(self, provider, revision, proxy="system", model=None, effort=None):
        if provider not in self.executors:
            raise Rejected("未配置的执行器")
        if getattr(self,"parent",None):
            raise Rejected("请在主工作台管理本机执行器")
        proxy = proxy_url(proxy)
        self.validate_selection(provider,model,effort)
        with transaction(self.db_path) as db:
            row = db.execute("SELECT value FROM app_setting WHERE key=?",("connection:"+provider,)).fetchone()
            if (json.loads(row[0])["revision"] if row else 0) != revision:
                raise Conflict("执行器设置已变化，请刷新")
            result = dict(revision=revision+1,proxy=proxy,model=model,effort=effort)
            db.execute("INSERT INTO app_setting VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       ("connection:"+provider,dumps(result)))
        return result

    def executor_identity(self, provider):
        from .executors.cli import CliExecutor
        if provider not in self.executors:
            raise Rejected("未配置的执行器")
        executor = self.executors[provider]
        if not isinstance(executor,CliExecutor):
            simulated = executor.probe().get("structured_result") == "simulated"
            return dict(authenticated=None, accountLabel=None,simulated=simulated)
        binary = executor.binary()
        if not binary:
            return dict(authenticated=None,accountLabel=None,unavailable=True)
        return executor.native_identity(binary,launch_env(provider,self.executor_connection(provider)))

    def checked_connection(self, slot):
        slot = dict(slot)
        expected = slot.pop("connectionRevision",None)
        if expected is not None and self.executor_connection(slot["provider"])["revision"] != expected:
            raise Conflict("执行器连接设置已更改，请刷新连接后再发送")
        return slot

    def run_connection(self, provider, model=None, effort=None):
        settings = self.executor_connection(provider)
        if model is None:
            model = settings["model"]
        if effort is None and model == settings["model"]:
            effort = settings["effort"]
        return dict(proxy=settings["proxy"],model=model,effort=effort)
