const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const {createHash} = require("node:crypto");
const {chromium} = require("playwright");

async function main() {
  const base = "http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const root = path.resolve(__dirname,"../..");
  const evidence = path.join(root,"修复验证_20261108");
  const installed = path.join(root,"methods/short-film/v7");
  const manifest = JSON.parse(await fs.readFile(path.join(installed,"manifest.json")));
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
    const guides={};
    for (const [name,description] of [["base-en.txt","文本与关键帧指南"],["ref-en.txt","全参考指南"]]) {
      const summary=page.locator("summary").filter({hasText:description+" · "+name});
      await summary.click();
      const body=await summary.locator("..").locator("pre").textContent();
      const bytes=await fs.readFile(path.join(installed,"guides",name));
      assert.equal(body,bytes.toString());
      guides[name]=createHash("sha256").update(bytes).digest("hex");
      assert.equal(guides[name],manifest.files[name].sha256);
      await summary.click();
    }
    const title="独立提示词的十类组成";
    const item=page.locator("summary").filter({hasText:title+" · 通用"});
    await item.click();
    assert.match(await item.locator("..").innerText(),/10\. 实际输入/);
    await item.scrollIntoViewIfNeeded();
    await page.screenshot({path:path.join(evidence,"43_提示词十类组成与H3原文入口.png")});
    await item.locator("..").getByRole("checkbox").uncheck();
    await page.getByRole("button",{name:"保存阶段方法"}).click();
    await page.getByRole("heading",{name:"本阶段制作方法"}).waitFor({state:"hidden"});
    assert.deepEqual((await get("stage_methods",{episode_id:eid,stage:"prompts"})).disabled,[title+".md"]);
    const after=await run("重写另一段动作与声音");
    const original=await get("run_methods",{run_id:initial});
    const revised=await get("run_methods",{run_id:after});
    assert.ok(original.sections.some(s=>s.name===title+".md"));
    assert.ok(!revised.sections.some(s=>s.name===title+".md"));
    for (const key of ["H3 关键帧、发声与配乐原文","H3 参考图、音轨与逐镜使用原文"]) {
      const spec=manifest.files[key];
      for (const doc of [original,revised]) {
        const part=doc.sections.find(s=>s.name===key+".md");
        assert.equal(part.sha256,spec.sha256);
        assert.equal(part.source.sha256,spec.source.sha256);
        assert.equal(part.text,await fs.readFile(path.join(installed,spec.path),"utf8"));
        assert.ok(!doc.sections.some(s=>s.name==="base-en.txt" || s.name==="ref-en.txt"));
      }
    }
    await page.reload();
    await open();
    await item.click();
    assert.equal(await item.locator("..").getByRole("checkbox").isChecked(),false);
    assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(evidence,"v7_提示词方法与H3原文浏览器验收.json"),JSON.stringify({
      project:project.id,episode:eid,guides,initial,after,originalInput:original.inputHash,changedInput:revised.inputHash,
      selectedInitially:true,disabledAfterReload:true,errors},null,2)+"\n");
    console.log("PASS full H3 guides, ten-part prompt method, versioned excerpts and frozen inputs after deselection");
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
