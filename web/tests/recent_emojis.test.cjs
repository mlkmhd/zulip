"use strict";

const assert = require("node:assert/strict");

const {set_global, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");

const recent_emojis = zrequire("recent_emojis");
const {set_current_user} = zrequire("state_data");

run_test("stores recent emojis per account", () => {
    const entries = new Map();
    set_global("localStorage", {
        getItem: (key) => entries.get(key) ?? null,
        setItem: (key, value) => entries.set(key, value),
    });
    const rocket = {emoji_code: "1f680", reaction_type: "unicode_emoji"};
    const custom = {emoji_code: "1", reaction_type: "realm_emoji"};

    set_current_user({user_id: 10});
    assert.deepEqual(recent_emojis.get(), []);
    recent_emojis.record(rocket);
    recent_emojis.record(custom);
    assert.deepEqual(recent_emojis.get(), [custom, rocket]);
    recent_emojis.record(rocket);
    assert.deepEqual(recent_emojis.get(), [rocket, custom]);

    set_current_user({user_id: 20});
    assert.deepEqual(recent_emojis.get(), []);
    recent_emojis.record(custom);

    set_current_user({user_id: 10});
    assert.deepEqual(recent_emojis.get(), [rocket, custom]);
    for (let id = 2; id < 32; id += 1) {
        recent_emojis.record({...custom, emoji_code: String(id)});
    }
    assert.equal(recent_emojis.get().length, 30);
    assert.equal(recent_emojis.get()[0].emoji_code, "31");
    assert.equal(recent_emojis.get().at(-1).emoji_code, "2");
});
