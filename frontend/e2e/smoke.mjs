// End-to-end smoke test of the built viewer against a running `vizcontrol serve` (see e2e/smoke.sh).
// Usage: node e2e/smoke.mjs <base-url>; CHROME_PATH overrides the browser.
import puppeteer from "puppeteer-core";

const base = process.argv[2] ?? "http://127.0.0.1:7171/";
const browser = await puppeteer.launch({
  executablePath: process.env.CHROME_PATH ?? "/usr/bin/google-chrome",
  headless: true,
  args: ["--no-sandbox"],
});
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 900 });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const text = (sel) => page.$eval(sel, (e) => e.textContent);
let failures = 0;
async function check(name, fn) {
  try {
    await fn();
    console.log(`ok   ${name}`);
  } catch (e) {
    failures++;
    console.log(`FAIL ${name}: ${e.message}`);
  }
}
const expect = (cond, msg) => {
  if (!cond) throw new Error(msg);
};
const nodeCenter = (label) =>
  page.$$eval(".react-flow__node", (ns, label) => {
    const n = ns.find((n) => n.textContent.includes(label));
    if (!n) return null;
    const r = n.getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  }, label);

await check("a passing run opens on the stage overview", async () => {
  await page.goto(`${base}#run=basic.vizc`, { waitUntil: "networkidle0" });
  await wait(800);
  expect((await text(".level")) === "L1 · Overview", `level is ${await text(".level")}`);
  expect((await page.$$(".stage")).length >= 5, "fewer than 5 stages");
  expect((await page.$$(".stage.main")).length >= 3, "main path not highlighted");
});

await check("the overview copies as a Mermaid flowchart", async () => {
  await browser.defaultBrowserContext().overridePermissions(new URL(base).origin, ["clipboard-read", "clipboard-sanitized-write"]);
  await page.click(".copy-mermaid");
  await wait(400);
  const clip = await page.evaluate(() => navigator.clipboard.readText());
  expect(clip.startsWith("flowchart LR") && clip.includes("app.services"), `clipboard: ${clip.slice(0, 60)}`);
});

await check("double-clicking a stage opens it into modules, classes and functions (level 2)", async () => {
  const c = await nodeCenter("app.services");
  expect(c, "no app.services stage");
  await page.mouse.click(c.x, c.y, { count: 2 });
  await wait(1500);
  expect((await text(".level")) === "L2 · Components", `level is ${await text(".level")}`);
  expect((await text(".inspector .title-lg")) === "app.services", "inspector shows another stage");
  expect((await text(".box-node.class")).includes("OrderService"), "no OrderService class box");
});

await check("clicking a function opens its first call (level 3)", async () => {
  const c = await nodeCenter("place_order");
  expect(c, "no place_order function");
  await page.mouse.click(c.x, c.y);
  await wait(1200);
  expect((await text(".level")) === "L3 · Function", `level is ${await text(".level")}`);
  expect((await text(".inspector .title-lg")) === "OrderService.place_order", "inspector shows another call");
});

await check("clicking an argument opens its value (level 4)", async () => {
  await page.$$eval(".inspector .binding", (bs) => bs[0].click());
  await wait(800);
  expect((await text(".level")) === "L4 · Value", `level is ${await text(".level")}`);
  expect((await page.$$(".vtree .row")).length >= 1, "no value tree");
});

await check("browser back and forward walk the levels", async () => {
  const level = async () => (await text(".level"));
  const steps = [];
  for (const move of ["back", "back", "back", "forward"]) {
    await (move === "back" ? page.goBack() : page.goForward());
    await wait(900);
    steps.push(await level());
  }
  const want = ["L3 · Function", "L2 · Components", "L1 · Overview", "L2 · Components"];
  expect(steps.join(",") === want.join(","), `got ${steps.join(" → ")}`);
});

await check("a failed run opens on its failure with the cause", async () => {
  await page.goto(`${base}#run=vip.vizc`, { waitUntil: "networkidle0" });
  await page.reload({ waitUntil: "networkidle0" });
  await wait(1500);
  expect((await text(".level")) === "L3 · Failure", `level is ${await text(".level")}`);
  expect((await text(".banner")).includes("ValueError"), "no failure banner");
  expect((await text(".inspector")).includes("rule.pct 20 → 120"), "last change before raise missing");
  expect((await page.$$(".node.raised")).length >= 3, "error path not highlighted");
});

await check("filtered calls fold into hidden (N) and the error path stays", async () => {
  await page.select(".toggle select", "100");
  await wait(1200);
  const stubs = await page.$$eval(".node.hidden", (ns) => ns.map((n) => n.textContent));
  expect(stubs.length >= 1 && stubs.every((t) => /hidden \(\d+\)/.test(t)), `stubs: ${stubs.join(" | ")}`);
  expect((await page.$$(".node.raised")).length >= 3, "error path was hidden");
  await page.select(".toggle select", "0");
  await wait(1000);
  expect(!(await page.$$(".node.hidden")).length, "stubs remain with no filter");
});

await check("the full source opens on the raise line and Esc closes only it", async () => {
  await page.$$eval(".inspector button.link", (bs) => bs.find((b) => b.textContent.startsWith("View file")).click());
  await wait(500);
  expect((await text(".source-panel .hl.raised")).includes("raise"), "raise line not highlighted");
  await page.keyboard.press("Escape");
  await wait(400);
  expect(!(await page.$(".source-panel")), "panel still open");
  expect((await text(".level")) === "L3 · Failure", `level is ${await text(".level")}`);
});

await check("search finds a function and reveals it", async () => {
  await page.keyboard.down("Control");
  await page.keyboard.press("k");
  await page.keyboard.up("Control");
  await page.keyboard.type("load_vip_rules");
  await wait(600);
  await page.keyboard.press("Enter");
  await wait(1200);
  expect((await text(".inspector .title-lg")) === "load_vip_rules", "search did not open the call");
});

await check("Data Flow follows an object, nested appearances included", async () => {
  await page.goto(`${base}#run=basic.vizc`, { waitUntil: "networkidle0" });
  await page.reload({ waitUntil: "networkidle0" });
  await wait(800);
  await page.$$eval(".tabs button", (bs) => bs.find((b) => b.textContent === "Data Flow").click());
  await wait(600);
  await page.$$eval(".picker .binding", (bs) => bs.find((b) => b.textContent.includes("order")).click());
  await wait(1000);
  expect((await text(".level")) === "Data Flow", `level is ${await text(".level")}`);
  expect((await page.$$(".step")).length >= 5, "fewer than 5 steps for the order");
  expect((await text(".inspector")).includes("'draft' → 'paid'"), "status change missing");
  await page.$$eval(".inspector .binding", (bs) => bs.find((b) => b.textContent.includes("customer")).click());
  await wait(1000);
  const heads = await page.$$eval(".step .head", (hs) => hs.map((h) => h.textContent));
  expect(heads.some((h) => h.includes("in order.customer")), `no nested step: ${heads.join(" | ")}`);
});

await check("clicking a step opens its call", async () => {
  await page.$$eval(".react-flow__node-step", (ns) => ns.find((n) => n.textContent.includes("compute")).click());
  await wait(1200);
  expect((await text(".inspector .title-lg")) === "compute", `inspector shows ${await text(".inspector .title-lg")}`);
});

await check("Diff compares two runs and explains where they split", async () => {
  await page.goto(`${base}#run=basic.vizc`, { waitUntil: "networkidle0" });
  await page.reload({ waitUntil: "networkidle0" });
  await wait(800);
  await page.$$eval(".tabs button", (bs) => bs.find((b) => b.textContent === "Diff").click());
  await wait(500);
  await page.$$eval(".picker .binding", (bs) => bs.find((b) => b.textContent.includes("vip")).click());
  await wait(1200);
  expect((await text(".diff-badge")).includes("compute"), `badge: ${await text(".diff-badge")}`);
  expect((await text(".inspector")).includes("order.customer.tier 'basic' → 'vip'"), "argument diff missing");
  await page.$$eval(".diff-split .diff-card", (cs) => cs.find((c) => c.textContent.includes("load_vip_rules")).click());
  await wait(1500);
  expect((await text(".inspector .title-lg")) === "load_vip_rules", `inspector shows ${await text(".inspector .title-lg")}`);
  expect((await text(".chip.outline")).includes("vip"), "did not switch to run B");
});

await check("no uncaught page errors", async () => expect(!errors.length, errors.join("; ")));
await browser.close();
process.exit(failures ? 1 : 0);
