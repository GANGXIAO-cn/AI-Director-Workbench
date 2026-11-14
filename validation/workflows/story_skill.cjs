const assert = require("node:assert/strict");
const { chromium } = require("playwright");

async function run() {
  const base = "http://127.0.0.1:" + process.env.ADW_TEST_PORT;
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH, headless: true });
  const page = await browser.newPage();
  try {
    page.setDefaultTimeout(9000);
    const query = async (name, args = {}) => {
      const res = await page.request.get(base + "/api/q/" + name, { params: { args: JSON.stringify(args) } });
      assert.ok(res.ok(), await res.text());
      return res.json();
    };
    assert.match((await query("storage_status")).dataDir, /\/adw-workflows-[^/]+\/data$/);
    const [project] = await query("list_projects");
    await page.goto(base + "/#/p/" + project.id + "/topics");
    const button = name => page.getByRole("button", { name, exact: true });
    const card = page.locator("details.card").filter({ hasText: "加入本项目候选" }).first();
    await card.locator("summary").click();
    await card.getByRole("button").click();
    await button("从本卡开工").click();
    await button("开工并开始创作").click();
    await page.waitForURL(/\/story$/);
    const eid = page.url().split("/").at(-2);
    const view = () => query("episode_view", { episode_id: eid });
    await page.getByText("稿件与历史版本",{exact:true}).evaluate(el=>el.closest("details").open=true);
    await button("稿件与历史版本").click();
    await button("✎ 编辑").click();
    const original = (await view()).artifacts[0].versions[0];
    const run = (await query("run_manifest", { run_id: original.source_run_id })).manifest;
    assert.equal(run.methodPack.id, "unified-film@draft");
    assert.ok(run.inputs.some(i => i.role === "method" && i.name === "story.md"));
    assert.ok(run.inputs.some(i => i.role === "project_settings"));
    assert.ok(run.inputs.some(i => i.role === "project_method" && i.name.includes("story.md")));
    const edited = original.content.synopsis + "\nMILO 把最后一张票还给了 LEO。";
    await page.locator(".center textarea").first().fill(edited);
    await button("保存为手稿").click();
    await page.getByText("MILO 把最后一张票还给了 LEO。", { exact: true }).first().waitFor();
    const preview=page.locator(".modal:visible").last();
    await preview.getByRole("button",{name:"发起独立审查",exact:true}).click();
    await preview.getByRole("button",{name:"只采用",exact:true}).click();
    await page.waitForFunction(async id=>{const a=(await (await fetch("/api/q/episode_view?args="+encodeURIComponent(JSON.stringify({episode_id:id})))).json()).artifacts[0];return a.adopted_version_id===a.versions[1].id;},eid);
    await page.goto(base+"/#/e/"+eid+"/story");
    const art = (await view()).artifacts[0];
    assert.equal(art.versions.length, 2);
    assert.equal(art.adopted_version_id, art.versions[1].id);
    const link = page.getByRole("link", { name: "下载故事梗概" });
    const markdown = await page.request.get(base + await link.getAttribute("href"));
    assert.ok((await markdown.text()).includes(edited));
    console.log("PUGGY card → unified project methods → edited R2 → independent review → adopted → reopened Markdown verified (simulated executors).");
  } catch (error) {
    console.error(page.url(), (await page.locator("body").innerText()).slice(-2500));
    throw error;
  } finally {
    await browser.close();
  }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
