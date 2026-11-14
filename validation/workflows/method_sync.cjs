const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {chromium} = require("playwright");

async function main() {
  const base = "http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const output=path.join(__dirname,"../../修复验证_20261108");
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:960}});
    const errors=[];
    page.on("pageerror",e=>errors.push(e.message));
    const query=async(name,args={})=>{
      const r=await page.request.get(base+"/api/q/"+name+"?args="+encodeURIComponent(JSON.stringify(args)));
      assert.equal(r.status(),200);return r.json();
    };
    const token=(await (await page.request.get(base+"/api/session")).json()).token;
    const command=async(name,args)=>{
      const r=await page.request.post(base+"/api/cmd/"+name,{headers:{"x-adw-token":token},data:args});
      const obj=await r.json();assert.equal(r.status(),200,JSON.stringify(obj));return obj;
    };
    const {dataDir}=await query("storage_status");
    assert.match(dataDir,/\/adw-workflows-[^/]+\/data$/);
    const {pid,eid,first,impact}=JSON.parse(fs.readFileSync(path.join(path.dirname(dataDir),"method-test.json")));
    const original=await query("run_methods",{run_id:first});
    const tag="记录接触前的位置和接触后的位移";
    const target="动作能量与反应";
    await page.goto(base+"/#/sys/skills");
    const choices=page.getByLabel("方法包",{exact:true});
    await choices.locator("option",{hasText:"unified-film@draft"}).waitFor({state:"attached"});
    assert.deepEqual(await choices.locator("option").allTextContents(),["unified-film@draft"]);
    await page.getByLabel("应用方法包的项目").selectOption(pid);
    const bad=await page.request.get(base+"/api/q/regress_pack",{params:{args:JSON.stringify({ref:"short-film@v9"})}});
    assert.equal(bad.status(),422);
    const comparison=await query("compare_packs",{original:"short-film@v7",candidate:"short-film@v8",project_id:pid});
    const change=comparison.sections.find(s=>s.key===target);
    assert.equal(change.after.source.lines,"24-37");
    assert.ok(change.patch.includes(tag));
    const rejected=await page.request.post(base+"/api/cmd/adopt_pack",{headers:{"x-adw-token":token},
      data:{project_id:pid,ref:"short-film@v8",command_id:"reject-historical"}});
    assert.equal(rejected.status(),422);
    const oldNext=await command("create_task",{episode_id:eid,stage:"script",goal:"旧集当前方法"});
    const {episodeId:newId}=await command("start_episode",{episode_id:null,project_id:pid,display_code:"新集",
      source_type:"idea",idea_text:"雨中站台",executor_plan:{create:{provider:"codex"},review:{provider:"claude"}}});
    const newTask=await command("create_task",{episode_id:newId,stage:"story",goal:"新集当前方法"});
    assert.deepEqual(await query("run_methods",{run_id:first}),original);
    const cases=[];
    for(const [label,id,stage] of [["旧集",oldNext.runId,"script"],["新集",newTask.runId,"story"]]){
      const doc=await query("run_methods",{run_id:id});
      assert.equal(doc.ref,"unified-film@draft");
      assert.ok(doc.sections.some(s=>s.name===stage+".md"));
      cases.push({label,run:id,pack:doc.ref,inputHash:doc.inputHash});
    }
    await page.goto(base+"/#/sys/tasks");
    await page.getByRole("row").filter({hasText:"新集当前方法"}).click();
    await page.getByText("查看本次冻结的方法原文",{exact:true}).click();
    const method=page.locator("summary").filter({hasText:"story.md · 通用"});
    await method.click();
    assert.equal(await method.locator("..").locator("pre").textContent(),fs.readFileSync(path.join(__dirname,"../../resources/skills/unified-film/story.md"),"utf8"));
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,"V6_v7基线方法小节改动与新旧集回放.json"),JSON.stringify({fixture:"隔离 Git 测试分支 verify-method-sync，模拟执行器",impact,cases,errors},null,2)+"\n");
    console.log("PASS frozen source line relocation, released pack comparison and old/new episode replay");
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
