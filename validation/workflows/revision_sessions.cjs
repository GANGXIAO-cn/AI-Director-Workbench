const assert=require("node:assert/strict");
const {chromium}=require("playwright");
const base="http://127.0.0.1:"+process.env.ADW_TEST_PORT;
async function main() {
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:960}});
    page.setDefaultTimeout(6500);
    const errors=[];page.on("pageerror",e=>errors.push(e.message));
    const query=async(name,args={})=>{
      const r=await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
      assert.ok(r.ok(),await r.text());return r.json();
    };
    assert.match((await query("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
    const {token}=await (await page.request.get(base+"/api/session")).json();
    const command=async(name,data)=>{
      const r=await page.request.post(base+"/api/cmd/"+name,{data,headers:{"x-adw-token":token}});
      assert.ok(r.ok(),await r.text());return r.json();
    };
    const [project]=await query("list_projects");
    const eid=project.recent[0].id;
    const art=async stage=>(await query("episode_view",{episode_id:eid})).artifacts.find(a=>a.stage===stage);
    const btn=name=>page.getByRole("button",{name,exact:true});
    for(const stage of ["story","script","plan","prompts","publish"]) {
      const prior=await art(stage);
      const a=prior.versions.find(v=>v.id===prior.adopted_version_id);
      let edit={notes:"浏览器验收的另一版"};
      if(stage==="publish")edit={title:"A different ending"};
      if(stage==="prompts"){
        const segments=structuredClone(a.content.segments);
        segments.G01.txt+="\n远处传来钟声。";edit={segments};
      }
      const b=await command("save_manual",{artifact_id:prior.id,base_version_id:a.id,content:edit});
      await page.goto(base+"/#/e/"+eid+"/"+stage);
      await btn("修改本阶段").waitFor();
      if(stage==="story")await page.goto(base+"/#/e/"+eid+"/story?v="+a.id);
      assert.ok(await page.locator(".center time").getAttribute("datetime"));
      await btn("比较版本").click();
      const modal=page.locator(".modal:visible").last();
      await modal.getByLabel("原版本").selectOption(a.id);
      await modal.getByLabel("比较版本").selectOption(b.versionId);
      await modal.locator("ins.d").first().waitFor();
      await modal.getByRole("button",{name:"关闭",exact:true}).click();
      assert.ok(await btn("恢复此完整稿为新版本").isDisabled());
      if(stage==="story" || stage==="script") assert.ok(await btn("✎ 编辑").isDisabled());
      if(stage==="plan"){
        await btn("生成段").click();assert.ok(await page.locator(".center tbody select").first().isDisabled());
      }
      if(stage==="prompts") assert.ok(!(await page.getByLabel("G01 提示词正文").isEditable()));
      if(stage==="publish") assert.ok(await btn("保存为新版本").isDisabled());
      assert.equal((await art(stage)).editState,"protected");
    }
    await page.goto(base+"/#/e/"+eid+"/story");
    const composer=page.locator(".assistant .composer textarea");
    const stateBefore=await art("story");
    await composer.fill("先聊聊结尾，为何这样写？");await composer.press("Control+Enter");
    await page.getByRole('paragraph').filter({hasText:/^本次只讨论当前稿件。$/}).waitFor();
    assert.equal((await art("story")).versions.length,stateBefore.versions.length);
    await composer.fill("修改第09步");await composer.press("Control+Enter");
    await page.waitForURL("**/plan");await btn("结束修改").click();await btn("修改本阶段").waitFor();
    await page.goto(base+"/#/e/"+eid+"/story");
    const original=(await art("story")).adopted_version_id;
    for (const mode of ["beginner","advanced","expert"]){
      const modeChange=page.waitForResponse(r=>r.url().endsWith("/api/cmd/set_cooperation_mode") && r.ok());
      await page.getByLabel("合作档位").selectOption(mode);await modeChange;
      if(mode!=="expert"){
        await btn("修改本阶段").waitFor();
        if(mode==="beginner"){await composer.fill("修改这里");await composer.press("Control+Enter");}
        else await btn("修改本阶段").click();
        await btn("结束修改").waitFor();
      }
      await page.goto(base+"/#/e/"+eid+"/story?v="+original);
      await btn("✎ 编辑").click();
      const field=page.locator(".center textarea").first();await field.fill((await field.inputValue())+"\n钟声响起。"+mode);
      await btn("保存为手稿").click();
      await page.waitForURL(/story\?v=.+/);
      await btn("关闭预览").click();
      if(mode!=="expert"){await btn("结束修改").click();await btn("修改本阶段").waitFor();}
      await page.goto(base+"/#/e/"+eid+"/script");
      await page.goto(base+"/#/e/"+eid+"/story");
      const current=await art("story");assert.equal(current.adopted_version_id,original);
      assert.match(current.versions.at(-1).content.synopsis,new RegExp(mode+"$"));
      assert.equal(current.editState,mode==="expert"?"open":"protected");
    }
    const before=await art("story"), candidate=before.versions.at(-1);
    await page.goto(base+"/#/e/"+eid+"/story?v="+candidate.id);
    await page.locator(".modal:visible").getByRole("button",{name:"发起独立审查",exact:true}).click();
    await page.locator(".modal:visible").getByRole("button",{name:"只采用",exact:true}).click();await page.getByText("R"+String(candidate.display_no).padStart(2,"0")+" 已采用",{exact:true}).first().waitFor();
    assert.equal((await art("story")).adopted_version_id,candidate.id);
    await page.goto(base+"/#/e/"+eid+"/story?v="+original);
    await btn("恢复此完整稿为新版本").click();
    await page.waitForURL(url=>url.hash.includes("story?v=") && !url.hash.endsWith(original));
    const restored=await art("story");
    assert.deepEqual(restored.versions.at(-1).content,restored.versions.find(v=>v.id===original).content);
    assert.equal(restored.adopted_version_id,candidate.id);
    assert.deepEqual(errors,[]);
    console.log("PASS 五阶段受保护查看、时间与双版本差异；同集三档改稿、结束与返回、明确采用、恢复旧稿");
  }finally{await browser.close();}
}
main().catch(err=>{console.error(err);process.exitCode=1;});
