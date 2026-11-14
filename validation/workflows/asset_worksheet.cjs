const assert = require("node:assert/strict");
const {chromium} = require("playwright");
(async()=>{
  const base="http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:940}});
    page.setDefaultTimeout(9000);
    const query=async(name,args={})=>(await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}})).json();
    assert.match((await query("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
    const [project]=await query("list_projects");
    const eid=project.recent[0].id;
    const view=await query("episode_view",{episode_id:eid});
    const version=view.artifacts.find(a=>a.stage==="asset_design").versions.at(-1).id;
    await page.goto(base+"/#/e/"+eid+"/asset_design?v="+version);
    await page.getByRole("button",{name:"成果文档",exact:true}).click();
    await page.getByRole("button",{name:"采用清单并去备图"}).click();
    await page.getByRole("heading",{name:"备图工作单"}).waitFor();
    for(const key of ["room","bell"]) {
      const prepared=page.waitForResponse(r=>r.url().endsWith("/api/cmd/prepare_asset_batch"));
      await page.locator('[data-need="'+key+'"]').getByRole("button",{name:"出新图",exact:true}).click();
      const response=await prepared;assert.ok(response.ok(),await response.text());
      await page.getByRole("button",{name:"确认授权 1 张"}).waitFor();
      await page.getByRole("button",{name:"关闭",exact:true}).last().click();
    }
    await page.getByRole("button",{name:"批量批准并生成 2 张",exact:true}).click();
    for(const key of ["room","bell"]) await page.locator('[data-need="'+key+'"]').getByRole("button",{name:"采用",exact:true}).click();
    await page.getByRole("link",{name:"进入 08 表演"}).waitFor();
    const batches=await query("episode_batches",{episode_id:eid});
    assert.equal(batches.reduce((n,b)=>n+b.count,0),2);
    assert.ok(batches.every(b=>b.authorization && b.state==="completed"));
    const sheet=await query("asset_worksheet",{episode_id:eid});
    assert(sheet.complete);
    assert((await query("episode_view",{episode_id:eid})).productionStatus.materialsConfirmed);
    console.log("PASS 从清单采用到两张图归位、逐项采用和 07 完成");
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
