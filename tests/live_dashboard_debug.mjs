/**
 * Live Playwright debug — Dashboard session + discussion bugs
 * Run: npx playwright test tests/live_dashboard_debug.mjs --headed
 * Or:  node tests/live_dashboard_debug.mjs
 */
import { chromium } from 'playwright';

const URL = 'http://127.0.0.1:8420';

(async () => {
  const browser = await chromium.launch({ headless: false, slowMo: 600 });
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });

  console.log('\n=== 1. OPEN DASHBOARD ===');
  await page.goto(URL);
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(2000);
  await page.screenshot({ path: 'screenshots/debug-01-dashboard-loaded.png' });

  // --- Check session bar ---
  console.log('\n=== 2. CHECK SESSION BAR ===');
  const sessionTabs = await page.locator('.session-tab').all();
  console.log(`  Session tabs found: ${sessionTabs.length}`);
  for (const tab of sessionTabs) {
    const text = await tab.textContent();
    const cls = await tab.getAttribute('class');
    console.log(`    Tab: "${text.trim()}" | active: ${cls.includes('active')}`);
  }

  // --- Check current chat messages ---
  console.log('\n=== 3. CHECK CHAT MESSAGES IN CURRENT SESSION ===');
  const chatMsgs = await page.locator('.chat-msg').all();
  console.log(`  Messages visible: ${chatMsgs.length}`);
  for (let i = 0; i < Math.min(chatMsgs.length, 5); i++) {
    const text = (await chatMsgs[i].textContent()).substring(0, 80);
    const cls = await chatMsgs[i].getAttribute('class');
    console.log(`    [${i}] class="${cls}" text="${text}..."`);
  }
  await page.screenshot({ path: 'screenshots/debug-02-current-chat.png' });

  // --- Switch to discussion mode tab ---
  console.log('\n=== 4. CHECK CHAT MODE TABS ===');
  const modeTabs = await page.locator('.mode-tab').all();
  for (const tab of modeTabs) {
    const text = await tab.textContent();
    const cls = await tab.getAttribute('class');
    console.log(`    Mode: "${text.trim()}" | active: ${cls.includes('active')}`);
  }

  // Click discussion tab if it exists
  const discussionTab = page.locator('.mode-tab', { hasText: /discussion/i });
  if (await discussionTab.count() > 0) {
    console.log('  >> Clicking Discussion tab...');
    await discussionTab.click();
    await page.waitForTimeout(1500);
    await page.screenshot({ path: 'screenshots/debug-03-discussion-tab.png' });

    const msgsAfter = await page.locator('.chat-msg').all();
    console.log(`  Messages after switching to discussion: ${msgsAfter.length}`);
  } else {
    console.log('  >> No discussion tab found');
  }

  // --- Switch to a session that has discussion messages ---
  console.log('\n=== 5. SWITCH TO SESSION WITH DISCUSSION MESSAGES ===');
  const allTabs = await page.locator('.session-tab').all();
  for (const tab of allTabs) {
    const text = await tab.textContent();
    if (text.includes('discussion') || text.includes('Discussion')) {
      console.log(`  >> Clicking session: "${text.trim()}"`);
      await tab.click();
      await page.waitForTimeout(2000);
      await page.screenshot({ path: 'screenshots/debug-04-discussion-session.png' });

      const msgs = await page.locator('.chat-msg').all();
      console.log(`  Messages in discussion session: ${msgs.length}`);
      for (let i = 0; i < Math.min(msgs.length, 5); i++) {
        const t = (await msgs[i].textContent()).substring(0, 100);
        const c = await msgs[i].getAttribute('class');
        console.log(`    [${i}] class="${c}" text="${t}"`);
      }
      break;
    }
  }

  // --- Check what the API returns for chat history ---
  console.log('\n=== 6. CHECK API RESPONSES ===');
  const sessionsResp = await page.evaluate(() =>
    fetch('/api/chat/sessions').then(r => r.json())
  );
  console.log('  Sessions:', JSON.stringify(sessionsResp.map(s => ({
    id: s.session_id.slice(-8), name: s.name, active: s.is_active, msgs: s.message_count
  })), null, 2));

  const histResp = await page.evaluate(() =>
    fetch('/api/chat/history').then(r => r.json())
  );
  console.log(`  Chat history (current session): ${histResp.length} messages`);
  if (histResp.length > 0) {
    console.log('  First msg:', JSON.stringify(histResp[0]).substring(0, 150));
    console.log('  Last msg:', JSON.stringify(histResp[histResp.length - 1]).substring(0, 150));
    // Count by mode
    const modes = {};
    histResp.forEach(m => { const mode = (m.metadata||{}).mode||'unknown'; modes[mode] = (modes[mode]||0)+1; });
    console.log('  Messages by mode:', modes);
  }

  // --- Test NEW SESSION creation ---
  console.log('\n=== 7. TEST NEW SESSION CREATION ===');

  // Dismiss any dialogs automatically
  page.on('dialog', async dialog => {
    console.log(`  Dialog: type=${dialog.type()} message="${dialog.message()}"`);
    if (dialog.type() === 'prompt') {
      await dialog.accept('Playwright Test Session');
    } else {
      await dialog.accept();
    }
  });

  const newBtn = page.locator('.session-new');
  if (await newBtn.count() > 0) {
    console.log('  >> Clicking "+ New" button...');
    const tabsBefore = await page.locator('.session-tab').count();
    console.log(`  Tabs before: ${tabsBefore}`);

    await newBtn.click();
    await page.waitForTimeout(3000);

    const tabsAfter = await page.locator('.session-tab').count();
    console.log(`  Tabs after: ${tabsAfter}`);

    await page.screenshot({ path: 'screenshots/debug-05-after-new-session.png' });

    const updatedTabs = await page.locator('.session-tab').all();
    for (const tab of updatedTabs) {
      const text = await tab.textContent();
      const cls = await tab.getAttribute('class');
      console.log(`    Tab: "${text.trim()}" | active: ${cls.includes('active')}`);
    }

    // Check if Playwright Test Session exists
    const found = await page.locator('.session-tab', { hasText: 'Playwright Test Session' }).count();
    console.log(`  "Playwright Test Session" tab found: ${found > 0 ? 'YES' : 'NO'}`);
  }

  // --- Wait for manual observation ---
  console.log('\n=== 8. LIVE OBSERVATION (30s) ===');
  console.log('  Watch the browser for 30 seconds...');
  await page.screenshot({ path: 'screenshots/debug-06-final-state.png' });
  await page.waitForTimeout(30000);

  await browser.close();
  console.log('\n=== DONE ===');
})();
