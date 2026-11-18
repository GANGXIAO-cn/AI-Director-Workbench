#!/bin/zsh
cd -- "${0:A:h}" || exit 1
export ADW_DATA_DIR="$PWD/data" ADW_PORT=8765
unset ADW_FAKE
url="http://127.0.0.1:$ADW_PORT"
export PATH="/Library/Frameworks/Python.framework/Versions/3.11/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
desktop_codex="/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
if [[ -z "${ADW_CODEX_PATH:-}" && -x "$desktop_codex" ]]; then
  export ADW_CODEX_PATH="$desktop_codex"
fi

# 只打开使用这个项目数据目录的实例。
ready() {
  curl --noproxy '*' -fsS --max-time 2 "$url/api/q/storage_status" 2>/dev/null |
    python3 -c 'import json, os, sys; from pathlib import Path; sys.exit(Path(json.load(sys.stdin)["dataDir"]).resolve() != Path(os.environ["ADW_DATA_DIR"]).resolve())' 2>/dev/null
}

if ! ready && lsof -nP -iTCP:"$ADW_PORT" -sTCP:LISTEN >/dev/null; then
  echo "端口 $ADW_PORT 正被其他服务使用。请先检查该服务。"
  exit 1
fi
if ready && ! curl --noproxy '*' -fsS --max-time 2 "$url/api/session" |
    python3 -c 'import json,sys; sys.exit(not {"save_stage_executor","start_repair_plan","initialize_asset_worksheet","adopt_project_stage_methods"}.issubset(json.load(sys.stdin).get("commands", [])))'; then
  echo '当前工作台仍在使用旧后端。请等正在执行的任务结束，在原服务终端按 Control-C，再双击本启动文件。'
  exit 1
fi
# 打开前构建；已打开的页面仍可能请求上一版带哈希的静态资源。
npm --prefix web run build -- --emptyOutDir=false || exit 1
if ready; then
  open "$url"
  exit $?
fi
echo "工作台地址 $url"
echo '使用期间请保留此终端窗口。按 Control-C 可停止工作台。'
(
  for attempt in {1..60}; do
    if ready; then
      open "$url"
      exit
    fi
    sleep 1
  done
  echo '页面尚未就绪，请查看本窗口中的启动信息。'
) &
export PYTHONPATH="$PWD/server${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m workbench.app
