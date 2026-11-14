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
    await page.goto(base+"/#/e/"+eid+"/plan");
    await page.getByRole("button",{name:"成果文档",exact:true}).click();
    const panel = page.locator(".stage-inputs");
    await panel.locator("summary").click();
    const view = await query("episode_view",{episode_id:eid});
    const version = view.artifacts.find(a=>a.stage==="plan").versions.at(-1);
    const inputs = await query("version_inputs",{version_id:version.id});
    for (const group of inputs.groups) {
      const label=group.key==="decisions" && !group.items.every(i=>i.role==="director_decisions")
        ? group.items.some(i=>i.role==="director_decisions")?"导演要求与决定":"导演要求" : group.label;
      await panel.getByRole("heading",{name:label,exact:true}).waitFor();
      for(const item of group.items) await panel.getByRole("button",{name:item.name,exact:true}).waitFor();
    }
    const source = inputs.groups.find(g=>g.key==="upstream").items.find(i=>i.role==="adopted_script");
    await panel.getByRole("button",{name:source.name,exact:true}).click();
    const drawer = page.getByRole("dialog",{name:source.name,exact:true});
    await drawer.getByText("工作台采用版本："+source.upstreamVersion.id,{exact:false}).first().waitFor();
    const box = await drawer.boundingBox();
    assert.equal(box.x+box.width,page.viewportSize().width);
    await page.keyboard.press("Escape");
    await drawer.waitFor({state:"hidden"});
    console.log("PASS stage input groups and frozen script text");
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
