const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const {createHash} = require("node:crypto");
const {chromium} = require("playwright");

async function main() {
  const base = "http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const root = path.resolve(__dirname,"../..");
  const evidence = path.join(root,"修复验证_20261108");
  const browser = await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:960}});
    const errors=[];
    page.on("pageerror",error=>errors.push(error.message));
    page.setDefaultTimeout(10000);
    const get = async(name,args={})=>{
      const response = await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
      assert.equal(response.status(),200);
      return response.json();
    };
    assert.match((await get("storage_status")).dataDir,/adw-workflows-/);
    const token = (await (await page.request.get(base+"/api/session")).json()).token;
    const command = async(name,args)=>{
      const response = await page.request.post(base+"/api/cmd/"+name,{headers:{"x-adw-token":token},data:args});
      assert.equal(response.status(),200,await response.text());return response.json();
    };
    const [project] = await get("list_projects");
    const eid=project.recent[0].id;
    const run=async goal=>(await command("create_task",{episode_id:eid,stage:"prompts",goal})).runId;
    const initial=await run("检查本轮动作与声音");
    await page.goto(base+"/#/e/"+eid+"/prompts");
    const open=()=>page.getByRole("button",{name:"制作方法",exact:true}).click();
    await open();
    await page.getByText("查阅 H3 指南原文",{exact:true}).click();
    const current=await get("stage_methods",{episode_id:eid,stage:"prompts"});
    const guides={};
    assert.deepEqual(current.guides.map(g=>g.name),["h3BaseGuide.txt","h3ReferenceGuide.txt"]);
    for(const guide of current.guides){
      const summary=page.locator("summary").filter({hasText:guide.name});
      await summary.click();
      const bytes=await fs.readFile(path.join(root,"resources/skills",guide.methodSource.path));
      guides[guide.name]=createHash("sha256").update(bytes).digest("hex");
      assert.equal(guides[guide.name],guide.methodSource.sha256);
      assert.equal(await summary.locator("..").locator("pre").textContent(),bytes.toString());
    }
    const item=page.locator("summary").filter({hasText:"prompts · 工作台本地化规则"});
    await item.click();
    const local=await fs.readFile(path.join(root,"resources/skills/unified-film/prompts.md"),"utf8");
    assert.equal(await item.locator("..").locator(".field").textContent(),local);
    assert.match(local,/10\. 实际输入/);
    assert.equal(await item.locator("..").getByRole("checkbox").count(),0);
    await page.screenshot({path:path.join(evidence,"43_提示词十类组成与H3原文入口.png")});
    const after=await run("重写另一段动作与声音");
    const original=await get("run_methods",{run_id:initial});
    const revised=await get("run_methods",{run_id:after});
    for(const doc of [original,revised]){
      assert.equal(doc.sections.find(s=>s.name==="prompts.md").text,local);
      for(const source of current.sections){
        const part=doc.sections.find(s=>s.name===source.name);
        assert.equal(part.sha256,source.methodSource.sha256);
        assert.equal(part.text,source.text);
      }
      assert.ok(!doc.sections.some(s=>s.name in guides));
    }
    assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(evidence,"v7_提示词方法与H3原文浏览器验收.json"),JSON.stringify({
      project:project.id,episode:eid,guides,initial,after,originalInput:original.inputHash,changedInput:revised.inputHash,
      contractRequired:true,errors},null,2)+"\n");
    console.log("PASS full H3 guides, ten-part prompt method, versioned excerpts and frozen inputs across revisions");
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
