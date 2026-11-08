import { SignalList, type SignalReport } from "./PromptSignals";
import { SceneContact } from "./SceneContact";
import { useState } from "react";
import { blob, cmd } from "./api";
import { ErrorLine, useAction, useQuery } from "./ui";

type Product = {code:string; name:string; english:string; description:string; sha256:string; commit:string};
type Section = {id:string; group:string; title:string; start:number; end:number; sha256:string; path:string; text?:string};
type Catalog = {products:Product[]; sections:Section[]};
type Props = {pid:string; eid?:string; stage?:string; refresh?:()=>void; productsOnly?:boolean; shots?:Array<{id:string;segment:string;action:string}>; images?:Array<{id:string;name:string;category:string;sha256:string;marks?:string[]}>};
const groups:Record<string,string> = {examples:"制作案例","user-priority-videos":"自家重点作品","product-placement":"商品入景方法","planning-contract":"选题规划"};

export function ProjectLibrary({pid,eid,stage,refresh,productsOnly,shots=[],images=[]}:Props) {
  const args = {project_id:pid,episode_id:eid};
  const library = useQuery<Catalog>("project_resources",args);
  const [selection,setSelection] = useState("");
  const [code,setCode] = useState("");
  const [added,setAdded] = useState("");
  const [shot,setShot] = useState(""), [intent,setIntent] = useState("");
  const [scene,setScene] = useState(""), [observation,setObservation] = useState("");
  const placements = useQuery<any>(productsOnly && eid ? "product_placements" : null,{episode_id:eid},[images.map(i=>i.id+(i.marks ?? []).join()).join(),shots.map(s=>s.id+s.action).join()]);
  const resetPlacement = () => {setShot("");setIntent("");setScene("");setObservation("");};

  const excerpt = useQuery<Section>(selection ? "project_section" : null,{...args,section_id:selection});
  const selected = useQuery<Section[]>(eid && stage ? "episode_project_sections" : null,{episode_id:eid,stage});
  const action = useAction();
  if (library.error) return <div className="error">{library.error}</div>;
  if (!library.data?.products.length && !library.data?.sections.length) return null;
  const {sections,products} = library.data;
  const product = products.find(p=>p.code===code);
  const keys = (selected.data ?? []).map(s=>s.id);
  const change = (ids:string[]) => action.run(async()=>{
    await cmd("set_project_sections",{episode_id:eid,stage,section_ids:ids});
    await selected.reload();
  });
  return <details className="card"><summary>{productsOnly ? "项目商品主图" : "项目制作资料"}</summary>
    {!productsOnly && <div className="col"><label>案例与方法原文 <select value={selection} onChange={e=>setSelection(e.target.value)}>
      <option value="">选择小节</option>{Object.entries(groups).map(([group,label])=><optgroup key={group} label={label}>
        {sections.filter(s=>s.group===group).map(s=><option key={s.id} value={s.id}>{s.title}</option>)}</optgroup>)}</select></label>
      {excerpt.data && <><pre style={{whiteSpace:"pre-wrap",maxHeight:320,overflow:"auto"}}>{excerpt.data.text}</pre>
        <small>{excerpt.data.path} · 第 {excerpt.data.start}–{excerpt.data.end} 行 · {excerpt.data.sha256.slice(0,12)}</small>
        {eid && stage && <button className="btn" disabled={action.busy || !selected.data || keys.includes(selection)}
          onClick={()=>change([...keys,selection])}>加入本阶段引用</button>}</>}
      {(selected.data ?? []).map(s=><div className="row" key={s.id}><span>{s.title}</span><button className="btn" disabled={action.busy} onClick={()=>change(keys.filter(k=>k!==s.id))}>移除引用</button></div>)}
    </div>}
    {(!stage || productsOnly) && <div className="col"><label>商品快照 <select value={code} onChange={e=>{setCode(e.target.value);setAdded("");resetPlacement();}}>
      <option value="">查看商品主图（{products.length} 款）</option>{products.map(p=><option key={p.code} value={p.code}>{p.code} · {p.name}</option>)}</select></label>
      {product && <div className="row"><img width={180} alt={product.name} src={`/api/project-product/${pid}/${product.code}${eid?"?episode_id="+eid:""}`}/>
        <div><b>{product.english}</b><p>{product.description}</p><small>来源版本 {product.commit.slice(0,12)} · 主图 {product.sha256.slice(0,12)}</small>
        {eid && <div><button className="btn" disabled={action.busy} onClick={()=>action.run(async()=>{
          await cmd("use_project_product",{episode_id:eid,code});setAdded(code);await placements.reload();refresh?.();
        })}>登记为本集商品参考图</button>{added===code && <span> 已登记</span>}</div>}</div></div>}</div>}
    {productsOnly && eid && placements.data?.minimum > 0 && <div className="col">
      <b>商品入景 · 静态覆盖 {placements.data.verified}/{placements.data.minimum}</b>
      {placements.data.records.map((r:any)=><div key={r.code+r.shot_id}>
        {r.product.name} · {r.segment} / {r.shot_id} · {r.issue || "已核对采用输入"}
        <button className="btn" onClick={()=>{setCode(r.code);setShot(r.shot_id);setIntent(r.intent);setScene(r.scene_version ?? "");setObservation(r.observation);}}>编辑记录</button></div>)}
      {placements.data.products.some((p:Product)=>p.code===code) && <>
        <label>关联镜头 <select value={shot} onChange={e=>{setShot(e.target.value);setScene("");setObservation("");}}>
          <option value="">选择采用方案的镜头</option>{shots.map(s=><option key={s.id} value={s.id}>{s.segment} · {s.id} · {s.action.slice(0,35)}</option>)}</select></label>
        <label>入景设计<textarea value={intent} placeholder="使用理由、位置与朝向、需要保留的识别特征" onChange={e=>setIntent(e.target.value)}/></label>
        <label>本集场面图 <select value={scene} onChange={e=>{setScene(e.target.value);setObservation("");}}>
          <option value="">先保存设计</option>{images.filter(a=>["state","grid","scene"].includes(a.category)).map(a=><option key={a.id} value={a.id}>{a.name} · {a.sha256.slice(0,8)}</option>)}</select></label>
        {scene && <><img width={260} alt="用于入景核对的场面原图" src={blob(images.find(a=>a.id===scene)?.sha256)}/>
          <label>实际看图观察<textarea value={observation} placeholder="记录款式可辨识程度、自然性与接续" onChange={e=>setObservation(e.target.value)}/></label></>}
        <button className="btn" disabled={action.busy || !shot || !intent.trim() || (!!scene && !observation.trim())} onClick={()=>action.run(async()=>{
          await cmd("save_product_placement",{episode_id:eid,code,shot_id:shot,intent,scene_version:scene||null,observation});
          await placements.reload();refresh?.();
        })}>保存入景记录</button>
      </>}
    </div>}
    <ErrorLine error={action.error}/>
  </details>;
}

type FrameMeasurements = {target:string;segments:number;grids:Record<string,{asset:string;width:number;height:number}>};
type HistoricalResult = {details?:unknown;state:string;message:string;warnings?:{code:string;message:string;asset_id?:string;asset_ids?:string[];scene_id?:string;request_id?:unknown}[]};
type Receipt = {receipt:string; folder:string; files:Record<string,string>; checks:Record<string,HistoricalResult>;rechecked?:Record<string,HistoricalResult>;
  archive?:{referenceReports?:{source:string;error?:string;missing:string[];resources:Record<string,string>}[];unavailable:Record<string,string>;fixedError?:string;fixed:Record<string,{sha256:string}>;standards:Record<string,{path:string;sha256:string}>};
  materialChecks?:Record<string,HistoricalResult>;materialRechecked?:Record<string,HistoricalResult>;integrity?:{checked:number;ok:boolean;issues:{sha256:string;problem:string}[]}};

type PageEvidence = {roles:{role:string;name:string;segments:string[];assetId?:string}[];
  issues:{location:string;state:string;message:string}[];segments:{segment:string;textState:string;mappingState:"passed"|"incomplete"|"failed";difference?:{line:number;page:string;expected:string};state:string;promptSha256?:string;
  message?:string;linkDifferences?:{label:string;assetId:string;expectedPath:string;pageLinks:string[]}[];inputs:{label:string;assetId:string;sha256:string;shots:string[]}[]}[]};
function HistoricalPageEvidence({report}:{report:PageEvidence}) {
  return <details><summary>查看页面逐段核对</summary>
    {report.roles.map(r=><p key={r.role}>{r.name} · {r.role} · {r.segments.join("、") || "未出镜"}{r.assetId && " · "+r.assetId}</p>)}
    {report.issues.filter(i=>!report.segments.some(s=>s.segment===i.location)).map((i,n)=><p key={n}>{i.location} · {i.message}</p>)}
    {report.segments.map(s=><details key={s.segment}><summary>{s.segment} · TXT {s.textState==="passed"?"一致":"未通过"} · 上传映射{({passed:"一致",incomplete:"待核对",failed:"未通过"})[s.mappingState]}</summary>
      {s.message && <p>{s.message}</p>}
      {s.difference && <div>首处差异在原文第 {s.difference.line} 行<p>页面显示</p><pre>{s.difference.page}</pre><p>独立 TXT</p><pre>{s.difference.expected}</pre></div>}
      {s.linkDifferences?.map(r=><p key={r.label}>{r.label} · {r.assetId}<br/>页面链接 · {r.pageLinks.join("、")}<br/>清单文件 · {r.expectedPath}</p>)}
      {s.promptSha256 && <p><a href={blob(s.promptSha256)} download={s.segment+".txt"}>下载本段原始 TXT</a> · <code>{s.promptSha256}</code></p>}
      <ol>{s.inputs.map(r=><li key={r.label}><a href={blob(r.sha256)} target="_blank" rel="noreferrer">{r.label} · {r.assetId}</a> {r.shots.join("、")}</li>)}</ol>
    </details>)}
  </details>;
}

function GridMeasurements({details}:{details:FrameMeasurements}) {
  const grids = Object.entries(details.grids);
  if (!grids.length) return null;
  return <details><summary>查看各段九宫格尺寸</summary><ul>
    {grids.map(([sid,grid])=><li key={sid}>段 {sid} · {grid.asset} · {grid.width} × {grid.height} 像素</li>)}
  </ul></details>;
}

type InventoryReport = {
  excluded_projects:string[];
  images:{id:string;sha256:string;paths:string[];currentPaths:string[];usedIn:string[];
    priority?:string;target_segments?:string[];selection_reason?:string;limitations?:string}[];
};

function InventoryImages({report}:{report:InventoryReport}) {
  const [preview,setPreview] = useState("");
  const [group,setGroup] = useState("current");
  const [query,setQuery] = useState("");
  const [page,setPage] = useState(0);
  const filters:Record<string,(row:InventoryReport["images"][number])=>boolean> = {
    current:r=>r.currentPaths.length>0, used:r=>r.usedIn.length>0, required:r=>r.priority==="required", old:r=>!r.currentPaths.length};
  const needle = query.trim().toLowerCase();
  const rows = report.images.filter(row=>filters[group](row) &&
    (!needle || [row.id,row.sha256,...row.paths,...row.usedIn].some(text=>text.toLowerCase().includes(needle))));
  const pages = Math.max(1,Math.ceil(rows.length/30));
  const current = Math.min(page,pages-1);
  return <details><summary>查看图片来源与使用段</summary>
    <div className="row"><label>显示 <select aria-label="图片盘点范围" value={group} onChange={e=>{setGroup(e.target.value);setPage(0);}}>
      {Object.entries({current:"本集当前图片",used:"正式已用",required:"必用图",old:"仅历史登记"}).map(([id,title])=>
        <option key={id} value={id}>{title}</option>)}</select></label>
      <input aria-label="搜索盘点图片" placeholder="编号、路径、指纹或段号" value={query} onChange={e=>{setQuery(e.target.value);setPage(0);}}/>
      <span>{rows.length} 项</span></div>
    {report.excluded_projects.length>0 && <p>独立子集 · {report.excluded_projects.join("、")}</p>}
    <ul>{rows.slice(current*30,(current+1)*30).map(row=><li key={row.sha256}><b>{row.id}</b> · {row.usedIn.length ? "正式输入 "+row.usedIn.join("、")
      : row.currentPaths.length ? "本集候选" : "仅有历史登记"}
      <div>{row.paths.join("、")}</div>
      {row.priority==="required" && <div>必用 · {row.target_segments?.join("、") || "至少一段"} · {row.selection_reason}</div>}
      {row.limitations && <div>用途限制 · {row.limitations}</div>}
      <code>{row.sha256}</code> {row.currentPaths.length>0 && <button className="btn" onClick={()=>setPreview(preview===row.sha256 ? "" : row.sha256)}>
        {preview===row.sha256 ? "收起原图" : "查看冻结原图"}</button>}
      {preview===row.sha256 && <div><img src={blob(row.sha256)} alt={row.currentPaths[0]} style={{maxWidth:"100%",maxHeight:420}}/></div>}
    </li>)}</ul>{pages>1 && <div className="row">
      <button className="btn" disabled={current===0} onClick={()=>setPage(current-1)}>上一页</button>
      <span>{current+1} / {pages}</span>
      <button className="btn" disabled={current===pages-1} onClick={()=>setPage(current+1)}>下一页</button></div>}</details>;
}

export function HistoricalChecks({pid}:{pid:string}) {
  const list = useQuery<Array<{folder:string;receipt?:string;available:boolean;versions:{id:string;createdAt:number}[]}>>("historical_episodes",{project_id:pid});
  const [folder,setFolder] = useState("");
  const [imported,setImported] = useState<Receipt|null>(null);
  const [selectedReceipt,setSelectedReceipt] = useState("");
  const [includeImages,setIncludeImages] = useState(false);
  const source = list.data?.find(e=>e.folder===folder);
  const [openedInputs,setOpenedInputs] = useState("");
  const saved = selectedReceipt || source?.receipt;
  const previous = useQuery<Receipt>(saved ? "historical_receipt":null,{project_id:pid,receipt:saved});
  const inputs = useQuery<{record:string}>(saved && openedInputs===saved ? "historical_review_inputs":null,{project_id:pid,receipt:saved});
  const action = useAction();
  const record = previous.data?.receipt===saved && previous.data?.folder===folder ? previous.data
    : imported?.folder===folder && imported.receipt===saved ? imported : null;
  const names:Record<string,string> = {pageContent:"历史图文页与实际输入",h3Sound:"H3 分段声音声明",imageInventory:"本集图片盘点",promptSignals:"提示词通读线索",aspect:"集画幅与九宫格尺寸",sceneAnchors:"场景锁定图与实际生成输入",sceneBaselines:"固定场景资产来源",manifest:"集清单",delivery:"图文交付",publish:"发布包",assets:"素材表与原文件",inputs:"分段输入与时长",prompts:"分段提示词",cast:"角色契约与身份绑定",appearance:"出镜角色与外观标准",depictions:"参考图开场时刻与场景锁定图",reviews:"审核依据与问题处置",promptReview:"提示词独审与实际送审文件",shotReferences:"逐镜角色、道具与参考图",rhythm:"镜头充分性与节奏"};
  if (list.error) return <div className="error">{list.error}</div>;
  if (!list.data?.length) return null;
  return <details className="card"><summary>核对历史集资料（{list.data.length} 集）</summary>
    <p>导入本集清单、分段 TXT、登记素材、固定身份图及审核依据。每次核对都使用导入时保存的副本。</p>
    <label>历史集 <select aria-label="历史集" value={folder} onChange={e=>{setFolder(e.target.value);setSelectedReceipt("");setImported(null);}}><option value="">选择历史集</option>
      {list.data.map(row=><option key={row.folder} value={row.folder}>{row.folder}{row.available?"":" · 原目录离线"}</option>)}</select></label>
    <label><input type="checkbox" checked={includeImages} onChange={e=>setIncludeImages(e.target.checked)}/>同时盘点并保存本集候选原图</label>
    <button className="btn" disabled={!source?.available || action.busy} onClick={()=>action.run(async()=>{
      const r=await cmd<Receipt>("import_historical_episode",{project_id:pid,folder,include_image_inventory:includeImages});setImported(r);setSelectedReceipt(r.receipt);await list.reload();
    })}>导入副本并核对</button>
    {!!source?.versions.length&&<label>核对版本 <select value={saved||""} onChange={e=>setSelectedReceipt(e.target.value)}>
      {source.versions.map((r,i)=><option key={r.id} value={r.id}>{new Date(r.createdAt*1000).toLocaleString()} · {r.id.slice(0,8)}{i===0?" · 最近导入":""}</option>)}
    </select></label>}
    {record && <><ul>{Object.entries(record.rechecked??record.checks).map(([key,check])=><li key={key}><strong>{names[key]}</strong> · {check.message}</li>)}</ul>
      {record.archive ? <><h3>材料逐项核对</h3><ul>{Object.entries(record.materialRechecked??record.materialChecks??{}).map(([key,result])=>
        <li key={key}><strong>{names[key]??key}</strong> · {({advisory:"有待核对项",checked:"已核对",passed:"通过",failed:"未通过",incomplete:"待核对",not_declared:"未登记"} as Record<string,string>)[result.state]} · {result.message}
          {key==="pageContent" && !!result.details && <HistoricalPageEvidence report={result.details as PageEvidence}/>}
          {key==="imageInventory" && !!result.details && <InventoryImages key={record.receipt} report={result.details as InventoryReport}/>}
          {key==="promptSignals" && !!result.details && <details><summary>查看通读位置与原文</summary><SignalList report={result.details as SignalReport}/></details>}
          {key==="h3Sound" && !!result.details && <details><summary>查看逐段声音声明</summary><ul>
            {(result.details as {segments:{segment:string;nativeBgm:unknown;fullSilence:unknown;state:string;message?:string;sha256?:string}[]}).segments.map((s,i)=>
              <li key={i}>{s.segment} · 配乐 {String(s.nativeBgm)} · 全静音 {String(s.fullSilence)} · {s.message??"通过"} {s.sha256&&<code>{s.sha256}</code>}</li>)}</ul></details>}
          {key==="aspect" && result.state==="passed" && <GridMeasurements details={result.details as FrameMeasurements}/>}
          {!!result.warnings?.length && <details><summary>查看逐项提示（{result.warnings.length}）</summary><ul>{result.warnings.map((w,i)=>
            <li key={i}>{[w.scene_id,w.asset_id,...(w.asset_ids || []),typeof w.request_id==="string"?w.request_id:""].filter(Boolean).join(" · ")} {w.message}</li>)}</ul></details>}
        </li>)}</ul>
        {record.archive.referenceReports?.map((report,index)=>{
          const base = "/api/history-report/"+encodeURIComponent(pid)+"/"+encodeURIComponent(record.receipt)+"/"+index+"/";
          return <div key={report.source}><h3>逐镜报告 · {report.source.split("/").slice(-2,-1)}</h3>
            {report.error ? <p className="error">{report.error}</p> : <>
              <a href={base+"preview"} target="_blank" rel="noreferrer">打开逐镜报告</a> · <a href={base+"report.zip"} download>下载原版报告与配图</a>
              <p>已核对 {Object.keys(report.resources).filter(n=>n.startsWith("frames/")||n.startsWith("evidence/")).length} 张抽帧与证据图。</p>
              {!!report.missing.length && <details><summary>未能核对的文件（{report.missing.length}）</summary>{report.missing.join("、")}</details>}
            </>}</div>;
        })}
        {saved && <SceneContact key={saved} pid={pid} receipt={saved}/>}
        <button className="btn" onClick={()=>setOpenedInputs(openedInputs===saved?"":saved || "")}>{openedInputs===saved?"收起":"查看"}待审输入清单</button>
        {inputs.data && <pre style={{whiteSpace:"pre-wrap"}}>{inputs.data.record}</pre>}
        {inputs.error && <p className="error">{inputs.error}</p>}
        {record.integrity&&<p>已核对 {record.integrity.checked} 个独立文件指纹 · {record.integrity.ok?"与副本一致":"存在缺失或修改"}</p>}
        {record.integrity?.issues.map(i=><p className="error" key={i.sha256}>{i.sha256} · {i.problem}</p>)}
        {record.archive.fixedError&&<p className="error">{record.archive.fixedError}</p>}
        {!!Object.keys(record.archive.unavailable).length&&<details><summary>缺失或越界的引用</summary>{Object.entries(record.archive.unavailable).map(([name,issue])=><p key={name}>{name} · {issue}</p>)}</details>}
        <details><summary>导入时的项目基准</summary>{Object.entries(record.archive.fixed).map(([key,r])=><p key={key}>{key} · <code>{r.sha256}</code></p>)}
        {Object.entries(record.archive.standards).map(([key,r])=><p key={key}>{r.path} · <code>{r.sha256}</code></p>)}</details>
      </>:<p>此记录只保存了早期文本副本。重新导入可核对登记素材与审核依据。</p>}
      <details><summary>已复制的文件与指纹（{Object.keys(record.files).length}）</summary>{Object.entries(record.files).map(([name,sha])=><p key={name}>{name} · <code>{sha}</code></p>)}</details></>}
    {previous.error&&<p className="error">{previous.error}</p>}
    <ErrorLine error={action.error}/>
  </details>;
}
