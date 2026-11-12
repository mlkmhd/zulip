import assert from "node:assert/strict";

import type {Page} from "puppeteer";

import * as common from "./lib/common.ts";

async function visual_test(page: Page): Promise<void> {
    await common.log_in(page);
    const message = "Checking the web interface message preview";
    await common.send_message(page, "private", {
        recipient: "hamlet@zulip.com",
        content: message,
    });
    await Promise.all([page.waitForNavigation(), page.click(".accept-web-ui-version")]);
    const preview = "#direct-messages-list .dm-message-preview";
    await page.waitForSelector(preview, {visible: true});
    const previews = await page.$$eval(preview, (nodes) => nodes.map((node) => node.textContent));
    assert.ok(previews.includes(message));

    await page.$eval(preview, (element) => {
        element.scrollIntoView({block: "center"});
    });
    await common.screenshot(page, "dm-message-previews-desktop");
    await page.setViewport({width: 1280, height: 800});
    await page.$eval(preview, (element) => {
        element.scrollIntoView({block: "center"});
    });
    await common.screenshot(page, "dm-message-previews-laptop");
}

await common.run_test(visual_test);
