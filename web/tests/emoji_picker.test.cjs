"use strict";

const assert = require("node:assert/strict");

const _ = require("lodash");

const {zrequire, set_global} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const emoji = zrequire("emoji");
const emoji_picker = zrequire("emoji_picker");

const emoji_codes = zrequire("../../static/generated/emoji/emoji_codes.json");

set_global("document", "document-stub");

run_test("initialize", () => {
    emoji.initialize({
        realm_emoji: {},
        emoji_codes,
    });
    emoji_picker.initialize();

    const complete_emoji_catalog = _.sortBy(emoji_picker.complete_emoji_catalog, "name");
    assert.equal(complete_emoji_catalog.length, 10);
    assert.equal(emoji.emojis_by_name.size, 1884);

    let total_emoji_in_categories = 0;

    function assert_emoji_category(ele, icon, num) {
        assert.equal(ele.icon, icon);
        assert.equal(ele.emojis.length, num);
        function check_emojis(val) {
            for (const this_emoji of ele.emojis) {
                assert.equal(this_emoji.is_realm_emoji, val);
            }
        }
        if (ele.name === "Custom") {
            check_emojis(true);
        } else {
            check_emojis(false);
            total_emoji_in_categories += ele.emojis.length;
        }
    }
    const zulip_emoji_count = 1;
    assert_emoji_category(complete_emoji_catalog.pop(), "fa-car", 195);
    assert_emoji_category(complete_emoji_catalog.pop(), "fa-hashtag", 224);
    assert_emoji_category(complete_emoji_catalog.pop(), "fa-smile-o", 169);
    assert_emoji_category(complete_emoji_catalog.pop(), "fa-thumbs-o-up", 386);
    assert_emoji_category(complete_emoji_catalog.pop(), "fa-lightbulb-o", 264);
    assert_emoji_category(complete_emoji_catalog.pop(), "fa-cutlery", 131);
    assert_emoji_category(complete_emoji_catalog.pop(), "fa-flag", 270);
    assert_emoji_category(complete_emoji_catalog.pop(), "fa-cog", 1);
    assert_emoji_category(complete_emoji_catalog.pop(), "fa-leaf", 159);
    assert_emoji_category(complete_emoji_catalog.pop(), "fa-soccer-ball-o", 85);

    assert.equal(emoji.emojis_by_name.size, total_emoji_in_categories + zulip_emoji_count);
});

run_test("is_emoji_present_in_text", () => {
    const thermometer_emoji = {
        name: "thermometer",
        emoji_code: "1f321",
        reaction_type: "unicode_emoji",
    };
    const headphones_emoji = {
        name: "headphones",
        emoji_code: "1f3a7",
        reaction_type: "unicode_emoji",
    };
    assert.equal(emoji_picker.is_emoji_present_in_text("🌡", thermometer_emoji), true);
    assert.equal(
        emoji_picker.is_emoji_present_in_text("no emojis at all", thermometer_emoji),
        false,
    );
    assert.equal(emoji_picker.is_emoji_present_in_text("😎", thermometer_emoji), false);
    assert.equal(emoji_picker.is_emoji_present_in_text("😎🌡🎧", thermometer_emoji), true);
    assert.equal(emoji_picker.is_emoji_present_in_text("😎🎧", thermometer_emoji), false);
    assert.equal(emoji_picker.is_emoji_present_in_text("😎🌡🎧", headphones_emoji), true);
    assert.equal(
        emoji_picker.is_emoji_present_in_text("emojis with text 😎🌡🎧", thermometer_emoji),
        true,
    );
    assert.equal(
        emoji_picker.is_emoji_present_in_text("emojis with text no space😎🌡🎧", headphones_emoji),
        true,
    );
});
