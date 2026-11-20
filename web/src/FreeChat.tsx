import { ModelPick } from "./Project";
import { ChatMarkdown } from "./ChatMarkdown";
import { ModelReply } from "./ModelReply";
import { useEffect, useRef, useState } from "react";
import { cmd, commandId, upload } from "./api";
import { useProjectRoute } from "./projectNavigation";
import { ErrorLine, useAction, usePoll, useQuery } from "./ui";

type Turn = {id:string;text:string;reply:string|null;status:string;reason:string|null;provider:string;model?:string;effort?:string;attempt:string;
  methods:string[];sources:{role:string;name:string;sha256:string}[]};
type Transcript = {threads:{id:string;title:string}[];turns:Turn[]};
type Method = {id:string;title:string;versions:{id:string;number:number}[]};
type Options = {assets:{id:string;name:string;version_no:number}[];providers:string[];canvases:{id:string;display_code:string;revision:number}[]};
const states:Record<string,string> = {queued:"排队中",running:"等待执行器回复",succeeded:"已回复",failed:"失败",waiting_quota:"等待额度",stopped:"已停止"};

export function FreeChat({pid,thread}:{pid:string;thread?:string}) {
  const route = useProjectRoute();
  const transcript = useQuery<Transcript>("free_chat",{project_id:pid,thread_id:thread});
  const options = useQuery<Options>("free_chat_options",{project_id:pid});
  const library = useQuery<{methods:Method[]}>("custom_methods",{project_id:pid});
  const methods = library.data?.methods ?? [];
  const [chosen,setChosen] = useState<string[]|null>(null);
  const modelCatalog=useQuery<any>("model_catalog");
  const [chosenModel,setSelection]=useState<{model:string|null;effort:string|null}|null>(null);
  const [provider,setProvider] = useState("");
  const [selectedImages,setImages] = useState<string[]|null>(null);
  const requested = new URLSearchParams(route.hash.split("?")[1]).getAll("image");
  const available = options.data?.assets ?? [];
  const images = selectedImages ?? [...new Set(requested)].filter(id=>available.some(a=>a.id===id)).slice(0,6);
  const [canvas,setCanvas] = useState("");
  const [target,setTarget] = useState("");
  const [uploadTitle,setUploadTitle] = useState("");
  const [newFile,setNewFile] = useState<File|null>(null);
  const [fileKey,setFileKey] = useState(0);
  const [uploadMethod,setUploadMethod] = useState("");
  const [notice,setNotice] = useState("");
  const draftKey = "free-draft:"+pid+":"+(thread || "new");
  const [text,setText] = useState(()=>sessionStorage.getItem(draftKey) || "");
  const action = useAction();
  const submission = useRef<{payload:string;id:string}|null>(null);
  const turns = transcript.data?.turns ?? [];
  const last = turns.at(-1);
  const selection = chosenModel ?? {model:last?.model??null,effort:last?.effort??null};
  const selected = chosen ?? last?.methods ?? [];
  const executor = provider || last?.provider || (options.data?.providers.includes("codex")?"codex":options.data?.providers[0]) || "";
  const pending = last?.status==="queued" || last?.status==="running";
  const canvases = options.data?.canvases ?? [];
  const stream=useRef<HTMLDivElement>(null),follow=useRef(true);
  useEffect(()=>{const el=stream.current;if(el&&follow.current)el.scrollTop=el.scrollHeight;},[last?.id,last?.status,last?.reply]);
  usePoll(pending,transcript.reload,750);
  function choose(method:Method,version:string) {
    const next = selected.filter(v=>!method.versions.some(r=>r.id===v));
    setChosen(version?[...next,version]:next);
  }
  async function send() {
    follow.current=true;
    const chosenCanvas = canvases.find(c=>c.id===canvas);
    const payload = {project_id:pid,thread_id:thread,previous:last?.id,text,provider:executor,...selection,
      images,methods:selected,canvas:chosenCanvas?.id,canvas_revision:chosenCanvas?.revision};
    const serialized = JSON.stringify(payload);
    if (submission.current?.payload !== serialized) submission.current = {payload:serialized,id:commandId()};
    const r = await cmd("send_free_chat",{...payload,command_id:submission.current.id});
    submission.current = null;
    setText("");sessionStorage.removeItem(draftKey);setImages([]);setCanvas("");
    if(!thread)route.go("#/p/"+pid+"/free/"+r.threadId);
    else await transcript.reload();
  }
  async function addSkill() {
    if(!newFile)return;
    const old = methods.find(m=>m.id===uploadMethod);
    const result = await upload("custom-method",newFile,{projectId:pid,title:uploadTitle,methodId:old?.id,expectedRevision:old?.versions[0].id}) as {versionId:string;number:number};
    setChosen([...selected.filter(id=>!old?.versions.some(v=>v.id===id)),result.versionId]);
    setNewFile(null);setFileKey(key=>key+1);setUploadTitle("");await library.reload();
    setNotice("已选用上传的 R"+result.number+" 版");
  }
  async function control(name:string,t:Turn) {
    try {await cmd(name,{project_id:pid,turn_id:t.id,expected_attempt:t.attempt});} finally {await transcript.reload();}
  }
  return <section className="free-chat"><div className="spread"><h1>自由对话</h1><span className="muted small">项目对话</span></div>
    <div className="free-stream" ref={stream} onScroll={e=>{const el=e.currentTarget;follow.current=el.scrollHeight-el.scrollTop-el.clientHeight<60;}} role="log" aria-label="自由对话消息">{turns.map(t=><article className="free-turn" key={t.id}><div className="msg user chat-prose">{t.text}</div><div className="free-reply" data-chat-kind={["failed","waiting_quota","stopped"].includes(t.status)?"action":["queued","running"].includes(t.status)?"progress":undefined}>
      <ModelReply provider={t.provider} model={t.model} effort={t.effort}><span className="tiny muted">{states[t.status]}</span>{t.reply && <ChatMarkdown text={t.reply}/>}{t.reason && <p>{t.reason}</p>}
      {t.sources.length>0 && <details><summary>本次引用（{t.sources.length}）</summary>{t.sources.map(s=><p key={s.role+s.name+s.sha256}>{s.name} · <code>{s.sha256}</code></p>)}</details>}
      {t===last && (pending || ["failed","waiting_quota","stopped"].includes(t.status)) &&
        <button className="btn" disabled={action.busy} onClick={()=>action.run(()=>control(pending?"stop_free_chat":"retry_free_chat",t))}>{pending?"停止本次请求":"按原输入重试"}</button>}
      {t.reply && canvases.length>0 && <details><summary>将回复放到画布</summary><label>目标集 <select value={target} onChange={e=>setTarget(e.target.value)}>
        <option value="">选择画布</option>{canvases.map(c=><option key={c.id} value={c.id}>{c.display_code}</option>)}</select></label>
        <button className="btn" disabled={!target||action.busy} onClick={()=>action.run(async()=>{
          try {await cmd("add_reply_to_canvas",{project_id:pid,turn_id:t.id,episode_id:target,expected_revision:canvases.find(c=>c.id===target)?.revision});
            setNotice("回复已写入目标画布的文本节点");} finally {await options.reload();}
        })}>添加文本节点</button>{target && <a className="btn" href={"#/e/"+target+"/assets?tab=canvas"}>打开目标画布</a>}</details>}
    </ModelReply></div></article>)}</div>
    <div className="free-composer"><details className="free-tools"><summary>更多工具</summary><div className="row">
    <select aria-label="历史自由对话" value={thread||""} onChange={e=>route.go("#/p/"+pid+"/free"+(e.target.value?"/"+e.target.value:""))}>
      <option value="">新对话</option>{transcript.data?.threads.map(t=><option key={t.id} value={t.id}>{t.title}</option>)}</select>
    {thread && <a className="btn" href={"#/p/"+pid+"/free"}>新对话</a>}</div><ModelPick label="本次执行器" catalog={modelCatalog.data} provider={executor} sel={selection} setSel={setSelection} setProvider={(p:string)=>{setProvider(p);setSelection({model:modelCatalog.data?.text[p]?.models[0]?.id??null,effort:null});}}/>
      <details><summary>创作技能（{selected.length}）</summary>{methods.map(m=><label key={m.id}>{m.title} <select aria-label={m.title+"对话版本"}
        value={selected.find(v=>m.versions.some(r=>r.id===v))||""} onChange={e=>choose(m,e.target.value)}><option value="">不使用</option>
        {m.versions.map(v=><option key={v.id} value={v.id}>R{v.number}</option>)}</select></label>)}
        <div className="row"><label>上传到 <select value={uploadMethod} onChange={e=>setUploadMethod(e.target.value)}><option value="">新技能</option>{methods.map(m=><option key={m.id} value={m.id}>{m.title}</option>)}</select></label>
          {!uploadMethod && <label>技能名称 <input value={uploadTitle} maxLength={100} onChange={e=>setUploadTitle(e.target.value)}/></label>}
          <input key={fileKey} type="file" accept=".md" aria-label="自由对话技能文件" onChange={e=>setNewFile(e.target.files?.[0]??null)}/>
          <button className="btn" disabled={!newFile||(!uploadMethod&&!uploadTitle.trim())||action.busy} onClick={()=>action.run(addSkill)}>上传并使用 SKILL.md</button></div></details>
      {!!options.data?.assets.length && <details><summary>附项目原图（{images.length}/6）</summary><select aria-label="附项目原图" multiple value={images}
        onChange={e=>setImages(Array.from(e.target.selectedOptions,o=>o.value).slice(0,6))}>{options.data.assets.map(a=><option key={a.id} value={a.id}>{a.name} · R{a.version_no}</option>)}</select></details>}
      {images.length>0 && <p className="small">本次附图 · {images.map(id=>{
        const image=available.find(a=>a.id===id);return image ? image.name+" v"+image.version_no : "";
      }).join("、")}</p>}
      {canvases.length>0 && <label>引用画布 <select value={canvas} onChange={e=>setCanvas(e.target.value)}><option value="">不附画布</option>{canvases.map(c=><option key={c.id} value={c.id}>{c.display_code} · R{c.revision}</option>)}</select></label>}
      </details><textarea disabled={action.busy} aria-label="对话内容" placeholder="聊一个灵感，或贴一段需要修改的文字" value={text} onChange={e=>{setText(e.target.value);sessionStorage.setItem(draftKey,e.target.value);}}/>
      <button className="btn primary" disabled={!text.trim()||!executor||pending||action.busy||!transcript.data||!options.data} onClick={()=>action.run(async()=>{try {await send();} catch(e){await Promise.all([transcript.reload(),options.reload()]);throw e;}})}>发送</button>
    </div>{notice && <p role="status">{notice}</p>}<ErrorLine error={action.error}/>{[transcript.error,library.error,options.error].filter(Boolean).map(e=><p className="error" key={e}>{e}</p>)}
  </section>;
}
