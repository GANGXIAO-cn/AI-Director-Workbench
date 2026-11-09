// Inspect the adopted plan, references and exported versions in an isolated episode.
const assert = require("node:assert/strict");
const {chromium} = require("playwright");
const base = "http://127.0.0.1:"+process.env.ADW_TEST_PORT;

async function main() {
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH, headless:true});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:960}});
    page.setDefaultTimeout(6500);
    const query = async (key, args={}) => {
      const response = await page.request.get(base+"/api/q/"+key,{params:{args:JSON.stringify(args)}});
      assert.ok(response.ok(),await response.text());
      return response.json();
    };
    assert.match((await query("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
    const [project] = await query("list_projects");
    const eid = project.recent[0].id;
    const address = base+"/#/e/"+eid+"/";
    const assets = await query("episode_assets",{episode_id:eid});
    const [old, current] = assets;
    const button = name => page.getByRole("button",{name,exact:true});
    const errors = [];
    page.on("pageerror",err=>errors.push(err.message));
    await page.goto(address+"plan");
    const cards = page.locator(".shotcard");
    await cards.first().waitFor();
    assert.deepEqual(await cards.locator("figcaption").allTextContents(),
      ["起幅 · 值班员 v2","本镜引用 · 值班员 v2","本段引用 · 值班员 v2"]);
    for (const card of await cards.all()) {
      const img = card.locator("img");
      assert.equal(await img.getAttribute("src"),"/api/blob/"+current.sha256);
      await img.evaluate(el=>el.decode());
    }
    await page.goto(address+"delivery");
    const hints = page.locator("details").filter({hasText:"起落幅参考待补"});
    await hints.locator("summary").click();
    assert.match(await hints.innerText(),/G01 S01 · 缺落幅；G01 S02 · 缺起幅、落幅；G02 S03 · 缺起幅、落幅/);
    await button("限定试验范围").click();
    await button("G02").click();
    assert.doesNotMatch(await hints.innerText(),/G01/);
    const delivery = project.deliveries[0].id;
    const frozen = await (await page.request.get(base+"/api/delivery-file/"+delivery+"/episode.json")).json();
    assert.deepEqual(frozen.workbench.page.publish.fields.map(([key])=>key),["title","body","tags"]);
    assert.deepEqual(frozen.segments.map(s=>s.references.map(r=>r.asset_id)),[[current.id],[current.id]]);
    await page.goto(address+"assets?tab=needs");
    const table = page.locator("table").filter({hasText:"角色身份覆盖"});
    await table.getByText("值班员 · 已引用 值班员").waitFor();
    await table.getByText("值班员 · 未出镜").waitFor();
    assert.equal(await table.getByText("已引用 1 张",{exact:true}).count(),2);
    await button("本轮出图").click();
    await page.getByText(/提示词已引用 1 张 · 手动已采用 0/).waitFor();
    const candidate = (v) => page.locator(".candidate-layout > div").filter({has:button("放大 值班员 v"+v)});
    await candidate(2).getByText("已引用 · G01、G02",{exact:true}).waitFor();
    assert.equal(await candidate(1).getByText(/已引用/).count(),0);
    await button("并排").click();
    const sizes = await page.locator(".candidate-preview img").evaluateAll(images => images.map(img=>[img.clientWidth,img.clientHeight]));
    assert.equal(sizes.length,2);
    assert.deepEqual(sizes[0],sizes[1]);
    await button("手机尺寸").click();
    const box = await candidate(1).locator(".candidate-preview img").boundingBox();
    assert.ok(Math.abs(box.width / box.height - 9/16)<0.003);
    for (const [label, mark] of [["推荐","recommended"],["补选","backup"]]) {
      await candidate(1).getByRole("button",{name:label,exact:true}).click();
      if (mark==="backup") await button("补选区").click();
      await candidate(1).getByRole("button",{name:"取消"+label,exact:true}).waitFor();
      await page.reload();
      await candidate(1).getByRole("button",{name:"取消"+label,exact:true}).click();
      await page.waitForFunction(async ({eid,id,mark})=>{
        const rsp = await fetch("/api/q/episode_assets?args="+encodeURIComponent(JSON.stringify({episode_id:eid})));
        return !(await rsp.json()).find(a=>a.id===id).marks.includes(mark);
      },{eid,id:old.id,mark});
      await page.reload();
      await button("本轮出图").click();
      await candidate(1).getByRole("button",{name:label,exact:true}).waitFor();
    }
    await page.goto(address+"review");
    await button("对照审查使用的提示词配置").click();
    const method = page.getByRole("checkbox",{name:/09-Q ·/});
    await method.check();
    await button("保存本步配置").click();
    await page.waitForFunction(()=>[...document.querySelectorAll("button")].some(el=>el.textContent==="开始第二遍" && !el.disabled));
    assert.ok(await button("送审材料（第一遍）").isDisabled());
    await page.goto(address+"prompts");
    await button("本步需求与参考").click();
    assert.ok(await page.getByRole("checkbox",{name:/09-Q ·/}).isChecked());
    assert.deepEqual(errors,[]);
    console.log("PASS 镜头/段落引用、起落幅与试验范围、身份覆盖、两版引用与 ZIP、预览和标记回读");
  } finally {
    await browser.close();
  }
}
main().catch(err=>{console.error(err);process.exitCode=1;});
