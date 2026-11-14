// Package 7 uses the isolated launcher in real_guard.py. All writes go through the page.
const {setTimeout:delay}=require("node:timers/promises");
const {chromium} = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const TASK = "按已采用的上游成果完成本阶段";
const base = process.env.ADW_REAL_URL;
const evidence = process.env.ADW_REAL_EVIDENCE;
const route = process.argv[2];
let action = process.argv[3] || "kickoff";
assert(["A","B","D"].includes(route));
const stages=["story","characters","world","treatment","script","stress","asset_design","acting","plan","prompts"];
assert(["kickoff","inspect","advance","image","review","package","change-upstream","stop-skipped-stress",...stages].includes(action));
const keep="修伞人的左手食指缠着一圈蓝布。";
const movement="请让修伞人把工具卷放在摊板左角再打开伞。";
assert(new URL(base).hostname === "127.0.0.1");
const ideas = {A:"雨刚停，一个修伞人在收摊前靠伞上的补丁把两把拿错的伞还给各自的主人。",D:"MILO要接住滚下厨房桌面的马克杯，LEO却在桌下把收纳篮往别处拖。"};
const script = "场01 内景 钟表铺 黄昏\n空铺里，一座摆钟的分针停在五十九分。钟摆向左晃，墙上另一只钟响了一声。\n场02 内景 钟表铺 黄昏\n分针跳到整点。窗外最后一缕阳光离开表盘，摆钟响了一声。";
async function main(){
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH});
  const page=await browser.newPage({viewport:{width:1440,height:950}});
  const dir=path.join(evidence,route);
  fs.mkdirSync(dir,{recursive:true});
  const save=(file,value)=>fs.writeFileSync(path.join(dir,file),JSON.stringify(value,null,2));
  const query=async(name,args={})=>{
    const res=await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
    assert.equal(res.status(),200,await res.text());return res.json();
  };
  const button=name=>page.getByRole("button",{name,exact:true});
  let violation;
  await page.route(/\/api\/(?:cmd|with-files)\//,async request=>{
    const command=new URL(request.request().url()).pathname.split("/").at(-1);

    try{
      assert(!["waive","waive_segment_issue"].includes(command),"需导演判断");
      assert.notEqual(command,"resolve_impact","采用新上游后须重新生成受影响的下游");
      if(["post_message","create_task"].includes(command)){
        assert(!request.request().url().includes("/with-files/"),"阶段验收不使用附加任务材料");
        const payload=request.request().postDataJSON();
        if(command==="post_message" && payload.discussion_only)assert(route==="A" && action==="script" && payload.text===movement);
        else assert.equal(payload.goal??payload.text,TASK);
      }
    }catch(error){violation=error;await request.abort();return;}
    await request.continue();
  });
  try{
    const storage=await query("storage_status");
    assert.equal(storage.dataDir,process.env.ADW_REAL_DATA);
    await page.goto(base);
    if(action==="kickoff"){
      assert(!fs.existsSync(path.join(dir,"kickoff.json")),"已有开工回执");
      await page.locator(".home-controls button").first().click();
      if(route==="D"){
        await page.getByText("用模板新建项目",{exact:true}).click();
        await page.getByRole("button",{name:/新项目（PUGGY ROOM 配置）/}).click();
      }else await button("＋ 新项目（设定为空）").click();
      if(route==="B"){
        await button("导入故事稿 / 剧本").click();
        await page.locator('input[type=file][accept=".md,.txt,.docx,.pdf"]').setInputFiles({name:"钟表铺定稿.txt",mimeType:"text/plain",buffer:Buffer.from(script)});
        await page.locator('select').filter({has:page.locator('option[value="script_source"]')}).selectOption("script_source");
        await button("继续").click();
        await page.getByRole("button",{name:/去开工 E/}).click();
        await page.getByLabel("导入稿用途").selectOption("locked_screenplay");
      }else{
        await page.locator("textarea").first().fill(ideas[route]);
        await page.getByRole("button",{name:/规格预设/}).click();
        await page.getByRole("button",{name:/调整完整开工设置/}).click();
      }
      await button("本集调整").click();
      for(const role of ["故事 写","审"])
        for(const [label,value] of [["执行器","codex"],["型号","gpt-6.1-sol"],["推理强度","high"]])
          await page.getByLabel(role+label,{exact:true}).selectOption(value);
      const receipt=page.waitForResponse(r=>r.url().endsWith("/cmd/start_episode"));
      await button("开工并开始创作").click();
      const res=await receipt;
      assert.equal(res.status(),200,await res.text());
      save("kickoff.json",await res.json());
    }
    const {episodeId}=JSON.parse(fs.readFileSync(path.join(dir,"kickoff.json")));
    const args={episode_id:episodeId};
    const read=()=>query("episode_view",args);
    const artifact=view=>view.artifacts.find(a=>a.stage===action);
    async function capture(){
      const view=await read();
      save("episode.json",view);
      for(const name of ["director_decisions","production_supplements"])save(name+".json",await query(name,args));
      for(const art of view.artifacts){
        for(const version of art.versions)save(version.id+"-inputs.json",await query("version_inputs",{version_id:version.id}));
        for(const task of art.tasks)for(const run of task.runs)save(run.id+"-manifest.json",await query("run_manifest",{run_id:run.id}));
      }
      return view;
    }
    const advancing=action==="advance";
    const receiptFor=pattern=>page.waitForResponse(r=>pattern.test(r.url()));
    async function writeDraft(){
      await page.locator(".composer:visible textarea").fill(TASK);
      const receipt=receiptFor(/\/cmd\/(create_task|post_message)$/);
      await button("发送消息").click();
      const response=await receipt;
      assert(response.ok(),await response.text());
    }
    const stale=async stage=>(await query("impacts",args)).some(i=>i.target_ref==="workflow:"+stage);
    async function advanceStage(){
      await page.goto(base+"/#/e/"+episodeId+"/"+action);
      let art=artifact(await read());
      if(!art){
        await button("打开本阶段").click();
        await page.waitForFunction(()=>!document.body.innerText.includes("打开本阶段"));
        art=artifact(await read());
      }
      assert(art,"阶段未打开");
      const latest=art.versions.at(-1);
      if(latest && latest.id===art.adopted_version_id){
        assert(await stale(action),"本阶段已采用且没有待复核依赖");
        await button("修改本阶段").click();await button("结束修改").waitFor();
        await writeDraft();return;
      }
      if(route==="A" && action==="characters" && !(await query("director_decisions",args)).some(d=>d.text===keep)){
        await button("本步需求与参考").click();
        await page.getByLabel(/必须保留/).fill(keep);
        await button("保存本步配置").click();
        await page.locator(".modal").waitFor({state:"hidden"});
      }
      if(!latest){
        assert(!art.tasks.some(t=>t.runs.length),"已有任务，须核对原回执后再重试");
        await writeDraft();
      }else if(latest.gate.formatErrors.length){
        assert(art.versions.length<4,"重复格式错误："+latest.gate.formatErrors.join("；"));
        await writeDraft();
      }else if(route==="A" && action==="script" && !(await query("decision_candidates",{artifact_id:art.id})).some(d=>d.text===movement) && !art.tasks.some(t=>(t.messages??[]).some(m=>m.text===movement))){
        await page.locator(".composer:visible textarea").fill(movement);
        const receipt=receiptFor(/\/cmd\/post_message$/);
        await button("发送消息").click();
        const response=await receipt;
        assert(response.ok(),await response.text());
      }else if(route==="A" && action==="script" && !(await query("director_decisions",args)).some(d=>d.text===movement)){
        await page.locator(".msg.user:visible").filter({hasText:movement}).getByRole("button",{name:"记为决定"}).click();
        assert((await query("director_decisions",args)).some(d=>d.text===movement));
      }else if(route==="A" && action==="script" && !(latest.content.scenes??[]).some(sc=>sc.action?.includes("摊板左角"))){
        await writeDraft();
      }else{
        if(latest.gate.unresolved?.length || latest.gate.blocking?.length){
          save(action+"-需导演判断.json",latest.gate);throw Error("需导演判断："+action);
        }
        assert(latest.gate.adoptable,JSON.stringify(latest.gate));
        await button("采用并继续").first().click();
        await page.waitForURL(url=>!url.hash.split("?")[0].endsWith("/"+action));
      }
    }
    if(action==="image"){
      const id=process.argv[4];
      const view=await read();
      const design=view.artifacts.find(a=>a.stage==="asset_design");
      const asset=design?.versions.find(v=>v.id===design.adopted_version_id)?.content.assets.find(a=>a.id===id);
      assert(asset?.imagePrompt,"只使用已采用 07 的出图原文");
      const name=asset.id;
      const prior=(await query("episode_batches",args)).find(b=>b.spec?.items.some(i=>i.target?.name===name));
      await page.goto(base+"/#/e/"+episodeId+"/assets");
      let batchId=prior?.id;
      if(!prior){
        await button("出图").click();
        const modal=page.locator(".modal:visible");
        await modal.locator("select").filter({has:page.locator('option[value="codex-native"]')}).selectOption("codex-native");
        await page.getByLabel("图像任务创作型号").selectOption("gpt-6.1-sol");
        assert.equal(await page.getByLabel("图像任务推理强度").inputValue(),"high");
        await page.getByLabel("素材名称").fill(name);
        const category=process.argv[5]??asset.kind;
        assert(["identity","scene","prop","state","grid","reference","costume"].includes(category));
        await page.getByLabel("素材分类").selectOption(category);
        await page.getByLabel("绘图要求").fill(asset.imagePrompt);
        const receipt=receiptFor(/\/cmd\/create_batch$/);
        await button("生成摘要，去确认").click();
        const response=await receipt;assert(response.ok(),await response.text());batchId=(await response.json()).batchId;
        const batch=await query("batch_view",{batch_id:batchId});
        assert.equal(batch.provider,"codex-native");assert.equal(batch.model,"gpt-6.1-sol");
        assert.equal(batch.spec.billingMode,"subscription_usage");assert.equal(batch.count,1);
        await button("确认授权 1 张").click();await button("发送已授权的 1 张").click();
      }
      let status;
      do{
        status=await query("batch_view",{batch_id:batchId});
        save(batchId+"-image.json",status);
        if(status.state==="completed")break;
        assert(["running","authorized","sending","submitted","in_progress"].includes(status.state),status.state);
        await delay(4000);
      }while(true);
    }
    if(action==="review" || action==="package"){
      const view=await capture();
      const version=view.artifacts.find(a=>a.stage==="prompts")?.adopted_version_id;
      assert(version,"须先采用当前提示词");
      const review=()=>query("prompt_review_status",{version_id:version});
      if(action==="review"){
        await page.goto(base+"/#/e/"+episodeId+"/review");
        for(const [field,label] of [["coldRead","送审材料（第一遍）"],["comparison","开始第二遍"]]){
          let submitted=false;
          while(true){
            const state=await review();
            save("review.json",state);
            const rows=Object.values(state).map(row=>row[field]);
            assert(rows.length);
            const issue=rows.find(row=>["pending_decision","blocked","waived"].includes(row.state));
            if(issue){save("需导演判断.json",state);throw Error("独审意见："+JSON.stringify(issue));}
            if(rows.every(row=>row.state==="passed"))break;
            if(rows.some(row=>row.activeRun)){await delay(4000);continue;}
            assert(!submitted,"本遍执行结束，须核对诊断，证据已保存");
            const receipt=receiptFor(/\/cmd\/start_prompt_review_batch$/);
            await button(label).click();
            const response=await receipt;assert(response.ok(),await response.text());
            submitted=true;await delay(1000);
          }
        }
      }else{
        const state=await review();assert(Object.values(state).length && Object.values(state).every(row=>row.ok&&!row.waived));
        const problems=await query("delivery_problems",{...args,scope:"full"});
        save("delivery-check.json",problems);
        await page.goto(base+"/#/e/"+episodeId+"/delivery");
        const deliver=receiptFor(/\/cmd\/deliver$/);
        await button("生成图文交付").click();
        const response=await deliver;assert(response.ok(),await response.text());
        const {deliveryId}=await response.json();
        const verified=await query("verify_delivery",{delivery_id:deliveryId});
        save("delivery.json",await query("delivery_view",{delivery_id:deliveryId}));assert(verified.ok);save("verification.json",verified);
        await page.goto(base+"/#/e/"+episodeId+"/package?delivery="+deliveryId);
        await button("预览所选段").click();await page.getByRole("region",{name:"出包预览"}).waitFor();
        const exported=receiptFor(/\/cmd\/package$/);
        await button("下载 H3 包").click();
        const packed=await exported;assert(packed.ok(),await packed.text());
        const result=await packed.json();
        assert(path.resolve(result.path).startsWith(path.resolve(process.env.ADW_REAL_DATA)+path.sep));
        fs.copyFileSync(result.path,path.join(dir,route+".h3project.zip"));save("package.json",result);
      }
    }
    if(action==="change-upstream"){
      assert.equal(route,"A","锁定定稿走重新导入，C 选用 A 的可编辑剧本");
      const target=path.join(evidence,"C");
      fs.mkdirSync(target,{recursive:true});
      const record=(name,value)=>fs.writeFileSync(path.join(target,name+".json"),JSON.stringify(value,null,2));
      const before=JSON.parse(fs.readFileSync(path.join(dir,"delivery.json")));
      assert.equal(before.scope,"full");
      assert.equal((await query("verify_delivery",{delivery_id:before.id})).ok,true);
      const previous=(await read()).artifacts.find(a=>a.stage==="script");
      await page.goto(base+"/#/e/"+episodeId+"/script");
      await button("修改本阶段").click();await button("结束修改").waitFor();
      await button("✎ 编辑").click();
      const actionField=page.locator(".center:visible input[placeholder='内外景 · 时段']").first().locator("../..").locator("textarea").first();
      const old=await actionField.inputValue();
      const sentence=old.match(/[^。！？\n]+。/);
      assert(sentence);
      const replacement=sentence[0].slice(0,-1)+"，摊板上的水珠滚到板沿。";
      const changed=old.replace(sentence[0],replacement);
      assert.notEqual(changed,old);
      await actionField.fill(changed);
      const saved=receiptFor(/\/cmd\/save_manual$/);
      await button("保存为手稿").click();
      const response=await saved;assert(response.ok(),await response.text());
      const adopted=receiptFor(/\/cmd\/adopt$/);await button("只采用").click();
      const adoption=await adopted;assert(adoption.ok(),await adoption.text());
      const impacts=await query("impacts",args);
      record("impacts",impacts);
      for(const key of ["plan","prompts"])assert(impacts.some(i=>i.target_ref==="workflow:"+key));
      const delivery=await query("delivery_view",{delivery_id:before.id});
      assert.equal(delivery.state,"needs_recheck");record("delivery",delivery);
      record("change",{previous:previous.adopted_version_id,old:sentence[0],replacement});
      await page.goto(base+"/#/e/"+episodeId+"/package?delivery="+before.id);
      const denied=receiptFor(/\/cmd\/package$/);await button("下载 H3 包").click();
      const rejection=await denied;assert(!rejection.ok());const reason=await rejection.text();assert.match(reason,/重新核验|待复核/);record("拒绝出包",{status:rejection.status(),reason});
    }
    if(action==="stop-skipped-stress"){
      assert.equal(route,"B");
      const view=await read();
      assert(view.skippedStages.stress && view.kickoff.sourceIntent==="locked_screenplay");
      const running=view.artifacts.find(a=>a.stage==="stress")?.tasks.flatMap(t=>t.runs).filter(r=>["running","queued"].includes(r.status))??[];
      if(running.length){
        assert.equal(running.length,1);
        await page.goto(base+"/#/e/"+episodeId+"/stress");
        const receipt=receiptFor(/\/cmd\/stop_run$/);
        await page.locator(".assistant:visible").getByRole("button",{name:"停止",exact:true}).click();
        const response=await receipt;assert(response.ok(),await response.text());
      }
    }
    if(advancing){
      const order=route==="B"?stages.slice(stages.indexOf("script")):stages;
      for(action of order){
        if((await read()).skippedStages?.[action])continue;
        let writes=0;
        while(true){
          const view=await read();
          const stage=artifact(view);
          const tasks=stage?.tasks??[];
          const runs=tasks.flatMap(t=>t.runs);
          if(runs.some(r=>["queued","running"].includes(r.status))){
            await delay(4000);continue;
          }
          await capture();
          if(stage?.adopted_version_id && stage.adopted_version_id===stage.versions.at(-1)?.id && !await stale(action))break;
          const last=runs.at(-1);
          if(last?.status==="failed" && ["timeout","connection_error"].includes(last.reason) && !runs.some(r=>r.purpose==="retry") && writes===0){
            await page.goto(base+"/#/e/"+episodeId+"/"+action);
            const retry=receiptFor(/\/cmd\/retry_run$/);
            await button("重试").last().click();
            const response=await retry;assert(response.ok(),await response.text());
            writes++;await delay(1200);continue;
          }
          if(tasks.at(-1)?.state==="clarifying" || ["failed","waiting_quota","stopped"].includes(last?.status)){
            throw Error(action+" 任务需检查："+JSON.stringify(last));
          }
          assert(++writes<=5,action+" 未能完成，已保存当前证据");
          console.log(route,action,"页面操作",writes);
          await advanceStage();
          await delay(1000);
        }
      }
    }else if(stages.includes(action))await advanceStage();
    const view=await capture();
    console.log(route,episodeId,view.artifacts.flatMap(a=>a.tasks.flatMap(t=>t.runs.map(r=>[a.stage,r.status,r.error_class]))));
    if(violation)throw violation;
  }catch(error){
    await page.screenshot({path:path.join(dir,"error.png")});
    console.error((await page.locator("body").innerText()).slice(-1900));
    throw error;
  }finally{await browser.close();}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
