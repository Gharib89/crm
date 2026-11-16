# Browser test: prove a form script, ribbon command or site map entry

The test that a `form_script`, `ribbon_command` or `sitemap_entry` requirement
needs (`reference/verification.md`). The CLI drives no browser: the runner is
Playwright in a headed Chromium with a persistent profile, so the human signs in
once and the agent reuses that session. Proven on Dataverse online with an
`onload` handler; on-premises sign-in pages (NTLM, AD FS) are untested.

**Only a browser run passes these kinds.** A handler run outside the browser (a
Node harness, a mocked `Xrm`) proves the handler's logic, not that the target
loads the library and wires the event: record it as unit-test evidence for the
shared library, and record the requirement `blocked` with the reason "browser
test not run".

## 1. Preconditions

- The target passed the `whoami` check (`reference/verification.md`).
- The repaired component is deployed and published.
- The package's requirement `details` are in hand: its libraries, handlers
  (event, function, attribute), control or site map entry, and its expected
  behavior is known (`reference/verification.md`).

Done when all three hold. Otherwise record the requirement `blocked`, naming the
unmet precondition in `errors`, and stop.

## 2. Install the runner, outside the application repository

Skip this step when `~/.crm-browser/tool/browser-test.mjs` exists and
Chromium is installed there.

```sh
mkdir -p ~/.crm-browser/tool && cd ~/.crm-browser/tool
npm install playwright && npx playwright install chromium
```

Save this as `~/.crm-browser/tool/browser-test.mjs`. It exits 0 on pass, 1 on
fail, 2 on blocked (the browser did not start, or the sign-in did not finish
within the window, 600 seconds by default), and writes `screenshot.png` and
`console.log` to the evidence folder.

```js
// node browser-test.mjs <session-dir> <url> <evidence-dir> <check.mjs> [sign-in-seconds]
import { chromium } from 'playwright';
import { mkdirSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const [sessionDir, url, outDir, checkFile, waitS = '600'] = process.argv.slice(2);
const check = (await import(pathToFileURL(resolve(checkFile)).href)).default;
const org = new URL(url).origin;
mkdirSync(outDir, { recursive: true });

const ctx = await chromium.launchPersistentContext(sessionDir, { headless: false }).catch((e) => {
  console.log(`BLOCKED: browser did not start: ${e.message.split('\n')[0]}`);
  process.exit(2);
});
const page = ctx.pages()[0] ?? (await ctx.newPage());
const log = [];
page.on('console', (m) => log.push(`[${m.type()}] ${m.text()}`));
page.on('pageerror', (e) => log.push(`[pageerror] ${e.message}`));

try {
  await page.goto(url);
  console.log('WAITING: sign in (and finish MFA) in the browser window');
  const ready = await page
    .waitForFunction((o) => location.origin === o && !!window.Xrm?.Utility?.getGlobalContext, org, {
      timeout: Number(waitS) * 1000,
    })
    .then(() => true, () => false);
  if (!ready) {
    console.log('BLOCKED: sign-in not completed');
    process.exitCode = 2;
  } else {
    await check(page);
    await page.screenshot({ path: `${outDir}/screenshot.png` });
    console.log('PASS');
  }
} catch (e) {
  await page.screenshot({ path: `${outDir}/screenshot.png` }).catch(() => {});
  console.log(`FAIL: ${e.message}`);
  process.exitCode = 1;
} finally {
  writeFileSync(`${outDir}/console.log`, log.join('\n') + '\n');
  await ctx.close();
}
```

Done when `npx playwright install chromium` exited 0 and the script is saved.

## 3. Test each requirement

Write `<results folder>/checks/<requirement>.mjs`, exporting
`async (page) => {...}` that drives the page and asserts a visible result for
every handler, control or entry the requirement lists, taken from its expected
behavior. Run it:

```sh
node ~/.crm-browser/tool/browser-test.mjs ~/.crm-browser/session-<profile> \
  '<org url>/main.aspx?pagetype=entityrecord&etn=<entity>' \
  <results folder>/evidence/<requirement> <results folder>/checks/<requirement>.mjs
```

Open a new record unless the expected behavior names an existing one; then add
`&id=<record id>`. Add `&appid=<app id>` when the entity lives in a specific app.

**The first run of a session signs the human in.** The session folder,
`~/.crm-browser/session-<profile>`, holds the signed-in browser profile with its
cookies: it stays outside the application repository and the results folder,
uncommitted and uncited. On the first run, start the command in the background;
when it prints `WAITING`, tell the human in one message: a browser window is
open on the target org; sign in with an account that can open the form or app
under test, finish MFA there, and nothing else is needed. The run continues on
its own once the page is back on the org with the client API loaded. Later runs
reuse the session until it expires.

The check, per kind:

- `form_script`, `onload`: opening the form fires it. Assert what the handler
  shows: `await page.getByText('<text>', { exact: true }).waitFor()`. Use
  `exact: true`: the form renders a notification's text twice (the banner and
  a screen-reader copy), and a loose match fails on the duplicate.
- `form_script`, `onchange`: change the attribute through its control on the
  form (`page.getByLabel('<field label>').fill(...)`, then press Tab), then
  assert the result. `onsave`: save from the command bar, then assert.
- `ribbon_command`: find the control by its label
  (`page.getByRole('menuitem', { name: '<label>' })`), click it, assert the
  result it should reach.
- `sitemap_entry`: open the app (`main.aspx?appid=<app id>`), click the
  navigation entry by its title, assert the page it lands on (heading or URL).

Only the `onload` check has run live. For every other check, a `FAIL` whose
screenshot shows the control never appeared is a check bug: fix the selector
and rerun before recording `fail`.

Done when every browser requirement of the package has a run that exited.

## 4. Save and record the evidence

Cite only the `screenshot.png` (the asserted state; it shows the signed-in
user's name) and `console.log` (the browser console, where a handler's own
errors land) the runner writes, as `reference/verification.md` says evidence is
cited. A Playwright trace records network traffic and browser storage, session
cookies included, so the runner records none. Before hashing a log, run
`grep -inE 'cookie|bearer|token|eyJ' console.log` (`eyJ` starts a JWT) and
remove each line it prints.

Map the exit code to the result:

- `0`: `pass`, with both files as evidence.
- `1`: `fail`; put the `FAIL:` line in `actual` and the console errors in
  `errors`, and cite the screenshot.
- `2`, or no run (no human available, sign-in refused, the runner would not
  install): `blocked`, with the reason in `errors`, such as "browser test not
  run: the sign-in was not completed".

`environment` is the `whoami` profile and org, even though the browser ran as
the signed-in human. Done when each browser requirement has its result and the
grep prints nothing for every cited log.
