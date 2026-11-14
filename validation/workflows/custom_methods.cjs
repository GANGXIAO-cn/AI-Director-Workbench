const assert=require("node:assert/strict");
const fs=require("node:fs");
const path=require("node:path");
const {createHash}=require("node:crypto");
const {chromium}=require("playwright");

async function main(){
  const base="http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const output=path.join(__dirname,"../../修复验证_20261108");
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try{
    const page=await browser.newPage({viewport:{width:1440,height:920}});
    const errors=[];
    page.on("pageerror",e=>errors.push(e.message));
    const q=async(name,args={})=>{
      const r=await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
      assert.equal(r.status(),200);return r.json();
    };
    assert.match((await q("storage_status")).dataDir,/adw-workflows-/);
    const token=(await (await page.request.get(base+"/api/session")).json()).token;
    const cmd=async(name,args)=>{
      const r=await page.request.post(base+"/api/cmd/"+name,{data:args,headers:{"x-adw-token":token}});
      const value=await r.json();assert.equal(r.status(),200,JSON.stringify(value));return value;
    };
    const puggy = await cmd("create_project",{config_id:"puggy",name:"项目原文同步验收"});
    await page.goto(base+"/#/p/"+puggy.projectId+"/settings");
    await page.getByText("本项目有 5 份项目方法尚未同步",{exact:true}).waitFor();
    const sync=page.getByRole("button",{name:"同步项目方法",exact:true});
    for(const stage of ["story","script","asset_design","acting","prompts"])
      await page.locator("li").filter({hasText:"puggy-methods/"+stage+".md"}).waitFor();
    await sync.click();
    await page.getByRole("status").filter({hasText:"已备份并同步项目方法"}).waitFor();
    const imported=await q("custom_methods",{project_id:puggy.projectId});
    assert.equal(imported.methods.length,5);
    assert.ok(imported.methods.every(m=>m.versions.length===1));
    const name="雨棚下的停顿", testProject="技能上传验证";
    const {projectId:pid}=await cmd("create_project",{config_id:"_template",name:testProject});
    const start=async code=>(await cmd("start_episode",{episode_id:null,project_id:pid,display_code:code,
      source_type:"idea",idea_text:"雨棚前有人收伞。",executor_plan:{create:{provider:"codex"},review:{provider:"claude"}}})).episodeId;
    const first=Buffer.from("\uFEFF---\r\nname: umbrella-pause\r\n---\r\n# 雨棚\r\n先停半拍，再收伞。\r\n");
    const second=Buffer.from(first.toString().replace("半拍","一拍"));
    const digest=b=>createHash("sha256").update(b).digest("hex");
    await page.goto(base+"/#/sys/skills");
    await page.getByLabel("应用方法包的项目").selectOption(pid);
    await page.getByLabel("技能名称").fill(name);
    async function send(bytes){
      await page.getByLabel("技能 Markdown 文件").setInputFiles({name:"SKILL.md",mimeType:"text/markdown",buffer:bytes});
      const done=page.waitForResponse(r=>r.url().endsWith("/api/upload/custom-method"));
      await page.getByRole("button",{name:"上传 SKILL.md",exact:true}).click();
      const response=await done;assert.equal(response.status(),200);return response.json();
    }
    async function choose(version){
      await page.getByLabel(name+"采用版本").selectOption(version);
      const done=page.waitForResponse(r=>r.url().endsWith("/api/cmd/set_custom_methods"));
      await page.getByRole("button",{name:"保存自定义技能选择"}).click();
      assert.equal((await done).status(),200);
      await page.getByRole("button",{name:"保存自定义技能选择"}).waitFor({state:"hidden"});
    }
    const one=await send(first);
    await choose(one.versionId);
    const old=await start("R1开工");
    const two=await send(second);
    await page.getByText("版本差异",{exact:true}).waitFor();
    const patch=page.locator("pre").filter({hasText:"--- R1"});
    assert.match(await patch.innerText(),/-先停半拍，再收伞。/);
    assert.match(await patch.innerText(),/\+先停一拍，再收伞。/);
    await patch.scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(output,"27_上传技能的两个版本.png")});
    await choose(two.versionId);
    const fresh=await start("R2开工");
    async function run(eid,label,expected){
      const {runId}=await cmd("create_task",{episode_id:eid,stage:"story",goal:label});
      const snapshot=await q("run_methods",{run_id:runId});
      const used=snapshot.sections.filter(s=>s.source?.methodId===one.methodId);
      if(expected){
        assert.equal(used.length,1);assert.equal(used[0].sha256,digest(expected));assert.equal(used[0].text,expected.toString());
      } else assert.deepEqual(used,[]);
      return {runId,label,sha256:used[0]?.sha256??null,revision:used[0]?.source.revision??null};
    }
    const cases=[await run(old,"旧集换版前起草",first),await run(fresh,"新集采用上传技能",second)];
    await page.goto(base+"/#/e/"+old+"/story");
    await page.getByRole("button",{name:"制作方法",exact:true}).click();
    await page.getByLabel(name+"采用版本").waitFor();
    assert.equal(await page.getByLabel(name+"采用版本").inputValue(),one.versionId);
    await choose("");
    cases.push(await run(old,"旧集停用后起草",null));
    await page.goto(base+"/#/sys/tasks");
    await page.getByRole("row").filter({hasText:"旧集换版前起草"}).click();
    await page.getByText("查看本次冻结的方法原文",{exact:true}).click();
    const summary=page.locator("summary").filter({hasText:"上传技能 · "+name+" · R1"});
    await summary.click();
    await summary.locator("..").locator("pre").filter({hasText:"先停半拍，再收伞。"}).waitFor();
    await summary.scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(output,"28_旧集运行使用的上传技能.png")});
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,"自定义技能上传选用与回放.json"),JSON.stringify({fixture:"隔离测试库和模拟执行器",cases,errors},null,2)+"\n");
    console.log("PASS upload two SKILL.md versions, compare, pin per episode, disable and replay exact bytes");
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
