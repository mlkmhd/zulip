import assert from "node:assert/strict";

import type {Page} from "puppeteer";

import * as common from "./lib/common.ts";

async function test_reply_to_message(page: Page): Promise<void> {
    await common.log_in(page);
    await common.send_message(page, "stream", {
        stream_name: "Verona",
        topic: "Message replies",
        content: "Original message for the reply test",
    });

    const source = await page.waitForSelector(
        "xpath///*[text()='Original message for the reply test']/ancestor::*[contains(@class, 'message_row')][1]",
        {visible: true},
    );
    assert.ok(source);
    await source.hover();
    const actions = await source.$(".message-actions-menu-button");
    assert.ok(actions);
    await actions.click();
    await page.click(".reply-to-message");
    await page.waitForSelector("#compose-textarea", {visible: true});
    await page.keyboard.type("This is a reply");
    const reply_content = await page.$eval("textarea#compose-textarea", (element) => element.value);
    await page.click("#compose-send-button");
    await common.wait_for_fully_processed_message(page, reply_content);

    const preview = await page.waitForSelector(".message_row .message-reply-link", {
        visible: true,
    });
    assert.ok(preview);
    assert.match(
        await preview.evaluate((element) => element.textContent!),
        /Original message for the reply test/,
    );
    await page.click(".message_row:has(.message-reply-link) .message_content p:last-child");
    await page.waitForFunction(() =>
        document
            .querySelector(".selected_message .message_content")
            ?.textContent?.includes("This is a reply"),
    );
    await common.screenshot(page, "sent-reply");
    await preview.click();
    await page.waitForFunction(
        () =>
            document.querySelector(".selected_message .message_content")?.textContent?.trim() ===
            "Original message for the reply test",
    );
}

await common.run_test(test_reply_to_message);
