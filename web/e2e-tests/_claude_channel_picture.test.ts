import assert from "node:assert/strict";

import type {Page} from "puppeteer";

import * as common from "./lib/common.ts";

async function test_channel_picture(page: Page): Promise<void> {
    await common.log_in(page);
    await page.goto("http://zulip.zulipdev.com:9981/#channels/new");
    await page.waitForSelector("#create_stream_name", {visible: true});
    await page.type("#create_stream_name", "Picture testing");
    await page.click("#stream_creation_go_to_subscribers");
    await page.click(".finalize_create_stream");
    await page.waitForSelector(".message-header-stream-settings-button", {visible: true});
    await page.click(".message-header-stream-settings-button");
    await page.waitForSelector(".channel-picture-upload", {visible: true});
    const stream_id = await page.evaluate(() => zulip_test.get_sub("Picture testing")!.stream_id);
    await page.evaluate(async () => {
        const token = document.querySelector<HTMLInputElement>(
            'input[name="csrfmiddlewaretoken"]',
        )!.value;
        const response = await fetch("/json/settings/web_ui_version", {
            method: "PATCH",
            headers: {
                "Content-Type": "application/x-www-form-urlencoded",
                "X-CSRFToken": token,
            },
            body: "version=modern",
        });
        if (!response.ok) {
            throw new Error(await response.text());
        }
    });
    await page.goto(
        `http://zulip.zulipdev.com:9981/#channels/${stream_id}/Picture-testing/general`,
    );
    await page.reload();
    await page.waitForSelector(".channel-picture-upload", {visible: true});
    await common.screenshot(page, "channel-picture-empty");

    const file_chooser = page.waitForFileChooser();
    await page.click(".channel-picture-upload");
    await (await file_chooser).accept(["static/images/default-avatar.png"]);
    await page.waitForSelector("#uppy-editor .cropper-crop-box", {visible: true});
    await common.screenshot(page, "channel-picture-crop");
    await page.click("#uppy-editor .dialog_submit_button");

    await page.waitForFunction(() => {
        const preview = document.querySelector<HTMLImageElement>(".channel-picture-preview");
        return preview && !preview.hidden && preview.naturalWidth > 0;
    });
    await page.waitForSelector(".channel-picture-remove", {visible: true});
    await common.screenshot(page, "channel-picture-saved");
    const sidebar = `.narrow-filter[data-stream-id="${stream_id}"] .channel-sidebar-picture`;
    assert.ok(
        await page.$eval(
            sidebar,
            (image) =>
                image instanceof HTMLImageElement &&
                !image.hidden &&
                image.src.includes("/icon?v="),
        ),
    );

    await page.setViewport({width: 480, height: 800});
    await page.waitForSelector(".channel-picture-preview", {visible: true});
    await common.screenshot(page, "channel-picture-narrow-settings");
    await page.setViewport({width: 1400, height: 937});

    await page.evaluate(async () => {
        const response = await fetch("/json/settings/web_ui_version", {
            method: "PATCH",
            headers: {
                "Content-Type": "application/x-www-form-urlencoded",
                "X-CSRFToken": document.querySelector<HTMLInputElement>(
                    'input[name="csrfmiddlewaretoken"]',
                )!.value,
            },
            body: "version=classic",
        });
        if (!response.ok) {
            throw new Error(await response.text());
        }
    });
    await page.emulateMediaFeatures([{name: "prefers-color-scheme", value: "dark"}]);
    await page.reload();
    await page.waitForFunction(() => {
        const preview = document.querySelector<HTMLImageElement>(".channel-picture-preview");
        return preview && !preview.hidden && preview.naturalWidth > 0;
    });
    await page.waitForSelector(`${sidebar}:not([hidden])`);
    await common.screenshot(page, "channel-picture-classic");
    await page.click(".channel-picture-remove");
    await page.waitForSelector(".micromodal[aria-hidden=false] .dialog_submit_button", {
        visible: true,
    });
    await page.click(".micromodal[aria-hidden=false] .dialog_submit_button");
    await page.waitForSelector(".channel-picture-preview", {hidden: true});
    await page.waitForSelector(".channel-picture-remove", {hidden: true});
    await common.screenshot(page, "channel-picture-removed");
}

await common.run_test(test_channel_picture);
