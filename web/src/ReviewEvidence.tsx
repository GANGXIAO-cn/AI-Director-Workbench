import {createContext, useContext, type ReactNode} from "react";
import {useDisplay} from "./DisplayPreferences";
const fields: Record<string,string> = {
  synopsis:"故事梗概", cast:"本集角色表", timeline:"时间线", props:"规则与道具表",
  causes:"因果核对表", spine:"故事脊", handoff:"交接说明", notes:"开发备注", adaptation:"改编依据",
};

type Passage = {field:string;label:string;text:string;quotes:string[]};
export type StoryLocation = {versionId:string;passages:Passage[]};
export const LocateStory = createContext<((location:StoryLocation|null)=>void)|null>(null);

function passagesFor(issue:any,content:Record<string,unknown>):Passage[] {
  const {anchor="",evidence=""}=issue.loc;
  const quotes=[...new Set(Array.from((anchor+" "+evidence).matchAll(/[“「]([^”」]+)[”」]/g),m=>m[1]))];
  const numbers=anchor.match(/(?:故事梗概|正文)第([\d、，,—–至到-]+)段/)?.[1] ?? "";
  const ranges=numbers.split(/[、，,]/).filter(Boolean).map((part:string)=>part.split(/[—–至到-]/).map(Number));
  return Object.entries(fields).flatMap(([field,label])=>{
    const value=content[field];
    if(typeof value!=="string") return [];
    let paragraph=0;
    return value.split(/\n+/).filter(line=>line.trim()).flatMap(line=>{
      if(field==="synopsis" && (paragraph>0 || !/^(#|[（(])/.test(line.trim()))) paragraph++;
      const selected=field==="synopsis" && ranges.some(([first,last=first]:number[])=>paragraph>=first && paragraph<=last);
      const found=quotes.filter(q=>line.includes(q));
      if(!selected && !found.length && !(line.trim().length>3 && evidence.includes(line.trim()))) return [];
      return [{field,label,text:line,quotes:found}];
    });
  });
}

export function MarkPassage({text,passage}:{text:string;passage?:Passage}) {
  if(!passage) return <>{text}</>;
  if(!passage.quotes.length) return <mark>{text}</mark>;
  const spans=passage.quotes.flatMap(q=>{
    const hits:number[]=[];
    for(let at=text.indexOf(q);at>=0;at=text.indexOf(q,at+q.length)) hits.push(at);
    return hits.map(start=>({start,end:start+q.length}));
  }).sort((a,b)=>a.start-b.start);
  const parts:ReactNode[]=[];
  let cursor=0;
  for(const {start,end} of spans){
    if(end<=cursor) continue;
    parts.push(text.slice(cursor,start),<mark key={end}>{text.slice(Math.max(cursor,start),end)}</mark>);
    cursor=end;
  }
  return <>{parts}{text.slice(cursor)}</>;
}

export function ChatReviewIssue({issue,version,children}:{issue:any;version:any;children:ReactNode}) {
  const locate=useContext(LocateStory);
  const {focusChat,setFocusChat}=useDisplay();
  const passages=passagesFor(issue,version.content);
  const show=()=>locate?.({versionId:version.id,passages});
  return <div className="chat-issue" onMouseEnter={()=>{if(passages.length && !focusChat)show();}} onMouseLeave={()=>locate?.(null)}
    onFocus={()=>{if(passages.length && !focusChat)show();}} onBlur={e=>{if(!e.currentTarget.contains(e.relatedTarget))locate?.(null);}}>
    <strong>{issue.blocking?"必须修正":"普通意见"}</strong><p>{issue.reason}</p>
    <ReviewEvidence issue={issue}/>
    {locate && passages.length>0 && <button className="locate-passage" onClick={()=>{setFocusChat(false);show();}}>在正文中定位</button>}
    {children}
  </div>;
}

export function ReviewEvidence({issue,content}:{issue:any;content?:Record<string,unknown>}) {
  const {anchor,evidence}=issue.loc;
  const matches=content?passagesFor(issue,content):[];
  return <div className="review-evidence">
    <div><b>问题位置</b>　{anchor}</div>
    {evidence && <p><b>审查依据</b>　{evidence}</p>}
    {issue.setting_content && <p><b>设定原文</b>　{issue.setting_content}</p>}
    {matches.length>0 && <details><summary>查看对应稿件原文（{matches.length}处）</summary>
      {matches.map((match,index)=><blockquote key={index}><b>{match.label}</b><div>{match.text}</div></blockquote>)}
    </details>}
  </div>;
}
