"""H-T0. Claude runs --execute with three supplied originals; default only freezes inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import uuid

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'server'))
from workbench.executors.base import ExecRequest
from workbench.executors.cli import CodexExecutor


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--images',nargs=3,type=Path,required=True,metavar='IMAGE')
    parser.add_argument('--execute',action='store_true',help='实际调用 Codex；仅由真实模型验收者使用')
    parser.add_argument('--model');parser.add_argument('--effort',default='high')
    args=parser.parse_args(argv)
    folder=Path(tempfile.mkdtemp(prefix='adw-image-read-probe-'))
    records=[];images=[]
    for i,source in enumerate(args.images,1):
        data=source.read_bytes();target=folder/(str(i)+source.suffix);target.write_bytes(data);images.append(target)
        records.append(dict(order=i,source=str(source.resolve()),path=str(target),sha256=hashlib.sha256(data).hexdigest()))
    (folder/'inputs.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
    print(folder)
    if not args.execute:return 0
    schema=dict(type='object',properties={'images':dict(type='array',items=dict(type='object',properties={'order':dict(type='integer'),'observation':dict(type='string')},required=['order','observation'],additionalProperties=False))},required=['images'],additionalProperties=False)
    work=folder/'work';work.mkdir()
    request=ExecRequest(run_id=str(uuid.uuid4()),purpose='image_assess',kind='discussion',prompt='实际打开所附三张图，按输入顺序逐张描述人物、物体、状态与空间关系。第三张为九宫格时逐格说明。看不清的如实说明，不从文件名推断。返回 images 数组，每项 order 与 observation。',schema=schema,session_mode='ephemeral',workdir=work,model=args.model,effort=args.effort,images=images)
    result=CodexExecutor().start(request)
    (folder/'result.json').write_text(json.dumps(dict(ok=result.ok,reply=result.structured,errorClass=result.error_class,diagnostic=result.diagnostic),ensure_ascii=False,indent=2))
    return 0 if result.ok else 1

if __name__=='__main__':raise SystemExit(main())
