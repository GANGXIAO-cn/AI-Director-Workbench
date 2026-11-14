const assert = require("node:assert/strict");
const { chromium } = require("playwright");

(async () => {
  const base="http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH});
  try {
    const page=await browser.newPage({viewport:{width:1500,height:1000}});
    const query=async(name,args={})=>(await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}})).json();
    assert.match((await query("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
    const [project]=await query("list_projects");
    const eid=project.recent[0].id;
    await page.goto(base+"/#/e/"+eid+"/characters");
    await page.getByRole("button",{name:"成果文档",exact:true}).click();
    await page.getByRole("button",{name:"导演决定 0",exact:true}).click();
    const drawer=page.getByRole("dialog",{name:"导演决定清单"});
    await drawer.getByRole("button",{name:"新增决定"}).click();
    await drawer.getByLabel("导演原话").fill("保留门锁的声音");
    await drawer.getByRole("button",{name:"保存决定"}).click();
    await drawer.getByText("D01", {exact:true}).waitFor();
    const [d]=await query("director_decisions",{episode_id:eid});
    assert.equal(d.text,"保留门锁的声音");
    assert.equal(d.origin_stage,"characters");
    await drawer.getByRole("button",{name:"撤销",exact:true}).click();
    await drawer.locator("details summary").click();
    await drawer.getByText("D01",{exact:true}).waitFor();
    assert.equal((await query("director_decisions",{episode_id:eid}))[0].state,"revoked");
    await page.keyboard.press("Escape");
    await page.goto(base+"/#/e/"+eid+"/story");
    const triggers=page.locator(".composer:visible .model-trigger");
    await triggers.nth(1).waitFor();
    assert.equal(await triggers.count(),2);
    for(const trigger of await triggers.all()) {
      assert((await trigger.locator("span").innerText()).trim());
    }
    const message=page.locator(".msg.user:visible").filter({has:page.getByRole("button",{name:"记为决定"})}).first();
    await message.getByRole("button",{name:"记为决定"}).click();
    await page.locator(".msg.user:visible").getByText("已记为决定 D02").first().waitFor();
    assert.equal((await query("director_decisions",{episode_id:eid})).filter(d=>d.state==="active").length,1);
    console.log("PASS decision drawer creates and revokes numbered decision");
  } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
