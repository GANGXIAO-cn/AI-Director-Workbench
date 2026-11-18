import {useEffect,useState,useRef} from 'react';
import {cmd} from './api';
import {ModelPick} from './Project';
import {useQuery,useAction,ErrorLine} from './ui';

export const INITIAL_PLAN={create:{provider:'codex',model:null,effort:null},review:{provider:'codex',model:null,effort:null},expert:{provider:'codex',model:null,effort:null}};
export function useExecutorPlan(initial?:any) {
  const query=useQuery<any>('executor_defaults');
  const [plan,setPlan]=useState<any>(initial || INITIAL_PLAN);
  const initialized=useRef(!!initial);
  useEffect(()=>{if(query.data&&!initialized.current){initialized.current=true;setPlan(query.data);}},[query.data]);
  return {plan,setPlan,error:query.error};
}
function ConnectionStatus({provider}:{provider:string}) {
  const identity=useQuery<any>('executor_identity',{provider});
  const v=identity.data;
  return <small>{identity.error || (!v?'正在读取连接状态…':v.unavailable?'不可用':v.simulated?'模拟执行器':v.authenticated===true?'已登录':v.authenticated===false?'未登录':v.lastVerifiedAccount?'上次验证已登录':'尚未验证登录')}</small>;
}
export function ExecutorPlanFields({plan,onChange,saveDefault=true}:{plan:any;onChange:(plan:any)=>void;saveDefault?:boolean}) {
  const catalog=useQuery<any>('model_catalog');
  const act=useAction();const [saved,setSaved]=useState(false);
  const change=(role:string,slot:any)=>{setSaved(false);onChange({...plan,[role]:slot});};
  return <div className="col">{(['create','review','expert'] as const).map(role=>{
    const slot=plan[role] || plan.create;
    return <div key={role}><ModelPick label={{create:'起草',review:'独审',expert:'专家'}[role]} provider={slot.provider} sel={slot} catalog={catalog.data}
      setProvider={(provider:string)=>change(role,{provider,model:null,effort:null})} setSel={(value:any)=>change(role,{...slot,...value})}/><ConnectionStatus provider={slot.provider}/></div>;
  })}{saveDefault&&<button className="btn" disabled={act.busy} onClick={()=>act.run(async()=>{await cmd('save_executor_defaults',{plan});setSaved(true);})}>{saved?'已设为默认':'设为默认'}</button>}<ErrorLine error={act.error}/>{catalog.error&&<p className="error">{catalog.error}</p>}</div>;
}
export function ExecutorDefaults() {
  const {plan,setPlan,error}=useExecutorPlan();
  return <section className="card"><h2>默认分工</h2><p className="muted small">用于新开工；本集设置可以单独调整。</p><ExecutorPlanFields plan={plan} onChange={setPlan}/>{error&&<p className="error">{error}</p>}</section>;
}
