
const F={"createdNodeIds":["33:296","33:297","33:298","33:299","33:328","33:329","33:330","33:331","33:332","33:333","33:334","33:335","33:336","33:337","I33:337;4185:3781"],"pages":{"02":"33:296","03":"33:297","04":"33:298","05":"33:299"},"variables":{"canvas":"VariableID:33:303","panel":"VariableID:33:305","card":"VariableID:33:307","elevated":"VariableID:33:309","text":"VariableID:33:311","muted":"VariableID:33:313","accent":"VariableID:33:315","selected":"VariableID:33:317","positive":"VariableID:33:319","warning":"VariableID:33:321","danger":"VariableID:33:323"},"styles":{"body":"S:ad3c56d04a65084189812dc78aa1cede9c273155,","chat":"S:4d5cf34907ac399c41c1a766768f62b4f128368f,","small":"S:071b38655497b27f5b2d861728f5242c7ec990fb,","title":"S:508a6868254dd80ca71eb05a3a14930c3a33225d,"},"components":{"Message":"33:328","Scene":"33:331","Stage":"33:334"},"collections":["VariableCollectionId:33:300","VariableCollectionId:33:301"],"buttonSource":"33:337"};
await figma.setCurrentPageAsync(await figma.getNodeByIdAsync(F.pages["04"]));
await figma.loadFontAsync({family:"Noto Sans SC",style:"Regular"});
await figma.loadFontAsync({family:"Noto Sans SC",style:"Medium"});
const V={};for(const [k,id] of Object.entries(F.variables)) V[k]=await figma.variables.getVariableByIdAsync(id);
const C={};for(const [k,id] of Object.entries(F.components)) C[k]=await figma.getNodeByIdAsync(id);
const B=await figma.importComponentSetByKeyAsync("cc8b558dc7d9684011b6b99ce8e6509399bc836b");
const BC=B.children.find(n=>n.name==="Variant=Primary, State=Default, Size=Medium");
for(const t of BC.findAll(n=>n.type==="TEXT"))for(const seg of t.getStyledTextSegments(["fontName"])) await figma.loadFontAsync(seg.fontName);
const created=[],roots=[],links=[];
const scripts={
r03:"林夏攥着船票站在检票口前，另一只手抱着父亲留下的旧相机。\n\n检票员：末班船了，走不走？\n林夏：我怕走了，就再也回不来了。\n检票员：想回来，就回来。\n\n林夏低头看相机，沉默片刻，把船票递过去。",
r04:"检票口的灯由绿转黄。林夏抬起船票，目光却落在旧相机磨损的背带上。\n\n她把相机贴近胸口，拇指慢慢擦过机身。船票从指间滑落，她弯腰拾起，回望空荡的候船厅。\n\n渡轮汽笛响起。她收紧背带，向前一步，将船票递给检票员。",
r05:"检票口的灯由绿转黄。林夏右手捏着船票，左手握着父亲留下的旧相机。\n\n她停在闸口前，把左手的相机贴近胸口，拇指擦过磨损的机身；右手始终捏住船票。她回望空荡的候船厅。\n\n渡轮汽笛响起。她让相机垂回颈前，左手扶住机身，向前一步，用右手将船票递给检票员。",
manual:"林夏攥着船票站在检票口前，另一只手抱着父亲留下的旧相机。\n\n检票员把闸口打开，留出一步宽的通道。\n\n检票员：末班船了，走不走？\n林夏：我怕走了，就再也回不来了。\n\n林夏低头看相机，沉默片刻，把船票递过去。",
r07:"检票口的灯由绿转黄。林夏捏着船票，把旧相机贴近胸口。她回望空荡的候船厅。\n\n渡轮汽笛响起。她收起相机，向前一步，将船票递给检票员。"
};
const HEX={canvas:"0B1018",panel:"101722",card:"172131",elevated:"202E42",text:"E8EDF5",muted:"98A8BC",accent:"91B8FF",selected:"233F63",positive:"8DDEC3",warning:"F2C38B",danger:"FFACB0"};
function paint(n,k){const h=HEX[k];n.fills=[figma.variables.setBoundVariableForPaint({type:"SOLID",color:{r:parseInt(h.slice(0,2),16)/255,g:parseInt(h.slice(2,4),16)/255,b:parseInt(h.slice(4,6),16)/255}},"color",V[k])];}
function box(p,name,w,h,dir="VERTICAL",bg=null,pad=0,gap=0){
 const n=figma.createAutoLayout(dir);n.name=name;n.resize(w,h||80);n.paddingTop=n.paddingBottom=n.paddingLeft=n.paddingRight=pad;n.itemSpacing=gap;n.fills=[];if(p)p.appendChild(n);n.layoutSizingHorizontal="FIXED";n.layoutSizingVertical=h?"FIXED":"HUG";if(bg)paint(n,bg);created.push(n.id);return n;
}
function txt(p,name,s,w,size=14,color="text"){
 const n=figma.createText();n.name=name;n.fontName={family:"Noto Sans SC",style:"Regular"};n.fontSize=size;n.lineHeight={unit:"PIXELS",value:Math.round(size*1.65)};n.characters=s;p.appendChild(n);n.resize(w,28);n.textAutoResize="HEIGHT";n.layoutSizingVertical="HUG";paint(n,color);created.push(n.id);return n;
}
function instance(p,key,w,values,bg){
 const n=C[key].createInstance();p.appendChild(n);n.resize(w,100);n.layoutSizingVertical="HUG";
 if(bg)paint(n,bg);
 for(const t of n.findAll(x=>x.type==="TEXT")) {if(values[t.name]!==undefined)t.characters=values[t.name];t.resize(w-n.paddingLeft-n.paddingRight,30);t.textAutoResize="HEIGHT";t.layoutSizingVertical="HUG";}
 return n;
}
function button(p,label,target,kind="secondary",width){
 const n=BC.createInstance();p.appendChild(n);n.name="action / "+label;
 n.setProperties({"Label#2:0":label,"Has Icon Start#4:128":false,"Has Icon End#4:64":false});
 n.resize(width||Math.max(72,Math.min(316,label.length*14+28)),36);n.layoutSizingHorizontal="FIXED";n.layoutSizingVertical="FIXED";n.cornerRadius=7;n.strokes=[];
 paint(n,kind==="primary"?"accent":kind==="disabled"?"panel":"elevated");
 for(const t of n.findAll(x=>x.type==="TEXT")){t.fontName={family:"Noto Sans SC",style:"Regular"};t.fontSize=13;paint(t,kind==="primary"?"canvas":kind==="disabled"?"muted":"text");}
 if(target)links.push({node:n.id,target,label});
 return n;
}
function note(p,title,body,w,color="accent"){
 const a=box(p,title,w,null,"VERTICAL","card",16,6);a.cornerRadius=10;txt(a,"title",title,w-32,15,color);txt(a,"detail",body,w-32,14,"muted");return a;
}
function scene(p,title,body,w,selected,target){
 const n=instance(p,"Scene",w,{heading:title,body},selected?"selected":"panel");
 if(target)links.push({node:n.id,target,label:"选择场02"});return n;
}
function build(s,index,W=1440,H=960){
 const narrow=s.compare, L=narrow?56:200,R=narrow?56:380,M=W-L-R;
 const root=box(null,"R2 / "+s.id+" / "+s.name,W,H,"VERTICAL","canvas");root.x=160+(index%4)*(W+160);root.y=160+Math.floor(index/4)*(H+180);root.clipsContent=true;
 const head=box(root,"顶栏",W,56,"HORIZONTAL","panel",16,12);
 txt(head,"brand","AI 导演工作台",184,15);
 txt(head,"project","末班渡轮  /  第01集",Math.max(230,W-854),14);
 txt(head,"save","已保存到本地 · 示例",180,12,"muted");
 txt(head,"task",s.status,280,12,s.running?"accent":"muted");
 const main=box(root,"工作区",W,H-80,"HORIZONTAL",null,0,0);
 const nav=box(main,"阶段导航",L,H-80,"VERTICAL","panel",narrow?8:16,8);
 if(narrow){button(nav,"展","draft", "secondary",40);txt(nav,"phase","02",32,14,"accent");}
 else {
 txt(nav,"project-title","创作流程",168,12,"muted");
 const names=["项目设定","故事剧本","制作方案","素材准备","分段提示词","图文审阅","交付导出"];
 const statuses=["已采用 · S02",s.version.includes("已采用")?s.version:"草稿待审","需复核 · 3 镜头","草稿 · 4 项","需复核 · 2 分段","未开始","未开始"];
 for(let i=0;i<names.length;i++){const st=instance(nav,"Stage",168,{label:"0"+(i+1)+"  "+names[i],status:statuses[i]},i===1?"selected":"panel");st.paddingTop=st.paddingBottom=8;st.itemSpacing=2;}
 const spacer=box(nav,"弹性间距",168,10);spacer.layoutSizingVertical="FILL";
 txt(nav,"phase-note","本批：故事阶段可交互\n其他阶段仅展示状态",168,11,"muted");
 button(nav,"本步要求","requirements","secondary",168);
 const branches=box(nav,"原型分支入口",168,null,"VERTICAL",null,0,6);
 txt(branches,"demo","分支演示",168,11,"muted");
 button(branches,"手工稿冲突","manual","secondary",168);
 button(branches,"两轮修订上限","limit","secondary",168);
 button(branches,"审核与放行","semantic","secondary",168);
 txt(nav,"tools","任务中心 / 设置 · 未模拟",168,11,"muted");
 }
 const center=box(main,"成果区",M,H-80,"VERTICAL","canvas",24,16); const cw=M-48;
 const titleRow=box(center,"成果标题",cw,null,"HORIZONTAL",null,0,12);
 txt(titleRow,"title",s.compare?"场02 · 版本对比":s.panel==="downstream"?"采用后的下游影响":"故事剧本",cw-300,22);
 txt(titleRow,"version",s.version,288,13,s.version.includes("已采用")?"positive":"accent");
 const tool=box(center,"场次与版本工具栏",cw,36,"HORIZONTAL",null,0,8);
 if(s.compare){txt(tool,"compare-help",s.conflict?"两份草稿均基于 R03；选择工作稿不会采用。":"左：当前采用版  /  右：候选草稿；仅场02发生变化。",cw,13,"muted");}
 else {button(tool,"完整稿",null);button(tool,"场02",s.id==="open"?"selected":null);button(tool,"本步要求","requirements");txt(tool,"autosave","正文可编辑 · 演示采用预设文本",cw-340,12,"muted");}
 const content=box(center,"成果滚动区域",cw,400,"VERTICAL",null,0,16);content.layoutSizingVertical="FILL";content.clipsContent=true;content.overflowDirection="VERTICAL";
 if(s.compare){
 const row=box(content,"对比双栏",cw,null,"HORIZONTAL",null,0,20);const ww=(cw-20)/2;
 const a=box(row,"对比左栏",ww,null,"VERTICAL",null,0,12),b=box(row,"对比右栏",ww,null,"VERTICAL",null,0,12);
 note(a,s.conflict?"M01 · 你的手工稿":"R03 · 当前已采用",s.conflict?"基于 R03 · 新增开闸动作":"原版含三句对白",ww,"muted");
 scene(a,"场02 · 码头候船厅",scripts[s.conflict?"manual":"r03"],ww,false);
 note(b,s.conflict?"R04 · AI 草稿":s.version.includes("R05")?"R05 · AI 草稿":"R04 · AI 草稿","基于 "+(s.draft==="r05"?"R04":"R03")+" · 尚未采用",ww);
 scene(b,"场02 · 修改内容",scripts[s.draft||"r04"],ww,true);
 note(b,"变化摘要",s.conflict?"手工稿新增开闸；AI 稿改为无对白。没有自动合并。":s.draft==="r05"?"删除对白；右手始终持票；明确左手持相机与右手递票。":"删除对白；加入贴近相机、船票滑落与回望动作。",ww);
 } else if(s.panel){
 const panels={
 impact:["采用 R05 会影响哪些内容？","当前采用 R03 → R05。采用后本任务结束，后续创作消息建立新线程。"],
 impact04:["采用 R04 会影响哪些内容？","当前采用 R03 → R04。仅采用剧本，不自动更新下游。"],
 downstream:["3 个镜头、2 个分段需要复核","来源：实际采用剧本 R05。保留现有下游版本与历史记录。"],
 limit:["已用完 2 轮自动修订","当前 R07 仍有语义分歧。系统已停止，等待你的决定。"],
 semantic:["Q17 · 相机收起与递票衔接不清","类型：语义 / 连续性分歧。原审核结论：未通过。"],
 reason:["逐项放行 · Q17","必须填写理由并明确确认。本页未填写理由，确认不可用。"],
 reasonFilled:["逐项放行 · Q17","接受相机收起动作留白。后续镜头用检票员接票的特写交代结果，愿意承担当前连续性不够明确的风险。"],
 released:["用户放行 · R07 / Q17","原独立审核结论：未通过。用户放行记录 U01 与理由单独保留。尚未采用。"],
 invalid:["旧放行记录不适用于 R08","相关手部动作已经改变。R07 / Q17 的记录保留历史，但不能放行 R08。"],
 format:["F03 · 角色引用不存在","场02引用「船员B」，但设定 S02 无此角色。必须修复，无法人工放行。"],
 formatFixed:["格式复检通过 · 语义审核待执行","已将「船员B」改为「检票员」。新版本 R08 必须重新审核。"],
 resume:["原输入 R03 ≠ 当前采用 R05","继续原线程只恢复聊天和任务记录，不回滚成果、不清零轮数。"],
 requirements:["本步要求 · 故事剧本","无对白；保留登船结局；仅修改场02。\n\n这些要求可以保存，或保存后带入对话。"],
 image:["用验证图确认空间关系","右手持票，左手持相机。计划比较两个构图，不能靠增加文字无限论证。"]
 };
 const p=panels[s.panel];note(content,p[0],p[1],cw,["format","invalid"].includes(s.panel)?"danger":s.panel==="released"?"warning":"accent");
 if(["impact","impact04","downstream"].includes(s.panel)){
 for(const [t,b] of [["制作方案 · 镜头04–06","持物与递票动作变化 → 标记需复核"],["素材准备 · 相机与船票","现有图片保留；是否适配新动作需人工核对"],["分段提示词 · 分段02–03","对白删除与动作时序变化 → 标记需复核"],["交付导出","使用最新采用版本；需复核项未完成前不能作为最终交付"]])note(content,t,b,cw,"muted");
 } else if(s.panel==="limit"){
 button(content,"手工调整","limitManual","secondary",220);
 button(content,"暂存当前稿","laterLimit","secondary",220);
 button(content,"查看独立审核与放行","semantic","secondary",220);
 } else if(["semantic","reason","reasonFilled","released","format","invalid","formatFixed"].includes(s.panel)){
 scene(content,"问题定位 · 场02",scripts[s.panel==="format"?"r07":s.draft||"r07"],cw,true);
 if(s.panel==="reason")button(content,"确认放行（请先填理由）",null,"disabled",280);
 if(s.panel==="released")note(content,"放行理由","接受相机收起动作留白；后续镜头用检票员接票特写交代结果。承担当前连续性风险。",cw,"warning");
 if(s.panel==="format")button(content,"采用 / 交付已阻断",null,"disabled",240);
 } else if(s.panel==="image"){
 note(content,"批次确认前","工具：独立图像服务待接入  /  2 张  /  费用未报价\n费用明确后才能确认付费。此原型未发起生成。",cw,"warning");
 button(content,"确认生成（服务未接入）",null,"disabled",260);
 } else if(s.panel==="requirements"){note(content,"保存状态","尚未保存；保存不会发送。示例文本不可自由输入，点击底部按钮演示保存结果。",cw,"muted");}
 } else {
 if(s.selected)note(content,"已选中 · 场02","范围已带入：剧本 R03 / 场02 / 锁定结局。",cw);
 if(s.id==="conflict")note(content,"AI 返回期间存在手工新稿","R04 和 M01 均已保留。请比较后选择工作草稿。",cw,"warning");
 scene(content,"场01 · 渡轮码头 · 外 / 黄昏","末班渡轮即将离岸。林夏走进候船厅，颈前挂着父亲留下的旧相机，手中是一张去海岛的单程船票。",cw,false);
 scene(content,"场02 · 候船厅检票口 · 内 / 黄昏",scripts[s.draft||"r03"],cw,!!s.selected||!!s.draft,s.id==="open"?"selected":null);
 scene(content,"场03 · 渡轮甲板 · 外 / 黄昏","林夏登上渡轮。她举起旧相机，对准逐渐远去的码头，按下快门。",cw,false);
 }
 const actions=box(center,"成果操作栏",cw,44,"HORIZONTAL",null,0,10);
 if(s.actions)for(const [label,to] of s.actions)button(actions,label,to,label.includes("采用")||label.includes("确认")?"primary":"secondary");
 else if(s.id==="open"){button(actions,"选择场02开始","selected","primary");txt(actions,"help","点击场02正文也可选中",300,12,"muted");}
 else if(s.selected&&s.id==="selected")button(actions,"让 AI 修改","compose","primary");
 else txt(actions,"footerhint",s.compare?"退出对比会恢复成果、会话与栏宽。":"当前已采用版本与工作草稿独立保存。",cw,12,"muted");
 const chat=box(main,narrow?"对话窄条":"AI 对话",R,H-80,"VERTICAL","panel",narrow?8:16,12);
 if(narrow){button(chat,"聊",s.conflict?"conflict":s.draft==="r05"?"draft05":"draft","secondary",40);txt(chat,"reminder","1\n条\n新\n消\n息",32,12,"accent");}
 else {
 const h=box(chat,"聊天标题",348,36,"HORIZONTAL",null,0,12);txt(h,"assistant","导演助手",222,17);button(h,"新任务",s.id==="adopted"?"newTask":s.status.includes("T018")?"newWhileActive":"compose","secondary",114);
 txt(chat,"context","剧本 "+(s.draft==="r05"?"R05":"R03")+"  /  场02  /  锁定结局",348,12,"accent");
 const messages=box(chat,"消息独立滚动区",348,340,"VERTICAL",null,0,12);messages.layoutSizingVertical="FILL";messages.clipsContent=true;messages.overflowDirection="VERTICAL";
 for(const [role,body] of s.msg||[])instance(messages,"Message",348,{role,body},role.startsWith("你")?"selected":"card");
 if(s.chatActions)for(const [label,to] of s.chatActions)button(messages,label,to,"secondary",348);
 const composer=box(chat,"固定输入区",348,184,"VERTICAL","card",12,8);composer.cornerRadius=10;
 txt(composer,"input",s.input||"说说你想修改什么…",324,14,s.send?"text":"muted");
 const gap=box(composer,"输入弹性空间",324,1);gap.layoutSizingVertical="FILL";
 const cr=box(composer,"发送工具",324,36,"HORIZONTAL",null,0,8);button(cr,"附件",null,"disabled",72);
 txt(cr,"preset","预设输入",80,11,"muted");
 if(s.running)button(cr,"停止","stopped","secondary",148);else button(cr,s.send?"发送示例":"发送",s.send||null,s.send?"primary":"disabled",148);
 txt(chat,"simulation","原型预设内容 · 未连接 AI · 未调用付费服务",348,10,"muted");
 }
 const foot=box(root,"原型范围提示",W,24,"HORIZONTAL","panel",4,0);txt(foot,"scope","R2 交互预演  ·  中文桌面 Web  ·  灰色无效控件尚未模拟；输入与执行采用预设状态",W-8,10,"muted");
 roots.push({id:s.id,node:root.id,name:s.name,width:W,height:H,descendants:root.findAll(()=>true).length});
 return root;
}

