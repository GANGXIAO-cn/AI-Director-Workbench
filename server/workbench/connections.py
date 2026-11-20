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
    def active_selection(self, slot):
        """Resolve retired CLI selections without changing historical records."""
        if slot.get('provider') != 'claude':
            return dict(slot)
        saved=self.get_setting('executor-defaults',{}).get('create',{})
        if saved.get('provider') in self.executors and saved.get('provider')!='claude':
            return dict(saved)
        provider=next((p for p in ('codex','antigravity') if p in self.executors),None)
        if provider is None:
            raise Rejected('请配置 Codex 或 Antigravity 执行器')
        return dict(provider=provider,model=None,effort=None)

    def executor_connection(self, provider):
        if provider not in self.executors:
            raise Rejected("未配置的执行器")
        result = self.get_setting("connection:"+provider, dict(revision=0,proxy="system",model=None,effort=None))
        return result

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
        if provider.startswith("api:"):
            return dict(authenticated=executor.probe()["authenticated"], accountLabel=None)
        if not isinstance(executor,CliExecutor):
            simulated = executor.probe().get("structured_result") == "simulated"
            return dict(authenticated=None, accountLabel=None,simulated=simulated)
        binary = executor.binary()
        if not binary:
            return dict(authenticated=None,accountLabel=None,unavailable=True)
        if provider == 'antigravity':
            saved = self.get_setting('identity:antigravity', {})
            if (saved.get('executable') == str(binary)
                    and saved.get('connectionRevision') == self.executor_connection(provider)['revision']):
                if 0 <= self.clock() - saved.get('checkedAt', 0) < 300:
                    return saved['identity']
                if saved.get('identity', {}).get('authenticated'):
                    return dict(authenticated=None, accountLabel=None,
                                lastVerifiedAccount=saved['identity']['accountLabel'], checkedAt=saved['checkedAt'])
        return executor.native_identity(binary,launch_env(provider,self.executor_connection(provider)))

    def verify_executor_identity(self, provider, command_id=None):
        if provider != 'antigravity':
            return self.executor_identity(provider)
        from .executors.agy_identity import verify
        from .executors.cli import CliExecutor
        executor = self.executors.get(provider)
        if not isinstance(executor, CliExecutor):
            return self.executor_identity(provider)
        binary = executor.binary()
        if not binary:
            return dict(authenticated=None, accountLabel=None, unavailable=True)
        connection = self.executor_connection(provider)
        identity = verify(binary, launch_env(provider, connection))
        self.put_setting('identity:antigravity', dict(executable=str(binary),
                         connectionRevision=connection['revision'], checkedAt=self.clock(), identity=identity))
        return identity

    def checked_connection(self, slot):
        if slot.get('provider')=='claude':
            return self.active_selection(slot)
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
        connection = dict(proxy=settings["proxy"],model=model,effort=effort)
        if provider.startswith("api:"):
            connection["binding"] = self.executors[provider].config()["binding"]
        return connection
