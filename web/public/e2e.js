// UI driver for walkthroughs in the in-app browser: acts only through visible UI elements.
(() => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();
  const CLICKABLE = "button,a,.pill,.tab,.para,.shotcard,.opt,label.btn,.item,.card,[role=button],div[style*='cursor: pointer'],span[style*='cursor: pointer']";
  const log = [];
  async function waitFor(pred, timeout = 8000, what = "") {
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) {
      const v = typeof pred === "string" ? (document.body.innerText.includes(pred) ? pred : null) : pred();
      if (v) return v;
      await sleep(120);
    }
    throw new Error("等待超时：" + (typeof pred === "string" ? pred : what));
  }
  function find(text, { sel = CLICKABLE, exact = false, within = document, nth = 0 } = {}) {
    const all = [...within.querySelectorAll(sel)].filter(visible).filter((el) => exact ? norm(el.innerText) === text : norm(el.innerText).includes(text));
    // innermost match first
    const inner = all.filter((el) => !all.some((o) => o !== el && el.contains(o)));
    return inner[nth];
  }
  async function click(text, opts = {}) {
    const el = await waitFor(() => find(text, opts), opts.timeout ?? 8000, "点击 " + text);
    el.scrollIntoView({ block: "center" });
    el.click();
    log.push("点击「" + text + "」");
    await sleep(opts.after ?? 350);
    return el;
  }
  function setValue(el, value) {
    const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : el.tagName === "SELECT" ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
    el.dispatchEvent(new Event(el.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  }
  async function type(placeholder, value, { nth = 0 } = {}) {
    const el = await waitFor(() => [...document.querySelectorAll("input,textarea")].filter(visible).filter((x) => (x.placeholder || "").includes(placeholder))[nth], 8000, "输入框 " + placeholder);
    setValue(el, value);
    log.push("在「" + placeholder + "」输入：" + value.slice(0, 40));
    await sleep(200);
    return el;
  }
  async function choose(optionText, { nth = 0 } = {}) {
    const sel = await waitFor(() => [...document.querySelectorAll("select")].filter(visible).filter((s) => [...s.options].some((o) => o.text.includes(optionText)))[nth], 8000, "下拉 " + optionText);
    const opt = [...sel.options].find((o) => o.text.includes(optionText));
    setValue(sel, opt.value);
    log.push("选择「" + opt.text + "」");
    await sleep(300);
  }
  async function upload(labelText, file) {
    const holder = await waitFor(() => [...document.querySelectorAll("label")].filter(visible).find((l) => norm(l.innerText).includes(labelText) && l.querySelector("input[type=file]")), 8000, "文件框 " + labelText);
    const input = holder.querySelector("input[type=file]");
    const dt = new DataTransfer(); dt.items.add(file); input.files = dt.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
    log.push("文件选择框（DataTransfer 注入）：" + file.name);
    await sleep(600);
  }
  async function go(hash) { location.hash = hash; log.push("打开 " + hash); await sleep(900); }
  const has = (t) => document.body.innerText.includes(t);
  function expect(cond, msg) { if (!cond) throw new Error("断言失败：" + msg); log.push("✓ " + msg); }
  async function api(name, args = {}) { return (await fetch(`/api/q/${name}?args=${encodeURIComponent(JSON.stringify(args))}`)).json(); }
  async function run(fn, id) {
    log.length = 0;
    let res;
    try { await fn(); res = { ok: true, log: [...log], hash: location.hash }; } catch (e) { res = { ok: false, error: String(e.message || e), log: [...log], hash: location.hash }; }
    if (id) { const all = JSON.parse(localStorage.getItem("e2e") || "{}"); all[id] = res; localStorage.setItem("e2e", JSON.stringify(all)); }
    return res;
  }
  window.E2E = { sleep, waitFor, find, click, type, choose, upload, go, has, expect, api, run, log, setValue };
})();
// Full production pipeline through the UI (used to prepare delivered episodes for later scenarios).
window.E2E.pipeline = async function (eid, { idea = "第二夜", draft = true, from = "story" } = {}) {
  const T = window.E2E;
  const png = async (w, h, color, name) => { const c = document.createElement("canvas"); c.width = w; c.height = h; const g = c.getContext("2d"); g.fillStyle = color; g.fillRect(0, 0, w, h); g.fillStyle = "#e8913a"; g.beginPath(); g.arc(w / 2, h * 0.6, w / 5, 0, 7); g.fill(); return new File([await new Promise((r) => c.toBlob(r, "image/png"))], name, { type: "image/png" }); };
  if (from === "review") { await T.go("#/e/" + eid + "/review"); await T.waitFor("两遍独审"); }
  else { await T.go("#/e/" + eid + "/story"); await T.waitFor("故事稿"); }
  if (from === "story") {
  if (draft) { await T.type("输入你的想法", "写本集故事稿"); await T.click("↑", { sel: ".composer button" }); await T.waitFor("已生成草稿 R01", 15000); }
  await T.click("发起独立审查", { sel: ".actionbar button", after: 1500 }); await T.waitFor("原结论 通过", 15000);
  await T.click("采用并继续", { sel: ".actionbar button", after: 1200 });
  await T.click("按采用的故事起草剧本", { after: 1200 }); await T.waitFor("场01");
  await T.click("采用并继续", { sel: ".actionbar button", after: 1200 });
  await T.click("按剧本设计镜头与生成段", { after: 1200 }); await T.waitFor("K01 ");
  await T.click("采用并继续", { sel: ".actionbar button", after: 1200 });
  await T.click("素材与选图", { sel: ".catalog .item" });
  await T.upload("上传", await png(96, 64, "#2b3a55", "纸箱火箭外观.png")); await T.waitFor("纸箱火箭外观", 8000);
  await T.click("提示词", { sel: ".catalog .item" });
  await T.click("从方案起草提示词", { after: 1200 }); await T.waitFor("图片1");
  await T.click("采用并继续", { sel: ".actionbar button", after: 1200 });
  await T.click("两遍独审", { sel: ".catalog .item", after: 900 });
  }
  await T.click("送审材料（第一遍）", { after: 1500 });
  await T.waitFor(() => !/第一遍 · 冷读\n待审/.test(document.body.innerText), 15000, "第一遍完成");
  await T.click("开始第二遍", { after: 1500 });
  await T.waitFor(() => /待判断 \/ 待审\n0 段/.test(document.body.innerText), 15000, "两遍完成");
  await T.go("#/e/" + eid + "/publish"); await T.waitFor("发布包");
  const L = (t) => [...document.querySelectorAll("label")].find((l) => l.innerText.startsWith(t))?.querySelector("input,textarea");
  await T.waitFor(() => L("Exclude Styles"), 8000, "读取项目的扩展发布字段");
  T.setValue(L("发布标题"), "Second Night"); T.setValue(L("正文第一行"), "The parrot learns the till sound."); T.setValue(L("Suno Title"), "Till Beep"); T.setValue(L("Style of Music"), "lofi, 92 BPM"); T.setValue(L("Exclude Styles"), "vocals");
  await T.click("保存为新版本", { after: 1200 }); await T.waitFor("Second Night");
  await T.click("采用并继续", { sel: ".actionbar button", after: 1500 });
  await T.waitFor("核验通过", 10000);
  await T.click("生成图文交付", { after: 2000 }); await T.waitFor("打开图文页", 8000);
  T.log.push("（完整流水线：故事→独审→剧本→镜头表→上传素材→提示词→两遍独审→发布包→整集交付 均在界面完成）");
};
