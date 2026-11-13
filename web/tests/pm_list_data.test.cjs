"use strict";

const assert = require("node:assert/strict");

const {make_realm} = require("./lib/example_realm.cjs");
const {make_bot, make_user} = require("./lib/example_user.cjs");
const {make_message_list} = require("./lib/message_list.cjs");
const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const blueslip = require("./lib/zblueslip.cjs");

const unread = mock_esm("../src/unread", {
    num_unread_mentions_for_user_ids_strings(user_ids_string) {
        if (user_ids_string === "103") {
            return true;
        }
        return false;
    },
});

mock_esm("../src/settings_data", {
    user_can_access_all_other_users: () => true,
});
mock_esm("../src/user_status", {
    get_status_emoji: () => ({
        emoji_code: "20",
    }),
});

const narrow_state = zrequire("narrow_state");
const people = zrequire("people");
const pm_conversations = zrequire("pm_conversations");
const pm_list_data = zrequire("pm_list_data");
const message_lists = zrequire("message_lists");
const {set_realm} = zrequire("state_data");
const {initialize_user_settings} = zrequire("user_settings");

set_realm(make_realm());
initialize_user_settings({user_settings: {}});

const alice = make_user({
    email: "alice@zulip.com",
    user_id: 101,
    full_name: "Alice",
});
const bob = make_user({
    email: "bob@zulip.com",
    user_id: 102,
    full_name: "Bob",
});
const me = make_user({
    email: "me@zulip.com",
    user_id: 103,
    full_name: "Me Myself",
    avatar_source: "J",
});
const zoe = make_user({
    email: "zoe@zulip.com",
    user_id: 104,
    full_name: "Zoe",
});
const cardelio = make_user({
    email: "cardelio@zulip.com",
    user_id: 105,
    full_name: "Cardelio",
});
const iago = make_user({
    email: "iago@zulip.com",
    user_id: 106,
    full_name: "Iago",
    avatar_url: "/user_avatars/iago.png",
    avatar_source: "U",
});
const bot_test = make_bot({
    email: "outgoingwebhook@zulip.com",
    user_id: 314,
    full_name: "Outgoing webhook",
});
// Add users to `valid_user_ids`.
const source = "server_events";
people.add_active_user(alice, source);
people.add_active_user(bob, source);
people.add_active_user(me, source);
people.add_active_user(zoe, source);
people.add_active_user(cardelio, source);
people.add_active_user(iago, source);
people.add_active_user(bot_test, source);
people.initialize_current_user(me.user_id);

function test(label, f) {
    run_test(label, (helpers) => {
        message_lists.set_current(undefined);
        pm_conversations.clear_for_testing();
        f(helpers);
    });
}

function set_pm_with_filter(user_ids) {
    message_lists.set_current(make_message_list([{operator: "dm", operand: user_ids}]));
}

test("find_saved_messages_before_sending", ({override}) => {
    override(unread, "num_unread_for_user_ids_string", () => 0);
    const get_matching_recipients = () =>
        pm_list_data.get_conversations("Saved Mes").map((conversation) => conversation.recipients);
    assert.deepEqual(get_matching_recipients(), ["translated: Saved Messages"]);
    pm_conversations.recent.insert([me.user_id], 1);
    assert.deepEqual(get_matching_recipients(), ["translated: Saved Messages"]);
});

test("pinned conversations stay above newer conversations", ({override}) => {
    override(unread, "num_unread_for_user_ids_string", () => 0);
    pm_conversations.recent.initialize({
        recent_private_conversations: [
            {user_ids: [alice.user_id, bob.user_id], max_message_id: 10, pinned: true},
            {user_ids: [zoe.user_id], max_message_id: 20},
        ],
    });
    pm_conversations.recent.insert([zoe.user_id], 30);

    let conversations = pm_list_data.get_conversations();
    assert.deepEqual(
        conversations.map(({user_ids_string}) => user_ids_string),
        ["101,102", "104"],
    );
    assert.equal(conversations[0].is_pinned, true);
    assert.deepEqual(
        pm_list_data.get_conversations("Zoe").map(({user_ids_string}) => user_ids_string),
        ["104"],
    );

    pm_conversations.recent.set_pinned([bob.user_id, alice.user_id], false);
    conversations = pm_list_data.get_conversations();
    assert.deepEqual(
        conversations.map(({user_ids_string}) => user_ids_string),
        ["104", "101,102"],
    );
    assert.equal(conversations[1].is_pinned, undefined);
});

test("get_conversations", ({override}) => {
    pm_conversations.recent.insert([alice.user_id, bob.user_id], 1);
    pm_conversations.recent.insert([me.user_id], 2);
    let num_unread_for_user_ids_string = 1;
    override(unread, "num_unread_for_user_ids_string", () => num_unread_for_user_ids_string);

    assert.equal(narrow_state.filter(), undefined);

    const expected_data = [
        {
            is_bot: false,
            is_active: false,
            includes_deactivated_user: false,
            is_group: false,
            is_zero: false,
            recipients: "translated: Saved Messages",
            unread: 1,
            url: "#narrow/dm/103-Me-Myself",
            avatar_url: undefined,
            avatar_initials: "M M",
            user_circle_class: "user-circle-offline",
            user_ids_string: "103",
            status_emoji_info: {
                emoji_code: "20",
            },
            has_unread_mention: true,
        },
        {
            recipients: "Alice, Bob",
            user_ids_string: "101,102",
            unread: 1,
            is_zero: false,
            is_active: false,
            includes_deactivated_user: false,
            url: "#narrow/dm/101,102-group",
            avatar_url: undefined,
            avatar_initials: undefined,
            user_circle_class: undefined,
            is_group: true,
            is_bot: false,
            status_emoji_info: undefined,
            has_unread_mention: false,
        },
    ];

    let pm_data = pm_list_data.get_conversations();
    assert.deepEqual(pm_data, expected_data);

    num_unread_for_user_ids_string = 0;

    pm_data = pm_list_data.get_conversations();
    expected_data[0].unread = 0;
    expected_data[0].is_zero = true;
    expected_data[1].unread = 0;
    expected_data[1].is_zero = true;
    assert.deepEqual(pm_data, expected_data);

    pm_data = pm_list_data.get_conversations();
    assert.deepEqual(pm_data, expected_data);

    expected_data.unshift({
        recipients: "Iago",
        user_ids_string: "106",
        unread: 0,
        is_zero: true,
        is_active: true,
        includes_deactivated_user: false,
        url: "#narrow/dm/106-Iago",
        status_emoji_info: {emoji_code: "20"},
        avatar_url: "/user_avatars/iago.png",
        avatar_initials: undefined,
        user_circle_class: "user-circle-offline",
        is_group: false,
        is_bot: false,
        has_unread_mention: false,
    });
    set_pm_with_filter([iago.user_id]);
    pm_data = pm_list_data.get_conversations();
    assert.deepEqual(pm_data, expected_data);

    pm_data = pm_list_data.get_conversations("Ia");
    assert.deepEqual(
        pm_data,
        expected_data.filter((item) => item.recipients === "Iago"),
    );

    // filter should work with email
    pm_data = pm_list_data.get_conversations("me@zulip");
    assert.deepEqual(
        pm_data,
        expected_data.filter((item) => item.recipients === "translated: Saved Messages"),
    );
});

test("get_conversations bot", ({override}) => {
    pm_conversations.recent.insert([alice.user_id, bob.user_id], 1);
    pm_conversations.recent.insert([bot_test.user_id], 2);

    override(unread, "num_unread_for_user_ids_string", () => 1);

    assert.equal(narrow_state.filter(), undefined);

    const expected_data = [
        {
            recipients: "Outgoing webhook",
            user_ids_string: "314",
            unread: 1,
            is_zero: false,
            is_active: false,
            includes_deactivated_user: false,
            url: "#narrow/dm/314-Outgoing-webhook",
            status_emoji_info: undefined,
            avatar_url: "/avatar/314",
            avatar_initials: undefined,
            user_circle_class: "user-circle-offline",
            is_group: false,
            is_bot: true,
            has_unread_mention: false,
        },
        {
            recipients: "Alice, Bob",
            user_ids_string: "101,102",
            unread: 1,
            is_zero: false,
            is_active: false,
            includes_deactivated_user: false,
            url: "#narrow/dm/101,102-group",
            avatar_url: undefined,
            avatar_initials: undefined,
            user_circle_class: undefined,
            status_emoji_info: undefined,
            is_group: true,
            is_bot: false,
            has_unread_mention: false,
        },
    ];

    const pm_data = pm_list_data.get_conversations();
    assert.deepEqual(pm_data, expected_data);
});

test("get_active_user_ids_string", () => {
    assert.equal(pm_list_data.get_active_user_ids_string(), undefined);

    message_lists.set_current(make_message_list([{operator: "stream", operand: "test"}]));
    assert.equal(pm_list_data.get_active_user_ids_string(), undefined);

    set_pm_with_filter([bob.user_id, alice.user_id]);
    assert.equal(pm_list_data.get_active_user_ids_string(), "101,102");

    blueslip.expect("warn", "Invalid user_ids");
    set_pm_with_filter([-1]);
    assert.equal(pm_list_data.get_active_user_ids_string(), undefined);
    blueslip.reset();

    set_pm_with_filter([alice.user_id, bob.user_id, me.user_id]);
    assert.equal(pm_list_data.get_active_user_ids_string(), "101,102");
});

test("get_conversations includes older read conversations", ({override}) => {
    override(unread, "num_unread_for_user_ids_string", () => 0);

    pm_conversations.recent.insert([alice.user_id], 1);
    pm_conversations.recent.insert([me.user_id], 2);
    pm_conversations.recent.insert([bob.user_id], 3);
    pm_conversations.recent.insert([zoe.user_id], 4);
    pm_conversations.recent.insert([cardelio.user_id], 5);
    pm_conversations.recent.insert([zoe.user_id, cardelio.user_id], 6);
    pm_conversations.recent.insert([alice.user_id, bob.user_id], 7);
    pm_conversations.recent.insert([zoe.user_id, bob.user_id], 8);
    pm_conversations.recent.insert([alice.user_id, cardelio.user_id], 9);
    pm_conversations.recent.insert([bob.user_id, cardelio.user_id], 10);

    const conversations = pm_list_data.get_conversations();
    assert.equal(conversations.length, 10);
    assert.equal(conversations.at(-1).recipients, "Alice");
    assert.deepEqual(
        pm_list_data.get_conversations("Alice").map((conversation) => conversation.recipients),
        ["Alice, Cardelio", "Alice, Bob", "Alice"],
    );
});
