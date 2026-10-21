// End-to-end smoke test of the built viewer (Replay) against `vizcontrol serve` of the ETL example (e2e/smoke.sh).
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
const open = async (run) => {
  await page.goto(`${base}#run=${run}`, { waitUntil: "networkidle0" });
  await page.reload({ waitUntil: "networkidle0" });
  await wait(1500);
};
const stepLabel = async () => Number((await text(".watch .label")).replace("Step ", ""));

await check("a run opens on its first step, the imports left out", async () => {
  await open("ok.vizc");
  expect((await stepLabel()) === 1 && (await text(".now-line")).includes("etl.__main__"), `opens on: ${await text(".now-line")}`);
  const lines = await page.$$eval(".transcript .line", (ls) => ls.map((l) => l.textContent));
  expect(lines[1].includes("main(") && !lines.some((l) => l.includes("<module>")), `then: ${lines.slice(0, 3).join(" / ")}`);
  expect((await page.$$(".transcript .line")).length >= 5, "no transcript");
  expect((await page.$$(".react-flow__node-file")).length >= 5, "no box per module");
});

await check("stepping moves the call stack and the lit function", async () => {
  const before = await stepLabel();
  for (let i = 0; i < 5; i++) await page.keyboard.press("ArrowRight");
  await wait(800);
  expect((await stepLabel()) === before + 5, `at step ${await stepLabel()}`);
  expect((await page.$$(".stack-row")).length >= 2, "no call stack");
  expect((await page.$$(".fn.current")).length === 1, "the current function is not lit");
  await page.keyboard.press("ArrowLeft");
  await wait(400);
  expect((await stepLabel()) === before + 4, "← did not step back");
});

await check("a callback says how it was called and where the function came from", async () => {
  const lines = await page.$$eval(".transcript .line", (ls) => ls.map((l) => l.textContent));
  for (let i = 0; i < 40 && !(await page.$(".callback-box")); i++) {
    await page.keyboard.press("ArrowRight");
    await wait(80);
  }
  const box = await text(".callback-box");
  expect(box.includes("didn't call it by name") && box.includes("self.steps["), `callback: ${box} / ${lines.length}`);
  expect((await text(".transcript .line.now")).includes("↩"), "the transcript doesn't mark the callback");
  for (let i = 0; i < 4; i++) await page.keyboard.press("ArrowRight");  // past it: on the path an edge has no label
  await wait(600);
  expect((await page.$$eval(".react-flow__edge-text", (ts) => ts.some((t) => t.textContent.includes("via self.steps")))), "no 'via' edge");
});

await check("the arrows never disappear while the recording plays", async () => {
  await open("ok.vizc");
  const counts = [];
  await page.select(".scrubber select", "25");
  await page.click(".scrubber .play");
  for (let i = 0; i < 40; i++) {
    await wait(25);
    counts.push(await page.$$eval(".react-flow__edge", (es) => es.length));
  }
  await page.click(".scrubber .play");
  expect(Math.min(...counts) > 0 && Math.min(...counts) === Math.max(...counts), `edges went ${Math.min(...counts)}..${Math.max(...counts)}`);
});

await check("the path to the current call is painted, and nodes can be moved", async () => {
  await open("ok.vizc");
  for (let i = 0; i < 8; i++) await page.keyboard.press("ArrowRight");
  await wait(800);
  const depth = (await page.$$(".stack-row")).length;
  expect((await page.$$(".react-flow__edge.live")).length >= Math.min(depth - 2, 1), "no edge painted on the path");
  const pos = () => page.$$eval(".react-flow__node-fn", (ns) => {
    const r = ns.find((n) => n.textContent.startsWith("main")).getBoundingClientRect();
    return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
  });
  const a = await pos();
  const box = await page.$$eval(".react-flow__node-file", (ns) => {  // a module's box carries its functions
    const r = ns.find((n) => n.textContent.startsWith("etl.__main__")).getBoundingClientRect();
    return { x: r.x + 20, y: r.y + 8 };
  });
  await page.mouse.move(box.x, box.y);
  await page.mouse.down();
  await page.mouse.move(box.x + 30, box.y + 80, { steps: 6 });
  await page.mouse.up();
  await wait(400);
  expect((await pos()).y - a.y > 40, "main did not move with its box");
  expect((await page.$$(".react-flow__edge")).length > 0, "moving a node lost the arrows");
  await page.click(".reset-layout");
  await wait(400);
  expect(Math.abs((await pos()).y - a.y) < 5, "Reset layout did not put it back");
  const inBox = () => page.evaluate(() => {
    const f = [...document.querySelectorAll(".react-flow__node-fn")].find((n) => n.textContent.startsWith("main")).getBoundingClientRect();
    const b = [...document.querySelectorAll(".react-flow__node-file")].find((n) => n.textContent.startsWith("etl.__main__")).getBoundingClientRect();
    return f.top >= b.top - 1 && f.bottom <= b.bottom + 1 && f.left >= b.left - 1 && f.right <= b.right + 1;
  });
  const b = await pos();
  await page.mouse.move(b.x, b.y);
  await page.mouse.down();
  await page.mouse.move(b.x + 300, b.y + 300, { steps: 8 });
  await page.mouse.up();
  await wait(400);
  expect(await inBox(), "a function left its module's box");
  await page.click(".reset-layout");
});

await check("a failed run opens on the step where it raised", async () => {
  await open("bad.vizc");
  expect((await text(".now-line")).includes("raised KeyError: GBP"), `now: ${await text(".now-line")}`);
  expect((await page.$$(".fn.failed")).length === 1, "the failing function is not marked");
  expect((await page.$$(".scrubber .mark")).length >= 1, "no raise mark on the timeline");
});

await check("a watch follows a variable step by step", async () => {
  await page.type(".watch-add input", "currency");
  await page.keyboard.press("Enter");
  await page.$eval(".watch-add input", (e) => e.blur());
  await wait(800);
  const row = await text(".watch-row");
  expect(row.includes("GBP"), `watch at the raise: ${row}`);
  await page.keyboard.press("ArrowLeft");
  await page.keyboard.press("ArrowLeft");
  await wait(600);
  expect(!(await text(".watch-row")).includes("GBP"), "the watch did not go back in time");
});

await check("a value opens with its full tree", async () => {
  await page.click(".step-values .v");
  await wait(800);
  expect((await page.$$(".vtree .row")).length >= 1, "no value tree");
  await page.click(".back");
});

await check("no uncaught page errors", async () => expect(!errors.length, errors.join("; ")));

await browser.close();
process.exit(failures ? 1 : 0);
