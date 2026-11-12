import assert from "node:assert/strict";

import type {Page} from "puppeteer";

import * as common from "./lib/common.ts";

async function assert_sidebar_order(page: Page, version: "classic" | "modern"): Promise<void> {
    await page.waitForSelector("#direct-messages-section-header", {visible: true});
    const direct_messages_first = await page.$eval(
        "#direct-messages-section-header",
        (dm_header) =>
            dm_header.getBoundingClientRect().top <
            document.querySelector("#streams_list")!.getBoundingClientRect().top,
    );
    assert.equal(direct_messages_first, version === "classic");
}

async function test_web_ui_version(page: Page): Promise<void> {
    await common.log_in(page);
    await assert_sidebar_order(page, "classic");
    await page.waitForSelector(".accept-web-ui-version", {visible: true});
    await common.screenshot(page, "classic-interface-offer");
    await page.emulateMediaFeatures([{name: "prefers-color-scheme", value: "light"}]);
    await page.setViewport({width: 480, height: 800});
    await common.screenshot(page, "classic-interface-offer-mobile");
    await page.setViewport(common.window_size);
    await page.emulateMediaFeatures([]);

    await Promise.all([page.waitForNavigation(), page.click(".accept-web-ui-version")]);
    await assert_sidebar_order(page, "modern");
    await common.screenshot(page, "modern-interface");

    await common.open_personal_menu(page);
    await page.click("#personal-menu-dropdown a[href^='#settings']");
    await page.waitForSelector('[data-section="preferences"]', {visible: true});
    await page.click('[data-section="preferences"]');
    await page.waitForSelector("#web-ui-version", {visible: true});
    assert.equal(await page.$eval("select#web-ui-version", (select) => select.value), "modern");
    await common.screenshot(page, "web-interface-preference");

    await Promise.all([page.waitForNavigation(), page.select("#web-ui-version", "classic")]);
    await assert_sidebar_order(page, "classic");
    assert.equal(await page.$(".accept-web-ui-version"), null);
}

await common.run_test(test_web_ui_version);
