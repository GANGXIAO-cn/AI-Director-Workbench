const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const {createHash} = require("node:crypto");
const {chromium} = require("playwright");

async function main() {
  const base = "http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const root = path.resolve(__dirname,"../.."), pack = path.join(root,"methods/short-film/v7");
  const manifest = JSON.parse(await fs.readFile(path.join(pack,"manifest.json")));
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1024}});
    page.setDefaultTimeout(8000);
    const errors=[],results=[];
    page.on("pageerror",err=>errors.push(err.message));
    const get=async(name,args={})=>(await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}})).json();
    assert.match((await get("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
    const projects=await get("list_projects");
    assert.equal(projects.length,2);
    const btn=name=>page.getByRole("button",{name,exact:true});
    const readExamples=async(scope,project)=>{
      const grid=scope.locator("details").filter({has:page.locator("summary").getByText("九格状态与镜头示意",{exact:true})}).first();
      await grid.locator("summary").first().click();
      const cases=grid.locator(":scope > details");
      const catalog={...manifest.files,...(manifest.projectFiles[project.config_id]||{})};
      const keys=manifest.stages.grid_examples.filter(k=>catalog[k]);
      assert.equal(await cases.count(),keys.length);
      const checks=[];
      for(const [i,key] of keys.entries()){
        const row=cases.nth(i), entry=catalog[key];
        await row.locator("summary").click();
        const value=await row.locator("pre").textContent();
        assert.equal(value,await fs.readFile(path.join(pack,entry.path),"utf8"));
        assert.equal(createHash("sha256").update(value).digest("hex"),entry.sha256);
        assert.ok((await row.innerText()).includes(entry.source.lines));
        checks.push({title:key,sha256:entry.sha256,lines:entry.source.lines});
        await row.locator("summary").click();
      }
      return checks;
    };
    for(const project of projects) {
      const eid=project.recent[0].id;
      await page.goto(base+"/#/e/"+eid+"/assets?tab=needs");
      const work=page.locator(".desktop-page:not([hidden]) .asset-stage");
      const needs=await readExamples(work,project);
      await btn("本轮出图").click();
      await btn("放大 开场九格 v1").click();
      const detail=await readExamples(page.locator(".modal:visible"),project);
      assert.deepEqual(detail,needs);
      await btn("关闭原图").click();
      await btn("修一格").click();
      const editing=await readExamples(page.locator(".modal:visible"),project);
      assert.deepEqual(editing,needs);
      await btn("取消").click();
      await page.goto(base+"/#/e/"+eid+"/script");
      await btn("制作方法").click();
      const modal=page.locator(".modal:visible");
      const script=modal.locator("summary").filter({hasText:"script · 工作台本地化规则"});
      await script.click();
      assert.equal(await script.locator("..").locator(".field").textContent(),(await fs.readFile(path.join(root,"resources/skills/unified-film/script.md"),"utf8")));
      assert.equal(await modal.getByLabel(/puggy-methods\/script.md采用版本/).count(),project.config_id==="puggy"?1:0);
      assert.equal(await modal.getByText(/系列九格示意/).count(),0);
      await modal.getByRole("button",{name:"关闭",exact:true}).click();
      results.push({profile:project.config_id,eid,needs,detail,editing});
    }
    assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(root,"修复验证_20261108/v7_九格示意与剧本方法浏览器验收.json"),JSON.stringify({results,errors},null,2)+"\n");
    console.log("PASS script sources and grid-only design examples in planning, actual image detail and scoped edits");
  }finally{await browser.close();}
}
main().catch(err=>{console.error(err);process.exitCode=1;});
