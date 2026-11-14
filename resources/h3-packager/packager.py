#!/usr/bin/env python3
"""Offline project packager. No model, network service or skill dependency."""
import argparse, hashlib, json, re, secrets, threading, webbrowser, zipfile, io, base64, mimetypes
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote, quote

DEFAULT_SERIES = Path.home() / 'Documents/自媒体 2/series/milo-leo'
MAX_FILE = 30 * 1024**2
MAX_TOTAL = 256 * 1024**2
DEFAULTS = dict(resolution='default', audio=True, candidates=1, seed=None, instanceType='default')

def digest(data): return hashlib.sha256(data).hexdigest()
def within(path, root): return path == root or root in path.parents

def locate(value):
    value=value.strip().strip('"').strip("'")
    if value.startswith('file:'):
        u=urlparse(value)
        if u.netloc not in ('','localhost'): raise ValueError('仅接受本机文件路径')
        value=unquote(u.path)
    elif '://' in value: raise ValueError('不支持HTTP网址，请粘贴本机HTML路径')
    page=Path(value).expanduser()
    if not page.is_absolute() or page.suffix.lower()!='.html' or not page.is_file(): raise ValueError('请选择存在的HTML绝对路径')
    page=page.resolve()
    for parent in (page.parent,page.parent.parent):
        manifest=parent/'episode.json'
        if manifest.is_file(): return manifest.resolve()
    raise ValueError('结果页所在目录或上一级没有episode.json')

def load_project(value, series=DEFAULT_SERIES):
    manifest=locate(value); root=manifest.parent
    raw=manifest.read_bytes(); d=json.loads(raw)
    if d.get('schema_version')!=2: raise ValueError('仅支持episode.json v2')
    if not isinstance(d.get('segments'),list) or not d['segments']: raise ValueError('缺少生成段')
    if len({s['id'] for s in d['segments']})!=len(d['segments']): raise ValueError('重复分段ID')
    assets=d.get('assets',[])
    if len({a['id'] for a in assets})!=len(assets): raise ValueError('重复资产ID')
    snapshot={}; snapshot_path=None; snapshot_raw=None
    if any(a.get('fixed_role') for a in assets):
        if not d.get('asset_snapshot'): raise ValueError('固定角色缺少本集asset_snapshot')
        snapshot_path=(root/d['asset_snapshot']).resolve()
        if not within(snapshot_path,root) or not snapshot_path.is_file() or snapshot_path.stat().st_size>200000: raise ValueError('角色快照路径或大小无效')
        snapshot_raw=snapshot_path.read_bytes(); snapshot={a['id']:a for a in json.loads(snapshot_raw)['assets']}
    return dict(root=root,manifest=manifest,raw=raw,data=d,series=Path(series).resolve(),snapshot=snapshot,snapshot_path=snapshot_path,snapshot_raw=snapshot_raw)

def read_asset(project,a):
    p=Path(a['path']); p=(p if p.is_absolute() else project['root']/p).resolve()
    if not within(p,project['root']) and not (a.get('fixed_role') and within(p,project['series'])): raise ValueError('素材路径超出项目及系列资产目录')
    if a.get('fixed_role'):
        lock=project['snapshot'].get(a['fixed_role'])
        if not lock or lock.get('sha256')!=a.get('sha256') or lock.get('version')!=a.get('version') or Path(lock['path']).resolve()!=p: raise ValueError('固定角色与本集快照不一致：'+a['id'])
    if not p.is_file() or not 0<p.stat().st_size<=MAX_FILE: raise ValueError(f"素材缺失或超过30MB：{a['id']}")
    data=p.read_bytes()
    if digest(data)!=a.get('sha256'): raise ValueError(f"素材指纹不符：{a['id']}")
    return p,data

def package_contents(project,selected,overrides=None):
    if project['manifest'].read_bytes()!=project['raw']: raise ValueError('清单已变化，请重新读取项目')
    if project.get('snapshot_path') and project['snapshot_path'].read_bytes()!=project['snapshot_raw']: raise ValueError('角色快照已变化，请重新读取项目')
    d=project['data']; wanted=set(selected)
    if not wanted or len(wanted)!=len(selected) or wanted-{s['id'] for s in d['segments']}: raise ValueError('请选择有效且不重复的生成段')
    segments=[s for s in d['segments'] if s['id'] in wanted]
    by_id={a['id']:a for a in d['assets']}; used={r['asset_id'] for s in segments for r in s['references']}
    if used-by_id.keys(): raise ValueError('参考素材不在资产表中')
    files={}; packed_assets=[]; total=0
    for key,a in by_id.items():
        if key not in used: continue
        p,content=read_asset(project,a); total+=len(content)
        if total>MAX_TOTAL: raise ValueError('素材合计超过256MB')
        if a['type'] not in ('image','video','audio'): raise ValueError('不支持的素材类型')
        ext=p.suffix.lower(); name=f'assets/{len(packed_assets)+1:04d}{ext}'
        files[name]=content
        entry=dict(id=key,path=name,name=p.name,kind=a['type'],sha256=digest(content),origin=a['origin'],version=a['version'])
        if a.get('fixed_role'): entry['fixedRole']=a['fixed_role']
        packed_assets.append(entry)
    packed_segments=[]
    for s in segments:
        duration=s['duration_s']
        if isinstance(duration,bool) or not isinstance(duration,(int,float)) or int(duration)!=duration or not 5<=duration<=15: raise ValueError(f"第{s['id']}段需为5–15秒整数")
        refs=sorted(s['references'],key=lambda r:r['upload_order'])
        if [r['upload_order'] for r in refs]!=list(range(1,len(refs)+1)): raise ValueError('上传顺序不连续')
        if len({r['asset_id'] for r in refs})!=len(refs): raise ValueError('同段参考重复')
        counts=dict(image=0,video=0,audio=0)
        for r in refs:
            kind=by_id[r['asset_id']]['type']; counts[kind]+=1
            n=counts[kind]; expected=(dict(image='图片',video='视频',audio='音频')[kind]+str(n)) if s.get('label_style','chinese')=='chinese' else '@'+dict(image='Image',video='Video',audio='Audio')[kind]+str(n)
            if r['label']!=expected: raise ValueError('参考标签与顺序不符')
        if not refs or len(refs)>12 or counts['image']>9 or counts['video']>1 or counts['audio']>3: raise ValueError('参考数量超出H3限制')
        p=(project['root']/s['prompt']).resolve()
        if not within(p,project['root']) or not p.is_file() or p.stat().st_size>200000: raise ValueError('提示词路径或大小无效')
        prompt=p.read_text(encoding='utf-8')
        labels={r['label'] for r in refs}
        if not prompt.strip() or len(prompt)>50000: raise ValueError('提示词为空或过长')
        for label in re.findall(r'(?:图片|视频|音频)\d+|@(?:Image|Video|Audio)\d+',prompt):
            if label not in labels: raise ValueError('提示词包含未知参考标签：'+label)
        params=dict(DEFAULTS); settings=s.get('generation_settings') or {}
        for key in DEFAULTS:
            if key in settings: params[key]=settings[key]
        params.update(overrides or {}); params.update(ratio=d['aspect_ratio'],duration=int(duration))
        if params['resolution'] not in ('default','480p','768p','1080p') or params['instanceType'] not in ('default','plus','ultra') or type(params['audio'])!=bool or type(params['candidates'])!=int or params['candidates'] not in (1,2,4): raise ValueError('生成参数无效')
        if params['seed'] is not None and (type(params['seed'])!=int or not 0<=params['seed']<=9007199254740987): raise ValueError('种子无效')
        if params['ratio'] not in ('1:1','2:3','3:2','3:4','4:3','9:16','16:9','21:9'): raise ValueError('画幅无效')
        packed_segments.append(dict(id=s['id'],title=s.get('name') or s['id'],prompt=prompt,params=params,refs=[dict(assetId=r['asset_id'],label=r['label']) for r in refs]))
    package=dict(schemaVersion=1,scope='episode' if len(segments)==len(d['segments']) else 'segments',episodeId=d['episode_id'],title=d['english_title'],manifestSha256=digest(project['raw']),assets=packed_assets,segments=packed_segments)
    suffix='整集' if package['scope']=='episode' else '-'.join(s['id'] for s in segments)
    stem=re.sub(r'[^\w\-\u4e00-\u9fff]','_',d['episode_id'])[:100]
    filename=f'{stem}_{suffix}_{datetime.now():%Y%m%d_%H%M%S_%f}_{secrets.token_hex(4)}.h3project.zip'
    return filename,package,files

def write_zip(stream,package,files):
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as z:
        z.writestr('project.json',json.dumps(package,ensure_ascii=False))
        for name,content in files.items(): z.writestr(name,content)

def download_package(project,selected,overrides=None):
    filename,package,files=package_contents(project,selected,overrides)
    stream=io.BytesIO();write_zip(stream,package,files)
    return filename,stream.getvalue(),package

def build_package(project,selected,overrides=None,output=None):
    # Explicit file export remains available for offline fixtures and scripts.
    filename,package,files=package_contents(project,selected,overrides)
    directory=Path(output).expanduser() if output else project['root']/'正式交付/系统导入包'
    if not directory.is_absolute(): raise ValueError('输出目录需为绝对路径')
    directory.mkdir(parents=True,exist_ok=True)
    path=directory/filename;pending=path.with_suffix(path.suffix+'.partial')
    with pending.open('xb') as stream: write_zip(stream,package,files)
    if path.exists(): raise ValueError('目标文件已存在，请重新打包')
    pending.rename(path)
    return path,package

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def send_json(self,status,data):
        body=json.dumps(data,ensure_ascii=False).encode(); self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if self.headers.get('Host')!=self.server.authority: return self.send_json(403,dict(error='访问地址不允许'))
        if self.path!='/': return self.send_json(404,dict(error='不存在'))
        page=(Path(__file__).parent/'index.html').read_text().replace('__TOKEN__',self.server.token)
        self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('X-Frame-Options','DENY');self.end_headers();self.wfile.write(page.encode())
    def do_POST(self):
        if self.headers.get('Host')!=self.server.authority or self.headers.get('Origin')!=f'http://{self.server.authority}' or self.headers.get('X-Packager-Token')!=self.server.token: return self.send_json(403,dict(error='请求来源不允许'))
        try:
            n=int(self.headers.get('Content-Length','0'))
            if not 0<n<=65536: raise ValueError('请求过大或为空')
            body=json.loads(self.rfile.read(n))
            with self.server.operation_lock:
                if self.path=='/read':
                    project=load_project(body['path'],self.server.series); self.server.projects={}; key=secrets.token_urlsafe(24);self.server.projects[key]=project
                    d=project['data']; return self.send_json(200,dict(key=key,title=d['english_title'],directory=str(project['root']),manifestSha256=digest(project['raw']),assets=[dict(id=a['id'],name=Path(a['path']).name,kind=a['type'],fixed=bool(a.get('fixed_role')),origin=a['origin']) for a in d['assets']],segments=[dict(id=s['id'],name=s.get('name',s['id']),duration=s['duration_s'],references=[r['asset_id'] for r in s['references']]) for s in d['segments']]))
                if self.path=='/asset':
                    project=self.server.projects.get(body['key'])
                    if not project: raise ValueError('请重新读取项目')
                    asset=next((a for a in project['data']['assets'] if a['id']==body['id']),None)
                    if not asset or asset['type']!='image': raise ValueError('仅支持图片预览')
                    path,content=read_asset(project,asset)
                    if path.suffix.lower() not in ('.png','.jpg','.jpeg','.webp'): raise ValueError('不支持的图片格式')
                    mime=mimetypes.guess_type(path.name)[0]
                    return self.send_json(200,dict(dataUrl='data:'+mime+';base64,'+base64.b64encode(content).decode()))
                if self.path=='/pack':
                    project=self.server.projects.get(body['key'])
                    if not project: raise ValueError('请重新读取项目')
                    filename,content,package=download_package(project,body['segments'],body.get('params'))
                    self.send_response(200)
                    self.send_header('Content-Type','application/zip')
                    self.send_header('Content-Disposition',"attachment; filename=project.h3project.zip; filename*=UTF-8''"+quote(filename,safe=''))
                    self.send_header('Content-Length',str(len(content)))
                    self.send_header('X-Package-Segments',str(len(package['segments'])))
                    self.send_header('X-Package-Assets',str(len(package['assets'])))
                    self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(content);return
                if self.path=='/shutdown':
                    self.send_json(200,dict(ok=True));threading.Thread(target=self.server.shutdown,daemon=True).start();return
                self.send_json(404,dict(error='不存在'))
        except Exception as e: self.send_json(400,dict(error=str(e)))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--series-root',type=Path,default=DEFAULT_SERIES);parser.add_argument('--no-browser',action='store_true');parser.add_argument('--port',type=int,default=0);args=parser.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler);server.token=secrets.token_urlsafe(32);server.authority=f'127.0.0.1:{server.server_port}';server.series=args.series_root.resolve();server.projects={};server.operation_lock=threading.Lock()
    url=f'http://{server.authority}/';print('本机打包工具：'+url,flush=True)
    if not args.no_browser: webbrowser.open(url)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
if __name__=='__main__': main()
