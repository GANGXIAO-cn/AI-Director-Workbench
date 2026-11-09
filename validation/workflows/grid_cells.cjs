const assert = require("node:assert/strict");
const {chromium} = require("playwright");

async function main() {
  const base = "http://127.0.0.1:" + process.env.ADW_TEST_PORT;
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH,headless:true});
  try {
    const page = await browser.newPage();
    const button = name => page.getByRole("button",{name,exact:true});
    const get = async (name,args={}) => {
      const result = await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}});
      assert.ok(result.ok(),await result.text());
      return result.json();
    };
    assert.match((await get("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
    const {token} = await (await page.request.get(base+"/api/session")).json();
    const command = async (name,data,status=200) => {
      const result = await page.request.post(base+"/api/cmd/"+name,{headers:{"x-adw-token":token},data});
      assert.equal(result.status(),status,await result.text());
      return result.json();
    };
    const [project] = await get("list_projects");
    const eid = project.recent[0].id;
    const [image] = await get("episode_assets",{episode_id:eid});
    const plan = (await get("episode_view",{episode_id:eid})).artifacts.find(a=>a.stage==="plan");
    const original = plan.versions.find(v=>v.id===plan.adopted_version_id);
    const openGrid = async () => {
      await page.goto(base+"/#/e/"+eid+"/assets");
      await button("放大 站台九格 v1").click();
      await page.getByRole("img",{name:"第 9 格",exact:true}).waitFor();
    };
    await openGrid();
    await page.getByText("列车进站",{exact:true}).waitFor();
    assert.equal(await page.getByRole("img",{name:/^第 \d 格$/}).count(),9);
    await button("设置第 3 格镜头与时刻").click();
    await page.getByLabel(/制作方案 .*镜头/).selectOption("S02");
    await page.getByLabel("距镜头开始（秒）").fill("1.250");
    await button("保存关联").click();
    await page.getByText("段内 4.250 秒",{exact:true}).waitFor();
    const payload = {episode_id:eid,version_id:image.id,plan_version_id:original.id,shot_id:"S02",offset_ms:1000};
    await command("set_grid_cell",{...payload,cell:3,expected_revision:0},409);
    await command("set_grid_cell",{...payload,cell:4,expected_revision:0,offset_ms:5001},422);
    await command("set_grid_cell",{...payload,cell:4,expected_revision:0,episode_id:"other"},422);
    const batch = await command("redraw_cells",{episode_id:eid,base_version_id:image.id,cells:[3],
      reference_version_ids:[],prompt:"站台人物回头",provider:"fake-image",size:"64x64",budget_cents:50});
    await command("set_grid_cell",{...payload,cell:3,expected_revision:1});
    await command("authorize_batch",{batch_id:batch.batchId,summary_hash:batch.summaryHash});
    await command("execute_batch",{batch_id:batch.batchId});
    const derived = (await get("episode_assets",{episode_id:eid})).find(a=>a.source_version_id===image.id);
    const [frozen] = await get("grid_bindings",{episode_id:eid,version_id:derived.id});
    assert.equal(frozen.offset_ms,1250);
    const updated = structuredClone(original.content);
    updated.shots[0].duration_ms = 4000;
    const draft = await command("save_manual",{artifact_id:plan.id,base_version_id:original.id,content:updated});
    await command("adopt",{artifact_id:plan.id,version_id:draft.versionId,expected_adopted_version_id:original.id});
    await page.reload();
    await button("放大 站台九格 v1").click();
    await page.getByText("段内 4.000 秒",{exact:true}).waitFor();
    await page.getByRole("link",{name:/R\d+ · G01 · S02/}).click();
    await page.locator(".shotcard.sel").filter({hasText:"S02"}).waitFor();
    assert.ok(page.url().includes(original.id));
    console.log("PASS 九格裁切、镜头与段内时刻、来源稿跳转、并发保护和修格快照");
  } finally { await browser.close(); }
}
main().catch(error=>{console.error(error);process.exitCode=1;});
