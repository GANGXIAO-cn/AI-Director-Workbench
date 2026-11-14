import { useEffect, useState } from "react";
import { cmd } from "./api";
import { ErrorLine, useAction } from "./ui";

export function ImageAPISettings({ provider, settings, reload }: { provider:string; settings:any; reload:()=>Promise<any> }) {
  const [limit,setLimit]=useState(String(settings.maxConcurrency));
  const [saved,setSaved]=useState(false);
  const act=useAction();
  useEffect(()=>setLimit(String(settings.maxConcurrency)),[settings.maxConcurrency]);
  const value=Number(limit);
  return <section className="card">
    <h2>OpenAI 图片 API · 最大并发</h2>
    <p>密钥：{settings.keyConfigured?"已配置，复用本机 MCP 凭证":"未配置"} · 当前请求 {settings.activeRequests} 个</p>
    <label className="formrow"><span>同时出图上限</span><input aria-label="图片 API 最大并发" type="number" min={1} max={64} step={1}
      value={limit} onChange={e=>{setLimit(e.target.value);setSaved(false);}}/></label>
    <p className="muted small">同一工作台服务的所有 API 图片批次共用此上限（1–64）。调低后，已开始的请求会完成，后续请求等待空位；调高后的批内并行数从下一次执行批次生效。</p>
    <button className="btn primary" disabled={act.busy || !Number.isInteger(value) || value<1 || value>64}
      onClick={()=>act.run(async()=>{await cmd("save_image_provider_settings",{provider,max_concurrency:value});await reload();setSaved(true);})}>保存并发设置</button>
    {saved && <span role="status"> 已保存</span>}
    <ErrorLine error={act.error}/>
  </section>;
}
