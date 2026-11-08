const assert = require("node:assert/strict");
const path = require("node:path");
const {chromium} = require("playwright");
async function main() {
  const base="http://127.0.0.1:"+process.env.ADW_TEST_PORT;
  const browser=await chromium.launch({executablePath:process.env.CHROME_PATH});
  try {
    const page=await browser.newPage({viewport:{width:1440,height:960}});
    const errors=[];
    page.on("pageerror",e=>errors.push(e.message));
    await page.goto(base+"/#/sys/tasks");
    const found = await (await page.request.get(base+"/api/q/task_records")).json();
    const a=found.find(t=>t.runs.some(r=>r.id==="569b1063-c2d2-47fe-8059-1cde2dd55c6e"));
    const b=found.find(t=>t.runs.some(r=>r.id==="c0368046-4d90-49eb-8c2b-0ba36cebb0fb"));
    assert.equal(a.state,"failed");
    assert.equal(b.state,"awaiting_decision");
    const row = page.getByRole("row").filter({hasText:a.display_code}).filter({has:page.getByText("失败",{exact:true})});
    await row.click();
    await page.getByText(/^adaptation 的值不能按阶段契约解析/).waitFor();
    await page.getByText("已收到回包，未通过入库校验",{exact:true}).waitFor();
    await page.screenshot({path:path.resolve(__dirname,"../../修复验证_20261108/40_历史运行中断与回包字段原因.png")});
    await page.reload();
    await row.click();
    await page.getByText("已收到回包，未通过入库校验",{exact:true}).waitFor();
    assert.deepEqual(errors,[]);
    console.log("PASS real historical A failure, field diagnostic, B status and reload");
  } finally {await browser.close();}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
