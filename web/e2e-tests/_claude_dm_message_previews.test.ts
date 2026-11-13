import assert from "node:assert/strict";

import type {Page} from "puppeteer";

import * as common from "./lib/common.ts";

async function visual_test(page: Page): Promise<void> {
    await common.log_in(page);
    await Promise.all([page.waitForNavigation(), page.click(".accept-web-ui-version")]);
    const message = "پیش‌نمایش پیام فارسی";
    await common.send_message(page, "private", {
        recipient: "hamlet@zulip.com",
        content: `${message} :melting_face:`,
    });
    await common.send_message(page, "private", {
        recipient: "hamlet@zulip.com, cordelia@zulip.com",
        content: "Group preview :+1:",
    });
    const preview = "#direct-messages-list .dm-message-preview";
    const expected = [
        {selector: `.dm-box[data-is-group="false"] .dm-message-preview`, text: `${message} 🫠`},
        {selector: `.dm-box[data-is-group="true"] .dm-message-preview`, text: "Group preview 👍"},
    ];
    await page.waitForFunction(
        (entries) =>
            entries.every(({selector, text}) =>
                [...document.querySelectorAll(selector)].some((node) => node.textContent === text),
            ),
        {},
        expected,
    );
    const preview_size = await page.$eval(preview, (element) => {
        const style = getComputedStyle(element);
        return {
            line_height: Number.parseFloat(style.lineHeight),
            font_size: Number.parseFloat(style.fontSize),
        };
    });
    assert.ok(preview_size.line_height > preview_size.font_size);

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
