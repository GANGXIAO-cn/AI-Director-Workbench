const assert = require("node:assert/strict");
const { chromium } = require("playwright");
(async () => {
  const base = "http://127.0.0.1:" + process.env.ADW_TEST_PORT;
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH});
  try {
    const page = await browser.newPage();
    const query = async name => (await page.request.get(base+"/api/q/"+name)).json();
    assert.match((await query("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
    const [project] = await query("list_projects");
    const url = base+"/#/e/"+project.recent[0].id+"/story";
    await page.goto(url);
    await page.getByRole("link",{name:"去项目设置同步"}).click();
    await page.getByText("本项目有 5 份项目方法尚未同步").filter({visible:true}).first().waitFor();
    await page.getByRole("button",{name:"同步项目方法",exact:true}).click();
    await page.getByText("已备份并同步项目方法。").waitFor();
    await page.goto(url);
    await page.reload();
    await page.getByRole("button",{name:"本集采用项目当前的方法"}).click();
    await page.getByText("本步之后发起的任务将使用项目当前的方法。").waitFor();
  } finally { await browser.close(); }
})().catch(error=>{console.error(error);process.exitCode=1;});
