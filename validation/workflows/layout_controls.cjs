const assert = require("node:assert/strict");
const {chromium} = require("playwright");

async function main() {
  const base = "http://127.0.0.1:" + process.env.ADW_TEST_PORT;
  const browser = await chromium.launch({executablePath:process.env.CHROME_PATH, headless:true});
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(8000);
    const get = async name => {
      const response = await page.request.get(base + "/api/q/" + name);
      assert.ok(response.ok(), await response.text());
      return response.json();
    };
    assert.match((await get("storage_status")).dataDir, /adw-workflows-/);
    const [project] = await get("list_projects");
    const eid = project.recent[0].id;
    const button = name => page.getByRole("button", {name, exact:true});
    const read = loc => loc.first().evaluate(el => {
      const s = getComputedStyle(el);
      return {radius:s.borderRadius, padding:s.padding, background:s.backgroundColor, font:s.fontSize};
    });
    async function fits(selectors) {
      for (const selector of selectors) {
        const size = await page.locator(selector).first().evaluate(el => [el.clientWidth,el.scrollWidth]);
        assert.ok(size[0]>0 && size[1]<=size[0]+1, selector + ": "+size);
      }
    }
    for (const width of [800,1024,1280,1440]) {
      await page.setViewportSize({width,height:900});
      await page.goto(base);
      const home = page.locator(".home-project-grid").first();
      await home.waitFor();
      await fits(["html",".main",".home-project-grid"]);
      await page.goto(base+"/#/p/"+project.id);
      const overview = page.locator(".project-overview");
      await overview.getByText(/必须守住/).waitFor();
      await fits(["html",".main",".project-overview",".project-todos",".project-overview .grid2"]);
      const projectButton = await read(button("＋ 新增一集"));
      const projectTag = await read(overview.locator(".tag"));
      const projectCard = await read(overview.locator(".card"));
      await button("＋ 新增一集").click();
      const projectInput = await read(page.locator(".modal input"));
      await button("取消").click();
      await page.goto(base+"/#/e/"+eid+"/assets?tab=canvas");
      const board = page.locator(".canvas-board");
      await board.waitFor();
      await fits(["html",".ws",".center",".canvas-board",".canvas-tools"]);
      const canvasButton = await read(button("整理布局"));
      let node = board.locator("[data-node]");
      if (!await node.count()) {
        await board.getByRole("button",{name:/＋.*参考图/}).click();
        await node.locator(".canvas-node-select").selectOption({label:"侧光参考 v1"});
      }
      await node.locator(".tag").waitFor();
      const canvasTag = await read(node.locator(".tag"));
      const canvasCard = await read(node);
      const canvasInput = await read(node.locator(".canvas-node-select"));
      assert.deepEqual(projectButton,canvasButton);
      assert.equal(projectTag.radius,canvasTag.radius);
      assert.equal(projectTag.padding,canvasTag.padding);
      assert.equal(projectCard.radius,canvasCard.radius);
      assert.equal(projectCard.background,canvasCard.background);
      assert.equal(canvasCard.padding,"10px");
      assert.equal(projectInput.radius,canvasInput.radius);
      assert.equal(projectInput.padding,canvasInput.padding);
      assert.equal(projectInput.background,canvasInput.background);
      await button("整理布局").click();
      console.log("PASS",width,"project/canvas",JSON.stringify({projectButton,projectTag,projectCard,projectInput,canvasTag,canvasCard,canvasInput}));
    }
  } finally { await browser.close(); }
}
main().catch(e=>{console.error(e);process.exitCode=1;});
