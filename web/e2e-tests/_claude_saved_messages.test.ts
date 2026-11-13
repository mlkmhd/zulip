import assert from "node:assert/strict";

import type {Page} from "puppeteer";

import * as common from "./lib/common.ts";

async function test_saved_messages(page: Page): Promise<void> {
    await common.log_in(page);
    await page.waitForSelector(".accept-web-ui-version", {visible: true});
    await Promise.all([page.waitForNavigation(), page.click(".accept-web-ui-version")]);
    const saved_messages_link = '#left-sidebar-navigation-list a[href="#narrow/dm/me"]';
    await page.waitForSelector(saved_messages_link, {visible: true});
    await page.click(saved_messages_link);
    await page.waitForSelector(".top_left_saved_messages.top-left-active-filter", {visible: true});
    const note = "Release checklist: review the changelog and verify the Docker image.";
    await page.keyboard.press("KeyR");
    await page.waitForSelector("#compose-textarea", {visible: true});
    await common.clear_and_type(page, "#compose-textarea", note);
    await page.click("#compose-send-button");
    await page.waitForFunction(
        (content) => [...document.querySelectorAll(".message_content")].some(
            (element) => element.textContent?.trim() === content,
        ),
        {},
        note,
    );
    await common.screenshot(page, "saved-messages");

    await common.manage_organization(page);
    const input = "#id_realm_expire_saved_messages";
    const label = 'label[for="id_realm_expire_saved_messages"]';
    const section = "#org-message-retention";
    await page.waitForSelector(label, {visible: true});
    assert.equal(
        await page.$eval(
            input,
            (element) => element instanceof HTMLInputElement && element.checked,
        ),
        false,
    );
    await page.$eval(section, (element) => {
        element.scrollIntoView({block: "center"});
    });
    await common.screenshot(page, "saved-messages-retention");
    await page.click(label);
    const save_button = section + ' .save-button[data-status="unsaved"]';
    await page.waitForSelector(save_button, {visible: true});
    await page.click(save_button);
    await page.waitForSelector(section + ' .save-button[data-status="saved"]', {
        visible: true,
    });
    await page.reload();
    await page.waitForSelector(label, {visible: true});
    assert.equal(
        await page.$eval(
            input,
            (element) => element instanceof HTMLInputElement && element.checked,
        ),
        true,
    );
    await page.setViewport({width: 480, height: 820});
    await page.$eval(section, (element) => {
        element.scrollIntoView({block: "center"});
    });
    await common.screenshot(page, "saved-messages-retention-mobile");
}

await common.run_test(test_saved_messages);
