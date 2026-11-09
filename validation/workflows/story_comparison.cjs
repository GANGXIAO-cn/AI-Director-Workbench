const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

async function main() {
  const base = "http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const output = process.env.ADW_EVIDENCE_DIR || path.join(__dirname,"../../修复验证_20261108");
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:960}});
    page.setDefaultTimeout(10000);
    const errors=[];
    page.on("pageerror", e=>errors.push(e.message));
    const query=async(name,args={})=>(await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}})).json();
    assert.match((await query("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
    const [p]=await query("list_projects");
    const eid=p.recent[0].id;
    const story=async()=>(await query("episode_view",{episode_id:eid})).artifacts.find(a=>a.stage==="story");
    const before=await story();
    const original=before.adopted_version_id;
    const button = name=>page.getByRole("button",{name,exact:true});
    await page.goto(base+"/#/e/"+eid+"/story");
    await button("修改本阶段").click();
    await button("✎ 编辑").click();
    await page.locator(".center textarea").first().fill("夜班收银台的伞丢了。阿福翻查记录，发现伞留在门后。故事结束。");
    await button("保存为手稿").click();
    const option=page.getByText("按本集分工并排出稿",{exact:true});
    await option.click();
    await button("提交两份候选稿").waitFor();
    const source=new URLSearchParams(new URL(page.url()).hash.split("?")[1]).get("compare");
    assert.ok(source && source!==original);
    await page.getByRole("textbox",{name:"共同创作要求"}).fill("给雨伞安排一个能从画面看懂的结局。");
    await button("提交两份候选稿").click();
    const panel=page.locator(".story-compare");
    await panel.getByRole("button",{name:"打开B稿"}).waitFor();
    const paired=await story();
    const slots=paired.comparisons;
    assert.equal(slots.length,2);
    assert.deepEqual(slots.map(s=>[s.provider,s.requested_model,s.requested_effort]),[["claude","fixture-author","low"],["codex","fixture-reviewer","high"]]);
    assert.ok(slots.every(s=>paired.versions.find(v=>v.id===s.version_id)?.base_id===source));
    const manifests=await Promise.all(slots.map(s=>query("run_manifest",{run_id:s.run_id}).then(x=>x.manifest)));
    assert.deepEqual(manifests[0].inputs,manifests[1].inputs);
    assert.equal(paired.adopted_version_id,original);
    await page.reload();
    await panel.getByRole("radio",{name:"取B稿交接说明"}).check();
    await panel.getByRole("button",{name:"组合为新的待审故事稿"}).click();
    await page.waitForURL(url=>url.hash.includes("?v=")&&!url.hash.includes("compare="));
    const combined=await story();
    const manuscript=combined.versions.find(v=>v.id===new URLSearchParams(new URL(page.url()).hash.split("?")[1]).get("v"));
    assert.equal(manuscript.origin,"combined");
    assert.equal(manuscript.content.synopsis,"阿福循着湿脚印找回门后的雨伞。故事结束。");
    assert.equal(manuscript.content.handoff,"用门缝的光交代伞的位置。");
    assert.ok(!manuscript.gate.adoptable && combined.adopted_version_id===original);
    await page.goto(base+"/#/e/"+eid+"/story?v="+source+"&compare="+source);
    await panel.getByRole("button",{name:"打开A稿"}).waitFor();
    await page.locator(".center").evaluate(el=>{el.scrollTop=0;});
    await page.screenshot({path:path.join(output,"20_故事候选稿逐项比较.png")});
    await panel.getByRole("combobox",{name:"比较轮次"}).selectOption("2");
    await button("提交两份候选稿").click();
    await panel.getByRole("button",{name:"打开B稿"}).waitFor();
    assert.deepEqual((await story()).comparisons.map(c=>c.round_no),[2,2,1,1]);
    await panel.getByRole("combobox",{name:"比较轮次"}).selectOption("1");
    await panel.getByRole("button",{name:"打开A稿"}).click();
    await page.waitForURL(url=>url.hash.endsWith("?v="+slots[0].version_id));
    await page.waitForFunction(()=>document.querySelector('.desktop-page:not([hidden]) select[aria-label="创作执行器"]')?.value==="claude");
    const heading=page.locator(".assistant .head");
    await heading.getByText(/创作 Claude Code/).first().waitFor();
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,"故事并排出稿验收.json"),JSON.stringify({scope:"隔离服务与模拟执行器",
      sameSource:source,creators:slots.map(s=>({side:s.side,provider:s.provider,model:s.requested_model,effort:s.requested_effort,version:s.version_id})),
      commonInputFiles:manifests[0].inputs.map(({role,name,sha256})=>({role,name,sha256})),oldAdoptionPreserved:true,
      combinedNeedsReview:true,priorRoundsAccessible:true,runtimeErrors:errors},null,2)+"\n");
    console.log("PASS story comparison from assistant, equal inputs, two independent drafts, field combination, review gate and history");
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
