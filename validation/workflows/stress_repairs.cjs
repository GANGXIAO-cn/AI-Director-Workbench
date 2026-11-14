const assert=require("node:assert/strict");
const {chromium}=require("playwright");
(async()=>{
 const base="http://127.0.0.1:"+process.env.ADW_TEST_PORT;
 const browser=await chromium.launch({executablePath:process.env.CHROME_PATH});
 try{
   const page=await browser.newPage({viewport:{width:1440,height:1000}});
   page.setDefaultTimeout(9000);
   const q=async(name,args={})=>(await page.request.get(base+"/api/q/"+name,{params:{args:JSON.stringify(args)}})).json();
   assert.match((await q("storage_status")).dataDir,/\/adw-workflows-[^/]+\/data$/);
   const [project]=await q("list_projects");const eid=project.recent[0].id;
   await page.goto(base+"/#/e/"+eid+"/stress");
   await page.locator(".repair-pill").click(); // 实测 138：返修与核对默认收在版本行的按钮里
   for(const id of ["I01","I02"])await page.getByLabel(id+" 返修方案").selectOption("0");
   await page.getByRole("button",{name:"按所选方案建立返修单"}).click();
   await page.reload();
   await page.locator(".repair-pill").click();
   await page.getByRole("button",{name:/采用 R\d+ 并继续/}).click();
   if(!await page.locator(".repair-progress").isVisible())await page.locator(".repair-pill").click();
   await page.locator(".repair-progress summary").filter({hasText:"已完成"}).first().click();
   const work=await q("stress_worklist",{episode_id:eid});
   assert.equal(work.plans[0].state,"done");
   assert.deepEqual(work.blockers,[]);
   console.log("PASS 06 关联问题一单返修、重新打开、导演采用、下游自动核对与复核");
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
