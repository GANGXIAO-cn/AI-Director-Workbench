const assert=require("node:assert/strict");
const {createHash}=require("node:crypto");
const {chromium}=require("playwright");
async function main(){
  const base="http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1280,height:900}});
    page.setDefaultTimeout(10000);
    const query=async(name,args={})=>{
      const r=await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
      assert.ok(r.ok(),await r.text());return r.json();
    };
    assert.match((await query("storage_status")).dataDir,/adw-workflows-/);
    const [project]=await query("list_projects"), eid=project.recent[0].id;
    const open=async stage=>{
      await page.goto(base+"/#/e/"+eid+"/"+stage);
      await page.getByRole("button",{name:"过关清单",exact:true}).click();
      const modal=page.locator(".modal:visible");
      await modal.locator(".stage-check-list li").first().waitFor();return modal;
    };
    const expected={
      story:["本版独立审查","本集正在使用这一版"],script:["分场剧本结构","上游故事稿"],
      plan:["G02 段长","设计 6 秒"],space:["已跳过","本集使用已有站台布局"],
      cast:["未通过","尚未确认出镜状态"],assets:["G01 素材引用","已关联 1 张图片"],
      prompts:["提示词结构与实际引用"],review:["G01 · 冷读","G02 · 对照","缺少匹配当前内容的审查回执"],
      publish:["本阶段稿件","尚无可检查的版本"],delivery:["未通过","当前整集的交付条件"],
      package:["ZIP · G01","与导出指纹一致"],
    };
    for(const [stage,texts] of Object.entries(expected)){
      const panel=await open(stage);
      const content=await panel.innerText();
      for(const text of texts) assert.ok(content.includes(text),stage+": "+text+" / "+content);
      if(stage==="review"){
        assert.match(await panel.locator("li").filter({hasText:"G01 · 冷读"}).innerText(),/^通过/);
        assert.match(await panel.locator("li").filter({hasText:"G02 · 对照"}).innerText(),/^未通过/);
      }
      if(stage==="story"){
        const report=await query("stage_checklist",{episode_id:eid,stage});
        assert.ok(report.creationMethods.length && report.methods.length);
        assert.ok(report.creationMethods.every(m=>m.methodSource.source.enhancementIds.includes("00-A")));
        assert.ok(report.methods.every(m=>m.methodSource.source.enhancementIds.includes("08-C")));
        const method=report.creationMethods[0];
        const detail=panel.locator("details").filter({has:page.getByText(method.name,{exact:true})});
        await detail.locator("summary").click();
        assert.equal(createHash("sha256").update(await detail.locator("pre").textContent()).digest("hex"),method.methodSource.sha256);
      }
      await panel.getByRole("button",{name:"关闭清单"}).click();
    }
    const trial=await open("delivery?scope=trial&segment=G01");
    const text=await trial.innerText();
    assert.ok(text.includes("所选试验段的交付条件") && text.includes("文件与登记清单一致") && !text.includes("未通过"),text);
    console.log("PASS 十一个阶段、实际稿件与当前方法原文、分段独审、试验交付及 ZIP 指纹");
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
