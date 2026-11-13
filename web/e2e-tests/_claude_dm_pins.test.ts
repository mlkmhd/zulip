import assert from "node:assert/strict";

import type {Page} from "puppeteer";

import * as common from "./lib/common.ts";

async function test_dm_pins(page: Page): Promise<void> {
    await common.log_in(page);
    await common.send_message(page, "private", {
        recipient: "hamlet@zulip.com",
        content: "Conversation to pin",
    });
    await common.send_message(page, "private", {
        recipient: "othello@zulip.com",
        content: "A newer conversation",
    });
    await page.waitForFunction(
        (content) => {
            const message = zulip_test.current_msg_list?.last();
            return message?.raw_content === content && !message.locally_echoed;
        },
        {},
        "A newer conversation",
    );
    await page.waitForSelector(".accept-web-ui-version", {visible: true});
    await Promise.all([page.waitForNavigation(), page.click(".accept-web-ui-version")]);

    await page.waitForSelector(".dm-message-preview", {visible: true});
    const hamlet_id = await common.get_user_id_from_name(page, common.fullname.hamlet);
    const row = `#direct-messages-list .dm-list-item[data-user-ids-string="${hamlet_id}"]`;
    const pinned = `${row}:first-child .dm-pin-button.dm-is-pinned`;
    await page.waitForSelector(row, {visible: true});
    assert.equal(await page.$eval(row, (element) => element.matches(":first-child")), false);
    await page.hover(row);
    await page.waitForSelector(`${row} .dm-pin-button .zulip-icon-pin`, {visible: true});
    await common.screenshot(page, "dm-before-pin");
    await page.click(`${row} .dm-pin-button`);
    await page.waitForSelector(`${pinned} .zulip-icon-pin-filled`, {visible: true});
    await page.mouse.move(0, 0);
    await common.screenshot(page, "dm-pinned");

    await page.emulateMediaFeatures([{name: "prefers-color-scheme", value: "dark"}]);
    await page.waitForFunction(() => matchMedia("(prefers-color-scheme: dark)").matches);
    await common.screenshot(page, "dm-pinned-modern-dark");
    await common.open_personal_menu(page);
    await page.click("#personal-menu-dropdown a[href^='#settings']");
    await page.waitForSelector('[data-section="preferences"]', {visible: true});
    await page.click('[data-section="preferences"]');
    await page.waitForSelector("#web-ui-version", {visible: true});
    await Promise.all([page.waitForNavigation(), page.select("#web-ui-version", "classic")]);
    await page.waitForSelector(`${pinned} .zulip-icon-pin-filled`, {visible: true});
    await page.click("#settings_page .content-wrapper .exit");
    await page.waitForSelector("#settings_overlay_container", {hidden: true});
    await common.screenshot(page, "dm-pinned-classic-dark");
    await page.focus(pinned);
    await page.keyboard.press("Enter");
    await page.waitForSelector(pinned, {hidden: true});
    await page.waitForSelector(`${row} .dm-pin-button .zulip-icon-pin`);
    assert.equal(await page.$eval(row, (element) => element.matches(":first-child")), false);

    await page.reload();
    await page.waitForSelector(row, {visible: true});
    assert.equal(await page.$eval(row, (element) => element.matches(":first-child")), false);
}

await common.run_test(test_dm_pins);
