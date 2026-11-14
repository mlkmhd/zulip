"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const {$} = require("./lib/zjquery.cjs");

const message_viewport = mock_esm("../src/message_viewport", {
    $scroll_container: $(":root"),
});
const resize = zrequire("resize");

run_test("reserve compose height and preserve bottom scroll position", ({override}) => {
    const $compose = $("#compose");
    let compose_height = 60;
    $compose.get(0).getBoundingClientRect = () => ({height: compose_height});
    message_viewport.$scroll_container.get(0).scrollHeight = 1200;

    let at_bottom = true;
    override(message_viewport, "at_rendered_bottom", () => at_bottom);
    const scroll_targets = [];
    override(message_viewport, "scrollTop", (target) => {
        scroll_targets.push(target);
    });

    resize.update_message_feed_bottom_space();
    assert.equal($(":root").get(0).style.getPropertyValue("--message-feed-bottom-space"), "60px");
    assert.deepEqual(scroll_targets, [1200]);

    compose_height = 240;
    resize.update_message_feed_bottom_space();
    assert.equal($(":root").get(0).style.getPropertyValue("--message-feed-bottom-space"), "240px");
    assert.deepEqual(scroll_targets, [1200, 1200]);

    at_bottom = false;
    compose_height = 90;
    resize.update_message_feed_bottom_space();
    assert.equal($(":root").get(0).style.getPropertyValue("--message-feed-bottom-space"), "90px");
    assert.deepEqual(scroll_targets, [1200, 1200]);
});
