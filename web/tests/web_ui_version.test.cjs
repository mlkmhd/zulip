"use strict";

const assert = require("node:assert/strict");

const {mock_esm, zrequire} = require("./lib/namespace.cjs");
const {make_stub} = require("./lib/stub.cjs");
const {run_test} = require("./lib/test.cjs");
const {$} = require("./lib/zjquery.cjs");

const channel = mock_esm("../src/channel");
const feedback_widget = mock_esm("../src/feedback_widget");
const reload = mock_esm("../src/reload");
const web_ui_version = zrequire("web_ui_version");

run_test("reloads after saving the web interface", ({override}) => {
    const reload_stub = make_stub();
    override(reload, "initiate", reload_stub.f);
    override(channel, "patch", ({url, data, success}) => {
        assert.equal(url, "/json/settings/web_ui_version");
        assert.deepEqual(data, {version: "modern"});
        success();
    });
    const on_error = make_stub();
    web_ui_version.save("modern", on_error.f);
    assert.equal(on_error.num_calls, 0);
    assert.deepEqual(reload_stub.get_args("options").options, {immediate: true});
});

run_test("reports a failure to save the web interface", ({override}) => {
    const on_error = make_stub();
    override(channel, "patch", ({error}) => error());
    override(feedback_widget, "show", ({title_text, populate}) => {
        assert.equal(title_text, "translated: Could not update the web interface");
        const $container = $.create("feedback");
        populate($container);
        assert.equal($container.text(), "translated: Please try again.");
    });
    web_ui_version.save("classic", on_error.f);
    assert.equal(on_error.num_calls, 1);
});
