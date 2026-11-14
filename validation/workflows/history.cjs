const assert = require("node:assert/strict");
const {chromium} = require("playwright");
const base = "http://127.0.0.1:"+process.env.ADW_TEST_PORT;

async function main() {
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:960}});
    page.setDefaultTimeout(6000);
    const query = async (name,args={})=>(await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}})).json();
    assert.match((await query("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
    const [project] = await query("list_projects");
    const {episodes} = await query("project_overview",{project_id:project.id});
    const episode = code=>episodes.find(e=>e.display_code===code).id;
    const eid = episode("E03");
    const data = await query("episode_view",{episode_id:eid});
    const old = data.artifacts.find(a=>a.stage==="story").adopted_version_id;
    const button = name=>page.getByRole("button",{name,exact:true});
    await page.goto(base+"/#/p/"+project.id);
    await page.getByRole("link",{name:"导演助手 · E03"}).click();
    await page.waitForURL("**/#/e/"+eid+"/story");
    const log=page.getByRole("log",{name:"本阶段对话"});
    const models=page.locator(".assistant:visible .composer-model");
    async function checkModels(create,review) {
      for(const [label,provider] of [["创作",create],["独审",review]]) {
        const model=models.filter({has:page.locator("small").getByText(label,{exact:true})});
        await model.locator(".model-trigger").click();
        assert.equal(await model.getByLabel(label+"执行器").inputValue(),provider);
        await model.locator(".model-trigger").click();
      }
    }
    await checkModels("claude","codex");
    for(const row of await log.locator("details > summary").filter({hasText:"条对话"}).all())await row.click();
    await log.getByText("退回 · 1 条意见",{exact:true}).first().waitFor();
    assert.doesNotMatch(await log.innerText(),/诊断包含 pass/);
    await page.goto(base+"/#/e/"+eid+"/script");
    await log.locator("details > summary").filter({hasText:"条对话"}).first().waitFor();
    for(const summary of await log.locator("details > summary").filter({hasText:"条对话"}).all())await summary.click();
    const messages=await log.locator(".msg.user:visible").evaluateAll(els=>els.map(el=>Array.from(el.childNodes).filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.textContent).join("")));
    assert.deepEqual(messages,["按已采用的上游成果完成本阶段","将抽屉关上的动作放在第二场","改写本集开场动作"]);
    assert.equal(await log.getByText("退回 · 1 条意见",{exact:true}).count(),1);
    assert.equal(await log.getByText("通过 · 0 条意见",{exact:true}).count(),1);
    await log.locator('a[href$="?v='+old+'"]').first().click();
    await page.waitForURL("**/#/e/"+eid+"/story?v="+old);
    await page.locator(".modal:visible").getByText(data.artifacts.find(a=>a.stage==="story").versions.find(v=>v.id===old).content.synopsis,{exact:true}).first().waitFor();
    await page.goto(base+"/#/e/"+episode("E02")+"/story");
    await checkModels("codex","claude");
    assert.deepEqual(await log.locator(".msg.user").evaluateAll(els=>els.map(el=>Array.from(el.childNodes).filter(n=>n.nodeType===Node.TEXT_NODE).map(n=>n.textContent).join(""))),["另一集专属的夜班记录"]);
    await page.goto(base+"/#/p/"+project.id+"/groups");
    for (let i=0;i<2;i++) {
      await button("连续夜班").click();
      const members = page.locator(".card.line").filter({hasText:"先交班"}).locator(".spread.small > span:first-child");
      await members.first().waitFor();
      assert.deepEqual((await members.allTextContents()).map(s=>s.split(" · ").slice(0,2)),[["先交班","E03"],["再巡店","E02"]]);
      if (i===0) await page.reload();
    }
    await page.goto(base+"/#/p/"+project.id);
    // 实测 125：导演决定项目集卡不再提供“我有故事稿”，稿件在本集导演助手“+”中添加。
    await page.locator(".epcard").filter({hasText:"E02 ·"}).getByRole("link",{name:"继续"}).waitFor();
    assert.equal(await page.locator(".epcard").getByRole("button",{name:"我有故事稿"}).count(),0);
  console.log("PASS 分集分阶段对话、找回旧稿、审核结论、项目助手入口、执行器分工、故事组回读、集卡不再有我有故事稿");
  } finally { await browser.close(); }
}
main().catch(e=>{console.error(e);process.exitCode=1;});
