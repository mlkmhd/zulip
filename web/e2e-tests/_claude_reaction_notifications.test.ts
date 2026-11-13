import assert from "node:assert/strict";

import type {Page} from "puppeteer";

import * as common from "./lib/common.ts";

async function test_reaction_setting(page: Page): Promise<void> {
    await common.log_in(page);
    await common.manage_organization(page);
    const input = "#id_realm_enable_reaction_notifications";
    const section = ".settings-subsection-parent:has(" + input + ")";
    const label = 'label[for="id_realm_enable_reaction_notifications"]';
    await page.waitForSelector(label, {visible: true});
    assert.equal(await page.$eval(input, (element) => (element as HTMLInputElement).checked), true);
    await page.$eval(section, (element) => element.scrollIntoView({block: "center"}));
    await common.screenshot(page, "reaction-notifications-default");
    await page.click(label);
    const save_button = section + ' .save-button[data-status="unsaved"]';
    await page.waitForSelector(save_button, {visible: true});
    await common.screenshot(page, "reaction-notifications-disabled-unsaved");
    await page.click(save_button);
    await page.waitForSelector(section + ' .save-button[data-status="saved"]', {visible: true});
    await page.reload();
    await page.waitForSelector(label, {visible: true});
    assert.equal(await page.$eval(input, (element) => (element as HTMLInputElement).checked), false);
    await page.setViewport({width: 480, height: 820});
    await page.$eval(section, (element) => element.scrollIntoView({block: "center"}));
    await common.screenshot(page, "reaction-notifications-disabled-mobile");
}

await common.run_test(test_reaction_setting);
