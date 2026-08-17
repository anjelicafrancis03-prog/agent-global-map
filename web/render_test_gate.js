// render_test_gate.js — 大地图渲染实测门闸（镜像前第三门，唯一常驻渲染门闸）
// 用法: node render_test_gate.js <map-build.html 绝对路径>
// 通过: exit 0；失败: exit 1（阻断发布）。依赖: 同目录 node_modules/puppeteer-core + 系统 Chrome。
const path = require("path");
const puppeteer = require("puppeteer-core");

const CHROME = "C:/Program Files/Google/Chrome/Application/chrome.exe";
const MANIFEST = "F:/codex/tools/agent-system-map/catalog/panel-manifest-v0.json";
const EXPECTED43 = "F:/codex/catalog/expected-placement-target-43.json";
// E-26 批次1: EXACT_PANELS 不再硬编码，从 manifest 推导（唯一权威）。
// 向后兼容：manifest 缺失/解析失败时抛错终止（阻断发布，暴露权威缺失而非静默放行）。
const manifestRaw = require("fs").readFileSync(MANIFEST, "utf8");
const manifest = JSON.parse(manifestRaw);
const EXACT_PANELS = (manifest.panels || []).map((p) => p.panel_id);

(async () => {
  const target = process.argv[2];
  if (!target) { console.error("usage: node render_test_gate.js <map-build.html>"); process.exit(2); }
  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: "new",
    args: ["--no-sandbox", "--disable-gpu"],
  });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const fileUrl = "file:///" + path.resolve(target).replace(/\\/g, "/");
  await page.goto(fileUrl, { waitUntil: "networkidle0", timeout: 60000 });
  await new Promise((r) => setTimeout(r, 1500));
  const stats = await page.evaluate(() => {
    const nav = document.querySelector("nav");
    const csp = document.querySelector("meta[http-equiv='Content-Security-Policy']");
    return {
      cards: document.querySelectorAll(".asset-card").length,
      panels: document.querySelectorAll("section.panel").length,
      navDisplay: nav ? getComputedStyle(nav).display : "MISSING",
      taskRows: document.querySelectorAll("#task-table tbody tr").length,
      hasCsp: Boolean(csp && csp.getAttribute("content") && csp.getAttribute("content").includes("default-src 'self'")),
      // codex-ygjz v5.5 门闸 v3：DOM 侧采集（document 只在 evaluate 内有效）
      panelIds: [...document.querySelectorAll("section.panel")].map((x) => x.id).sort(),
      aidList: [...document.querySelectorAll(".asset-card")].map((c) => c.dataset.aid).filter(Boolean).sort(),
      rendererKeys: Object.keys(window.RENDERER_REGISTRY || {}),
      rendererBad: Object.entries(window.RENDERER_REGISTRY || {}).filter(([k, f]) => typeof f !== "function" || f() === null).map(([k]) => k),
    };
  });
  await browser.close();

  const fails = [];
  if (errors.length) fails.push("JS_ERRORS: " + errors.join(" | "));
  if (!stats.hasCsp) fails.push("CSP meta tag missing or invalid in <head>");
  // v3-1 精确面板集合
  if (JSON.stringify(stats.panelIds) !== JSON.stringify([...EXACT_PANELS].sort())) fails.push("panel-set mismatch: [" + stats.panelIds.join(",") + "]");
  // v3-2 asset_id 集合完全相等（expected-43）
  try {
    const exp = JSON.parse(require("fs").readFileSync(EXPECTED43, "utf8"));
    const want = exp.records.map((r) => r.asset_id).sort();
    if (JSON.stringify(stats.aidList) !== JSON.stringify(want)) fails.push("asset_id set mismatch: got=" + stats.aidList.length + " want=" + want.length + " missing=" + want.filter((x) => !stats.aidList.includes(x)).slice(0, 5).join(","));
  } catch (e) { fails.push("expected-43 unreadable: " + e.message); }
  // v3-11 renderer 注册门（manifest renderer ⊆ RENDERER_REGISTRY 可解析函数）
  try {
    const man = JSON.parse(require("fs").readFileSync(MANIFEST, "utf8"));
    const need = [...new Set(man.panels.flatMap((p) => p.sections.map((x) => x.renderer)))];
    const miss = need.filter((n) => !stats.rendererKeys.includes(n) || stats.rendererBad.includes(n));
    if (miss.length) fails.push("renderer unresolved: " + miss.join(","));
  } catch (e) { fails.push("renderer-gate unreadable: " + e.message); }
  
  if (stats.navDisplay !== "flex") fails.push("navDisplay=" + stats.navDisplay);
  if (stats.taskRows < 1) fails.push("taskRows=0");

  console.log("render-gate stats:", JSON.stringify(stats));
  if (fails.length) {
    console.error("render-gate FAIL — 阻断镜像，先修页面再发布:");
    fails.forEach((f) => console.error("  - " + f));
    process.exit(1);
  }
  // codex-ygjz：aidList 落 generation.json 便于事后审计（双审建议）
  try {
    const fs2 = require("fs");
    const genDir = require("path").dirname(target);
    fs2.writeFileSync(require("path").join(genDir, "render-gate-aidlist.json"), JSON.stringify({ generatedAt: new Date().toISOString(), count: stats.aidList.length, aidList: stats.aidList }, null, 1));
  } catch (e) { /* 审计文件失败不阻断 */ }
  console.log("render-gate OK");
  process.exit(0);
})().catch((e) => { console.error("render-gate ERROR:", e.message); process.exit(1); });
