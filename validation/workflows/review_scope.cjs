const assert = require("node:assert/strict");
const {chromium} = require("playwright");

(async () => {
  const base = "http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const browser = await chromium.launch({headless:true,executablePath:process.env.CHROME_PATH});
  try {
    const page = await browser.newPage({viewport:{width:1500,height:1000}});
    const query = async name => (await page.request.get(base+"/api/q/"+name)).json();
    assert.match((await query("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
    const [project] = await query("list_projects");
    await page.goto(base+"/#/e/"+project.recent[0].id+"/review");
    await page.getByRole("button",{name:"成果文档",exact:true}).click();
    const summary = page.getByText("第二遍送审文件",{exact:true}).first();
    await summary.click();
    const group = summary.locator("..");
    await group.getByRole("button",{name:"开工原文.txt",exact:true}).waitFor();
    await group.getByRole("button",{name:"G02-接点原文.json",exact:true}).click();
    await page.getByRole("dialog").getByText(/阿福发现抽屉没关/).waitFor();
    await page.keyboard.press("Escape");
    const first = page.getByText("第一遍送审文件",{exact:true}).first();
    await first.click();
    await first.locator("..").getByRole("button",{name:"G01.txt",exact:true}).waitFor();
    assert.equal(await first.locator("..").getByRole("button",{name:"开工原文.txt"}).count(),0);
    console.log("PASS comparison reads adjacent scene and requirements; cold read is isolated");
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
