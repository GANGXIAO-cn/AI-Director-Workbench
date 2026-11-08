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
    await page.getByLabel("方法包",{exact:true}).selectOption("short-film@v9");
    await page.locator("p.error").filter({hasText:"方法包读取或指纹核对失败"}).waitFor();
    await page.getByLabel("应用方法包的项目").selectOption(pid);
    await page.getByLabel("方法包",{exact:true}).selectOption("short-film@v8");
    await page.getByText("当前 short-film@v7",{exact:true}).waitFor();
    await page.getByText(/动作能量与反应 · 正文修改/).click();
    const row=page.locator(".method-changes details").filter({has:page.locator("summary").filter({hasText:"动作能量与反应 · 正文修改"})}).first();
    assert.match(await row.innerText(),/24-37/);
    assert.match(await row.locator("pre").innerText(),new RegExp(tag));
    assert.match(await page.getByText(/阶段输入变更 ·/).innerText(),/制作方案、剧本|剧本、制作方案/);
    await page.screenshot({path:path.join(output,"41_v7方法更新与受影响阶段.png")});
    await page.getByRole("button",{name:"应用于新开工的集"}).click();
    await page.getByText("当前 short-film@v8",{exact:true}).waitFor();
    const oldNext=await command("create_task",{episode_id:eid,stage:"script",goal:"换版后旧集延续"});
    const {episodeId:newId}=await command("start_episode",{episode_id:null,project_id:pid,display_code:"新集",source_type:"idea",idea_text:"雨中站台",executor_plan:{create:{provider:"codex"},review:{provider:"claude"}}});
    await command("create_task",{episode_id:newId,stage:"story",goal:"起草新集"});
    const art=(await query("episode_view",{episode_id:newId})).artifacts.find(a=>a.stage==="story");
    const version=art.versions.at(-1).id;
    await command("submit_review",{version_id:version});
    await command("adopt",{artifact_id:art.id,version_id:version,expected_adopted_version_id:null,and_continue:true});
    const newTask=await command("create_task",{episode_id:newId,stage:"script",goal:"换版后新集起草"});
    assert.deepEqual(await query("run_methods",{run_id:first}),original);
    const cases=[];
    for (const [label,id,ref] of [["换版前旧集",first,"v7"],["换版后旧集",oldNext.runId,"v7"],["换版后新集",newTask.runId,"v8"]]) {
      const doc=await query("run_methods",{run_id:id});
      const section=doc.sections.find(s=>s.name===target+".md");
      assert.equal(section.text.includes(tag),ref==="v8");
      assert.equal(doc.ref,"short-film@"+ref);
      assert.equal(section.source.lines,ref==="v8"?"24-37":"22-34");
      cases.push({label,run:id,pack:doc.ref,inputHash:doc.inputHash,section});
    }
    await page.goto(base+"/#/sys/tasks");
    await page.getByRole("row").filter({hasText:"换版后新集起草"}).click();
    await page.getByText("查看本次冻结的方法原文",{exact:true}).click();
    const method=page.locator("summary").filter({hasText:target+".md · 通用"});
    await method.click();
    assert.ok((await method.locator("..").innerText()).includes(tag));
    await method.scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(output,"42_v8演练新集冻结方法原文.png")});
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,"V6_v7基线方法小节改动与新旧集回放.json"),JSON.stringify({fixture:"隔离 Git 测试分支 verify-method-sync，模拟执行器",impact,cases,errors},null,2)+"\n");
    console.log("PASS frozen source line relocation, released pack comparison and old/new episode replay");
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
