import { useEffect, useRef, useState } from "react";
import { cmd } from "./api";
import { ErrorLine, PROVIDER_LABEL } from "./ui";

type Check = { key:string; verdict:"compatible"|"uncertain"|"conflict"; settingQuote:string; requestQuote:string; reason:string; suggested?:{settingKey:string;requestQuote:string} };
type View = { basis:string; state:string; settings:Record<string,string>; request:string; provider:string; model:string;
  checks:Check[]; decisions:Record<string,{conflict:boolean; requestQuote:string}>; blocked:boolean; error:string };
type Props = { source:Record<string,unknown>; provider:string; model:string|null; effort:string|null; onBlock:(b:boolean)=>void };

export function KickoffCheck({source, provider, model, effort, onBlock}:Props) {
  const [view, setView] = useState<View|null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [key, setKey] = useState("");
  const [quote, setQuote] = useState("");
  const live = useRef(false);
  const sourceJSON = JSON.stringify(source);
  function display(next:View) {
    setView(next);
    const suggestion=next.checks.find(c=>c.suggested && !next.decisions[c.key])?.suggested;
    if(suggestion && (!key || view?.basis!==next.basis)){setKey(suggestion.settingKey);setQuote(suggestion.requestQuote);}
    onBlock(next.blocked || next.state === "running");
  }
  useEffect(() => {
    live.current = true;
    const timer = setTimeout(() => cmd<View>("kickoff_check_view", {source:JSON.parse(sourceJSON)})
      .then(next=>{if(live.current) display(next);}).catch(e=>{if(live.current) setError(e.message);}), 200);
    return () => {live.current=false; clearTimeout(timer); onBlock(false);};
  }, [sourceJSON]);
  useEffect(() => {
    if (view?.state !== "running" || busy) return;
    const timer = setTimeout(async () => {
      try { const next = await cmd<View>("kickoff_check_view",{source}); if(live.current) display(next); }
      catch(e:any) {if(live.current) setError(e.message);}
    }, 1200);
    return () => clearTimeout(timer);
  },[view,busy]);
  async function act(name:string,args:Record<string,unknown>) {
    if (!view || busy) return;
    setBusy(true); onBlock(true); setError("");
    try {
      const next = await cmd<View>(name,{source,basis:view.basis,...args});
      if(live.current) {display(next);}
    } catch(e:any) {if(live.current) {setError(e.message); onBlock(view.blocked || view.state === "running");}}
    finally {if(live.current) setBusy(false);}
  }
  return <section className="card col" aria-label="开工设定核对">
    <strong>必守设定与本集要求</strong>
    <ErrorLine error={error?{message:error}:null}/>
    {view && <>
      {Object.entries(view.settings).map(([k,v])=><div key={k}>{v}</div>)}
      <details><summary>查看本集要求原文</summary><p style={{whiteSpace:"pre-wrap"}}>{view.request}</p></details>
      {view.state === "done" ? <small>已用 {PROVIDER_LABEL[view.provider] || view.provider} {view.model} 检查</small>
        : <button type="button" className="btn" disabled={busy || view.state==="running"} onClick={()=>act("kickoff_check_run",{provider,model,effort})}>
        {busy || view.state==="running" ? "正在处理…" : "用当前 CLI 检查冲突"} · {PROVIDER_LABEL[provider] || provider}（使用额度）</button>}
      <ErrorLine error={view.error?{message:view.error}:null}/>
      {view.checks.map(c=><div key={c.key}>
        <strong>{{conflict:"发现冲突",uncertain:"需导演判断",compatible:"可同时满足"}[c.verdict]}</strong> · {c.reason}
        <div className="small">{c.settingQuote || view.settings[c.key]}{c.requestQuote && <> ／ {c.requestQuote}</>}</div>
        {c.verdict==="conflict" && view.decisions[c.key]?.requestQuote!==c.requestQuote && <button className="pill" disabled={busy} onClick={()=>act("kickoff_check_decide",{key:c.key,request_quote:c.requestQuote,conflict:false})}>我已核对，两句可同时满足</button>}
      </div>)}
      {Object.entries(view.decisions).map(([k,d])=><div key={k}>导演已核对 · {d.conflict?"存在冲突":"可同时满足"}<div className="small">{view.settings[k]} ／ {d.requestQuote}</div>
        {d.conflict && <button className="pill" disabled={busy} onClick={()=>act("kickoff_check_decide",{key:k,request_quote:d.requestQuote,conflict:false})}>更正为可同时满足</button>}</div>)}
      <details><summary>手动标记冲突</summary><div className="col">
        <select aria-label="选择冲突的必守设定" value={key} onChange={e=>setKey(e.target.value)}><option value="">选择必守设定</option>
          {Object.entries(view.settings).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select>
        <textarea placeholder="粘贴与设定冲突的本集原句" value={quote} onChange={e=>setQuote(e.target.value)}/>
        <button className="btn" disabled={busy || !key || !quote.trim() || !view.request.includes(quote)} onClick={()=>act("kickoff_check_decide",{key,request_quote:quote,conflict:true})}>确认两句冲突</button>
      </div></details>
      {view.blocked && <p role="status">请修改本集要求，或核对冲突引文后再开工。</p>}
    </>}
  </section>;
}
