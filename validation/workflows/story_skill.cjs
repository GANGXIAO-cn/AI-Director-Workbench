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
    await button("开工，进入故事稿").click();
    await page.waitForURL(/\/story$/);
    const eid = page.url().split("/").at(-2);
    const view = () => query("episode_view", { episode_id: eid });
    const composer = page.getByPlaceholder("输入你的想法，继续完善这一集");
    await composer.fill("按所选原卡写故事稿。");
    await composer.press("Control+Enter");
    await button("✎ 编辑").click();
    const original = (await view()).artifacts[0].versions[0];
    const run = (await query("run_manifest", { run_id: original.source_run_id })).manifest;
    assert.equal(run.methodPack.id, "short-film@v7");
    assert.ok(run.inputs.some(i => i.name === "story-standard.md"));
    assert.ok(run.inputs.some(i => i.role === "project_settings"));
    const edited = original.content.synopsis + "\nMILO 把最后一张票还给了 LEO。";
    await page.locator(".center textarea").first().fill(edited);
    await button("保存为手稿").click();
    await page.getByText("MILO 把最后一张票还给了 LEO。", { exact: true }).first().waitFor();
    await button("发起独立审查").click();
    await button("只采用").click();
    await page.getByText(/R0?2 已采用/i).first().waitFor();
    await page.reload();
    const art = (await view()).artifacts[0];
    assert.equal(art.versions.length, 2);
    assert.equal(art.adopted_version_id, art.versions[1].id);
    const link = page.getByRole("link", { name: "下载本版故事稿" });
    const markdown = await page.request.get(base + await link.getAttribute("href"));
    assert.ok((await markdown.text()).includes(edited));
    console.log("PUGGY card → v7 project methods → edited R2 → independent review → adopted → reopened Markdown verified (simulated executors).");
  } catch (error) {
    console.error(page.url(), (await page.locator("body").innerText()).slice(-2500));
    throw error;
  } finally {
    await browser.close();
  }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
