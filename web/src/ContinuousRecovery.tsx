import { PROVIDER_LABEL } from "./ui";

export function recoveryText(block:any, count:number) {
  if(block.kind==='auth') {
    const who=PROVIDER_LABEL[block.provider]??block.provider??'执行器';
    return `${['image_assess','image'].includes(block.role)?'重新登录 Codex':'重新登录 '+who+' 或换执行器'}后会自动继续（已自动再试 ${count} 次）`;
  }
  if(block.kind==='quota')return block.resetsAt
    ?`额度预计 ${new Date(block.resetsAt*1000).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})} 恢复，到时将自动再试（已尝试 ${count} 次）`
    :`额度恢复时间未知，每 30 分钟将自动再试一次（已尝试 ${count} 次）`;
  if(block.kind==='transient')return `网络或超时问题，等待 30 秒后，在下一次检查时自动再试（已尝试 ${count} 次）`;
  return '图片请求处理完后会自动继续';
}

export function ContinuousRecoveryNote({run,needKey,all=false}:{run:any;needKey?:string;all?:boolean}) {
  if(run?.state!=='waiting_external')return null;
  const blocks=(run.cursor?.blocks??[]).filter((b:any)=>all || (needKey?b.scope==='need:'+needKey:!b.scope.startsWith('need:')));
  return <>{blocks.map((b:any)=><p className="muted small" key={b.id}>{recoveryText(b,run.cursor?.wakeCounts?.[b.id]??0)}</p>)}</>;
}
