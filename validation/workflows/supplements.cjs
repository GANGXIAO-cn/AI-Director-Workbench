const assert = require("node:assert/strict");
const {chromium} = require("playwright");

(async () => {
  const base = "http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const browser = await chromium.launch({headless:true, executablePath:process.env.CHROME_PATH});
  try {
    const page = await browser.newPage({viewport:{width:1500,height:1000}});
    const query = async (name,args={}) => (await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}})).json();
    assert.match((await query("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
    const [project] = await query("list_projects");
    const eid = project.recent[0].id;
    await page.goto(base+"/#/e/"+eid+"/space");
    await page.getByRole("button",{name:"成果文档",exact:true}).click();
    const section=page.locator("section").filter({has:page.getByRole("heading",{name:"制作补充",exact:true})}).last();
    await section.getByText("查看制作补充 · 1 条",{exact:true}).click();
    await section.getByText("手机在左侧口袋",{exact:true}).waitFor();
    await section.getByRole("button",{name:"全部确认"}).click();
    await section.getByText(/无出处.*已确认/).waitFor();
    assert.equal((await query("production_supplements",{episode_id:eid}))[0].state,"confirmed");
    console.log("PASS director inspected and confirmed proposed supplement");
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
