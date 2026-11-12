"use strict";

const assert = require("node:assert/strict");

const {add} = require("date-fns");

const {$t} = require("./lib/i18n.cjs");
const {clock, zrequire} = require("./lib/namespace.cjs");
const {run_test} = require("./lib/test.cjs");
const {$} = require("./lib/zjquery.cjs");

const {initialize_user_settings} = zrequire("user_settings");

const user_settings = {};
initialize_user_settings({user_settings});

const timerender = zrequire("timerender");

timerender.initialize("persian");

function get_date(time_ISO, DOW) {
    const time = new Date(time_ISO);
    // DOW helps the test reader to know the DOW of the current date being tested.
    assert.equal(new Intl.DateTimeFormat("en-US", {weekday: "long"}).format(time), DOW);
    return time;
}

const date_2017 = get_date("2017-05-18T07:12:53.000Z", "Thursday");

// Check there is no UTC offset.
assert.equal(timerender.get_tz_with_UTC_offset(date_2017), "UTC");

const date_2017_PM = get_date("2017-05-18T21:12:53.000Z", "Thursday");

const date_2019 = get_date("2019-04-12T17:52:53.000Z", "Friday");

const date_2021 = get_date("2021-01-27T01:53:08.000Z", "Wednesday");

const date_2025 = get_date("2025-03-03T12:10:00.000Z", "Monday");

run_test("get_localized_date_or_time_for_format returns correct format", () => {
    const date = date_2021;
    const formats = [
        {
            format: "time",
            expected: {
                date: "1:53 AM",
            },
        },
        {
            format: "time_sec",
            expected: {
                date: "1:53:08 AM",
            },
        },
        {
            format: "weekday",
            expected: {
                date: "چهارشنبه",
            },
        },
        {
            format: "dayofyear",
            expected: {
                date: "۸ بهمن",
            },
        },
        {
            format: "dayofyear_time",
            expected: {
                date: "۸ بهمن، ۱:۵۳ ق.ظ.",
            },
        },
        {
            format: "dayofyear_year",
            expected: {
                date: "۸ بهمن ۱۳۹۹",
            },
        },
        {
            format: "dayofyear_year_time",
            expected: {
                date: "۸ بهمن ۱۳۹۹، ۱:۵۳ ق.ظ.",
            },
        },
        {
            format: "weekday_dayofyear_year",
            expected: {
                date: "۱۳۹۹ بهمن ۸, چهارشنبه",
            },
        },
        {
            format: "weekday_dayofyear_year_time",
            expected: {
                date: "۱۳۹۹ بهمن ۸, چهارشنبه، ۱:۵۳ ق.ظ.",
            },
        },
        {
            format: "full_weekday_dayofyear_year_time",
            expected: {
                date: "۱۳۹۹ بهمن ۸, چهارشنبه ساعت ۱:۵۳ ق.ظ.",
            },
        },
    ];

    for (const format of formats) {
        const actualDate = timerender.get_localized_date_or_time_for_format(date, format.format);
        assert.equal(actualDate, format.expected.date);
    }
});

run_test("Persian dates use the display time zone at Nowruz", () => {
    const before_midnight = new Date("2024-03-19T20:29:59Z");
    const after_midnight = new Date("2024-03-19T20:30:00Z");

    timerender.set_display_time_zone("Asia/Tehran");
    try {
        const format = "dayofyear_year";
        assert.equal(
            timerender.get_localized_date_or_time_for_format(before_midnight, format),
            "۲۹ اسفند ۱۴۰۲",
        );
        assert.equal(
            timerender.get_localized_date_or_time_for_format(after_midnight, format),
            "۱ فروردین ۱۴۰۳",
        );

        const two_days_later = add(after_midnight, {days: 2});
        assert.equal(
            timerender.render_now(before_midnight, two_days_later).time_str,
            "۲۹ اسفند ۱۴۰۲",
        );
        assert.equal(timerender.render_now(after_midnight, two_days_later).time_str, "۱ فروردین");
    } finally {
        timerender.set_display_time_zone("UTC");
    }
});

run_test("get_tz_with_UTC_offset", () => {
    let time = date_2019;

    assert.equal(timerender.get_tz_with_UTC_offset(time), "UTC");

    // Test the GMT[+-]x:y logic.
    timerender.set_display_time_zone("Asia/Kolkata");
    assert.equal(timerender.get_tz_with_UTC_offset(time), "(UTC+05:30)");

    timerender.set_display_time_zone("America/Los_Angeles");
    assert.equal(timerender.get_tz_with_UTC_offset(time), "PDT (UTC-07:00)");

    time = date_2025;

    assert.equal(timerender.get_tz_with_UTC_offset(time), "PST (UTC-08:00)");

    timerender.set_display_time_zone("UTC");
});

run_test("get_utc_offset_string", () => {
    // Offsets depend on the date: standard time vs. daylight saving time.
    assert.equal(timerender.get_utc_offset_string("America/New_York", date_2025), "UTC-05:00");
    assert.equal(timerender.get_utc_offset_string("America/New_York", date_2019), "UTC-04:00");

    // Fractional-hour offsets, positive and negative.
    assert.equal(timerender.get_utc_offset_string("Asia/Kolkata", date_2025), "UTC+05:30");
    assert.equal(timerender.get_utc_offset_string("America/St_Johns", date_2025), "UTC-03:30");
    assert.equal(timerender.get_utc_offset_string("America/St_Johns", date_2019), "UTC-02:30");

    assert.equal(timerender.get_utc_offset_string("UTC", date_2025), "UTC+00:00");
});

run_test("render_now_returns_today", () => {
    clock.setSystemTime(date_2019.getTime());

    const expected = {
        time_str: $t({defaultMessage: "Today"}),
        formal_time_str: "۱۳۹۸ فروردین ۲۳, جمعه",
        needs_update: true,
    };
    const actual = timerender.render_now(date_2019);
    assert.equal(actual.time_str, expected.time_str);
    assert.equal(actual.formal_time_str, expected.formal_time_str);
    assert.equal(actual.needs_update, expected.needs_update);

    clock.reset();
});

run_test("render_now_returns_yesterday", () => {
    clock.setSystemTime(date_2019.getTime());

    const yesterday = add(date_2019, {days: -1});
    const expected = {
        time_str: $t({defaultMessage: "Yesterday"}),
        formal_time_str: "۱۳۹۸ فروردین ۲۲, پنجشنبه",
        needs_update: true,
    };
    const actual = timerender.render_now(yesterday);
    assert.equal(actual.time_str, expected.time_str);
    assert.equal(actual.formal_time_str, expected.formal_time_str);
    assert.equal(actual.needs_update, expected.needs_update);

    clock.reset();
});

run_test("render_now_returns_year", () => {
    clock.setSystemTime(date_2019.getTime());

    const year_ago = add(date_2019, {years: -1});
    const expected = {
        time_str: "۲۳ فروردین ۱۳۹۷",
        formal_time_str: "۱۳۹۷ فروردین ۲۳, پنجشنبه",
        needs_update: false,
    };
    const actual = timerender.render_now(year_ago);
    assert.equal(actual.time_str, expected.time_str);
    assert.equal(actual.formal_time_str, expected.formal_time_str);
    assert.equal(actual.needs_update, expected.needs_update);

    clock.reset();
});

run_test("render_now_returns_month_and_day", () => {
    clock.setSystemTime(date_2019.getTime());

    const three_months_ago = add(date_2019, {months: -3});
    const expected = {
        time_str: "۲۲ دی ۱۳۹۷",
        formal_time_str: "۱۳۹۷ دی ۲۲, شنبه",
        needs_update: false,
    };
    const actual = timerender.render_now(three_months_ago);
    assert.equal(actual.time_str, expected.time_str);
    assert.equal(actual.formal_time_str, expected.formal_time_str);
    assert.equal(actual.needs_update, expected.needs_update);

    clock.reset();
});

run_test("format_time_modern", () => {
    const today = date_2021;

    const few_minutes_in_future = add(today, {minutes: 30});
    const weeks_in_future = add(today, {days: 20});
    const less_than_24_hours_ago = add(today, {hours: -23});
    const twenty_four_hours_ago = add(today, {hours: -24});
    const more_than_24_hours_ago = add(today, {hours: -25});
    const less_than_a_week_ago = add(today, {days: -6});
    const one_week_ago = add(today, {days: -7});
    const less_than_6_months_ago = add(today, {months: -3});
    const more_than_6_months_ago = add(today, {months: -9});
    const previous_year_but_less_than_6_months = add(today, {months: -1});

    assert.equal(timerender.format_time_modern(few_minutes_in_future, today), "۸ بهمن ۱۳۹۹");
    assert.equal(timerender.format_time_modern(weeks_in_future, today), "۲۸ بهمن ۱۳۹۹");
    assert.equal(timerender.format_time_modern(less_than_24_hours_ago, today), "2:53 AM");
    assert.equal(
        timerender.format_time_modern(twenty_four_hours_ago, today),
        "translated: Yesterday",
    );
    assert.equal(
        timerender.format_time_modern(more_than_24_hours_ago, today),
        "translated: Yesterday",
    );
    assert.equal(timerender.format_time_modern(less_than_a_week_ago, today), "پنجشنبه");
    assert.equal(timerender.format_time_modern(one_week_ago, today), "۱ بهمن");
    assert.equal(
        timerender.format_time_modern(previous_year_but_less_than_6_months, today),
        "۷ دی",
    );
    assert.equal(timerender.format_time_modern(less_than_6_months_ago, today), "۶ آبان");
    assert.equal(timerender.format_time_modern(more_than_6_months_ago, today), "۸ اردیبهشت ۱۳۹۹");
});

run_test("format_time_modern_different_timezones", () => {
    // Day is yesterday in UTC+0 but is 2 days ago in local timezone hence DOW is returned.
    let today = date_2017_PM;
    let yesterday = add(date_2017, {days: -1});
    assert.equal(timerender.format_time_modern(yesterday, today), "translated: Yesterday");

    timerender.set_display_time_zone("America/Juneau");
    let expected = "translated: ۱۳۹۶/۲/۲۶ at 11:12:53 PM AKDT (UTC-08:00)";
    assert.equal(timerender.get_full_datetime_clarification(yesterday), expected);
    assert.equal(timerender.format_time_modern(yesterday, today), "سه‌شنبه");
    timerender.set_display_time_zone("UTC");

    // Day is 2 days ago in UTC+0 but is yesterday in local timezone.
    today = date_2017;
    yesterday = add(date_2017_PM, {days: -2});
    assert.equal(timerender.format_time_modern(yesterday, today), "سه‌شنبه");

    timerender.set_display_time_zone("Asia/Brunei");
    expected = "translated: ۱۳۹۶/۲/۲۷ at 5:12:53 AM (UTC+08:00)";
    assert.equal(timerender.get_full_datetime_clarification(yesterday), expected);
    assert.equal(timerender.format_time_modern(yesterday, today), "translated: Yesterday");
    timerender.set_display_time_zone("UTC");

    // Day is 6 days ago in UTC+0 but a week ago in local timezone hence difference in returned strings.
    today = date_2017_PM;
    yesterday = add(date_2017, {days: -6});
    assert.equal(timerender.format_time_modern(yesterday, today), "جمعه");

    timerender.set_display_time_zone("America/Juneau");
    expected = "translated: ۱۳۹۶/۲/۲۱ at 11:12:53 PM AKDT (UTC-08:00)";
    assert.equal(timerender.get_full_datetime_clarification(yesterday), expected);
    assert.equal(timerender.format_time_modern(yesterday, today), "۲۱ اردیبهشت");
    timerender.set_display_time_zone("UTC");
});

run_test("render_now_returns_year_with_year_boundary", () => {
    clock.setSystemTime(date_2019.getTime());

    const six_months_ago = add(date_2019, {months: -6});
    const expected = {
        time_str: "۲۰ مهر ۱۳۹۷",
        formal_time_str: "۱۳۹۷ مهر ۲۰, جمعه",
        needs_update: false,
    };
    const actual = timerender.render_now(six_months_ago);
    assert.equal(actual.time_str, expected.time_str);
    assert.equal(actual.formal_time_str, expected.formal_time_str);
    assert.equal(actual.needs_update, expected.needs_update);

    clock.reset();
});

run_test("render_date_renders_time_html", () => {
    timerender.clear_for_testing();

    const today = date_2019;
    clock.setSystemTime(today.getTime());

    const message_time = today;
    const expected_text = $t({defaultMessage: "Today"});

    const $span_stub = $("<span>");
    timerender.render_date(message_time);
    assert.equal($span_stub.text(), expected_text);
    assert.equal($span_stub.attr("data-tippy-content"), "۱۳۹۸ فروردین ۲۳, جمعه");
    assert.equal($span_stub.attr("class"), "date_row_text timerender-content timerender0");

    clock.reset();
});

run_test("get_full_time", () => {
    const timestamp = date_2017.getTime() / 1000;
    const expected = "2017-05-18T07:12:53Z"; // ISO 8601 date format
    const actual = timerender.get_full_time(timestamp);
    assert.equal(actual, expected);
});

run_test("get_timestamp_for_flatpickr", () => {
    const func = timerender.get_timestamp_for_flatpickr;
    // Freeze time for testing.
    clock.setSystemTime(date_2017.getTime());

    // Invalid timestamps should show current time on the hour.
    const date_without_minutes = new Date();
    date_without_minutes.setMinutes(0, 0);
    assert.equal(func("random str").valueOf(), date_without_minutes.getTime());

    // Valid ISO timestamps should return the timestamp.
    assert.equal(func(date_2017.toISOString()).valueOf(), date_2017.getTime());

    // Restore the Date object.
    clock.reset();
});

run_test("absolute_time_12_hour", ({override}) => {
    override(user_settings, "twenty_four_hour_time", false);

    // timestamp with hour > 12, same year
    let timestamp = date_2019.getTime();

    let today = date_2019;
    clock.setSystemTime(today.getTime());
    let expected = "۲۳ فروردین، ۵:۵۲ ب.ظ.";
    let actual = timerender.absolute_time(timestamp);
    assert.equal(actual, expected);

    // timestamp with hour > 12, different year
    let next_year = add(today, {years: 1});
    clock.setSystemTime(next_year.getTime());
    expected = "۲۳ فروردین ۱۳۹۸، ۵:۵۲ ب.ظ.";
    actual = timerender.absolute_time(timestamp);
    assert.equal(actual, expected);

    // timestamp with hour < 12, same year
    timestamp = date_2017.getTime();

    today = date_2017;
    clock.setSystemTime(today.getTime());
    expected = "۲۸ اردیبهشت، ۷:۱۲ ق.ظ.";
    actual = timerender.absolute_time(timestamp);
    assert.equal(actual, expected);

    // timestamp with hour < 12, different year
    next_year = add(today, {years: 1});
    clock.setSystemTime(next_year.getTime());
    expected = "۲۸ اردیبهشت ۱۳۹۶، ۷:۱۲ ق.ظ.";
    actual = timerender.absolute_time(timestamp);
    assert.equal(actual, expected);

    clock.reset();
});

run_test("absolute_time_24_hour", ({override}) => {
    override(user_settings, "twenty_four_hour_time", true);

    // date with hour > 12, same year
    let today = date_2019;
    clock.setSystemTime(today.getTime());
    let expected = "۲۳ فروردین، ۱۷:۵۲";
    let actual = timerender.absolute_time(date_2019.getTime());
    assert.equal(actual, expected);

    // date with hour > 12, different year
    let next_year = add(today, {years: 1});
    clock.setSystemTime(next_year.getTime());
    expected = "۲۳ فروردین ۱۳۹۸، ۱۷:۵۲";
    actual = timerender.absolute_time(date_2019.getTime());
    assert.equal(actual, expected);

    // timestamp with hour < 12, same year
    today = date_2017;
    clock.setSystemTime(today.getTime());
    expected = "۲۸ اردیبهشت، ۰۷:۱۲";
    actual = timerender.absolute_time(date_2017.getTime());
    assert.equal(actual, expected);

    // timestamp with hour < 12, different year
    next_year = add(today, {years: 1});
    clock.setSystemTime(next_year.getTime());
    expected = "۲۸ اردیبهشت ۱۳۹۶، ۰۷:۱۲";
    actual = timerender.absolute_time(date_2017.getTime());
    assert.equal(actual, expected);

    clock.reset();
});

run_test("get_full_datetime", ({override}) => {
    const time = date_2017_PM;

    let expected = "translated: ۱۳۹۶/۲/۲۸ at 9:12:53 PM UTC";
    assert.equal(timerender.get_full_datetime_clarification(time), expected);
    expected = "translated: ۲۸ اردیبهشت ۱۳۹۶ at 9:12:53 PM";
    assert.equal(timerender.get_full_datetime(time), expected);

    expected = "translated: ۱۳۹۶/۲/۲۸ at 9:12 PM UTC";
    assert.equal(timerender.get_full_datetime_clarification(time, "time"), expected);
    expected = "translated: ۲۸ اردیبهشت ۱۳۹۶ at 9:12 PM";
    assert.equal(timerender.get_full_datetime(time, "time"), expected);

    // test 24 hour time setting.
    override(user_settings, "twenty_four_hour_time", true);
    expected = "translated: ۱۳۹۶/۲/۲۸ at 21:12:53 UTC";
    assert.equal(timerender.get_full_datetime_clarification(time), expected);
    expected = "translated: ۲۸ اردیبهشت ۱۳۹۶ at 21:12:53";
    assert.equal(timerender.get_full_datetime(time), expected);

    override(user_settings, "twenty_four_hour_time", false);

    // Test the GMT[+-]x:y logic.
    timerender.set_display_time_zone("Asia/Kolkata");
    expected = "translated: ۱۳۹۶/۲/۲۹ at 2:42:53 AM (UTC+05:30)";
    assert.equal(timerender.get_full_datetime_clarification(time), expected);
    expected = "translated: ۲۹ اردیبهشت ۱۳۹۶ at 2:42:53 AM";
    assert.equal(timerender.get_full_datetime(time), expected);
    timerender.set_display_time_zone("UTC");
});

run_test("last_seen_status_from_date", () => {
    // Set base_date to March 1 2016 12.30 AM (months are zero based)
    let base_date = new Date(2016, 2, 1, 0, 30);
    clock.setSystemTime(base_date.getTime());

    function assert_same(duration, expected_status) {
        const past_date = add(base_date, duration);
        const actual_status = timerender.last_seen_status_from_date(past_date, base_date);
        assert.equal(actual_status, expected_status);
    }

    assert_same({minutes: -30}, $t({defaultMessage: "Active 30 minutes ago"}));

    assert_same({hours: -1}, $t({defaultMessage: "Active an hour ago"}));

    assert_same({hours: -2}, $t({defaultMessage: "Active 2 hours ago"}));

    assert_same({hours: -20}, $t({defaultMessage: "Active 20 hours ago"}));

    assert_same({hours: -24}, $t({defaultMessage: "Active yesterday"}));

    assert_same({hours: -48}, $t({defaultMessage: "Active 2 days ago"}));

    assert_same({days: -2}, $t({defaultMessage: "Active 2 days ago"}));

    assert_same({days: -61}, $t({defaultMessage: "Active 61 days ago"}));

    assert_same({days: -300}, $t({defaultMessage: "Active ۱۶ اردیبهشت"}));

    assert_same({days: -366}, $t({defaultMessage: "Active ۱۰ اسفند ۱۳۹۳"}));

    assert_same({years: -3}, $t({defaultMessage: "Active ۱۱ اسفند ۱۳۹۱"}));

    // Set base_date to May 1 2016 12.30 AM (months are zero based)
    base_date = new Date(2016, 4, 1, 0, 30);
    clock.setSystemTime(base_date.getTime());

    assert_same({days: -91}, $t({defaultMessage: "Active ۱۱ بهمن ۱۳۹۴"}));

    // Set base_date to May 1 2016 10.30 PM (months are zero based)
    base_date = new Date(2016, 4, 2, 23, 30);
    clock.setSystemTime(base_date.getTime());

    assert_same({hours: -1}, $t({defaultMessage: "Active an hour ago"}));

    assert_same({hours: -2}, $t({defaultMessage: "Active 2 hours ago"}));

    assert_same({hours: -12}, $t({defaultMessage: "Active 12 hours ago"}));

    assert_same({hours: -24}, $t({defaultMessage: "Active yesterday"}));

    clock.reset();
});

run_test("relative_time_string_from_date", () => {
    // Set base_date to March 1 2016 12.30 AM (months are zero based)
    let base_date = new Date(2016, 2, 1, 0, 30);
    clock.setSystemTime(base_date.getTime());

    function assert_same(duration, expected_status) {
        const past_date = add(base_date, duration);
        const actual_status = timerender.relative_time_string_from_date(past_date);
        assert.equal(actual_status, expected_status);
    }

    assert_same({seconds: -20}, $t({defaultMessage: "Just now"}));

    assert_same({minutes: -1}, $t({defaultMessage: "Just now"}));

    assert_same({minutes: -2}, $t({defaultMessage: "Just now"}));

    assert_same({minutes: -30}, $t({defaultMessage: "30 minutes ago"}));

    assert_same({hours: -1}, $t({defaultMessage: "An hour ago"}));

    assert_same({hours: -2}, $t({defaultMessage: "2 hours ago"}));

    assert_same({hours: -20}, $t({defaultMessage: "20 hours ago"}));

    assert_same({hours: -24}, $t({defaultMessage: "Yesterday"}));

    assert_same({hours: -48}, $t({defaultMessage: "2 days ago"}));

    assert_same({days: -2}, $t({defaultMessage: "2 days ago"}));

    assert_same({days: -61}, $t({defaultMessage: "61 days ago"}));

    assert_same({days: -300}, "۱۶ اردیبهشت");

    assert_same({days: -366}, "۱۰ اسفند ۱۳۹۳");

    assert_same({years: -3}, "۱۱ اسفند ۱۳۹۱");

    // Set base_date to May 1 2016 12.30 AM (months are zero based)
    base_date = new Date(2016, 4, 1, 0, 30);
    clock.setSystemTime(base_date.getTime());

    assert_same({days: -91}, "۱۱ بهمن ۱۳۹۴");

    // Set base_date to May 1 2016 10.30 PM (months are zero based)
    base_date = new Date(2016, 4, 2, 23, 30);
    clock.setSystemTime(base_date.getTime());

    assert_same({hours: -1}, $t({defaultMessage: "An hour ago"}));

    assert_same({hours: -2}, $t({defaultMessage: "2 hours ago"}));

    assert_same({hours: -12}, $t({defaultMessage: "12 hours ago"}));

    assert_same({hours: -24}, $t({defaultMessage: "Yesterday"}));

    clock.reset();
});

run_test("set_full_datetime", ({override}) => {
    let time = date_2019;

    override(user_settings, "twenty_four_hour_time", true);
    let time_str = timerender.stringify_time(time);
    let expected = "17:52";
    assert.equal(time_str, expected);

    override(user_settings, "twenty_four_hour_time", false);
    time_str = timerender.stringify_time(time);
    expected = "5:52 PM";
    assert.equal(time_str, expected);

    time = add(time, {hours: -7}); // time between 1 to 12 o'clock time.
    override(user_settings, "twenty_four_hour_time", false);
    time_str = timerender.stringify_time(time);
    expected = "10:52 AM";
    assert.equal(time_str, expected);
});

run_test("are_timezones_on_same_clock_now", () => {
    assert.ok(
        timerender.are_timezones_on_same_clock_now("America/Los_Angeles", "America/Los_Angeles"),
    );
    assert.ok(timerender.are_timezones_on_same_clock_now("America/New_York", "America/New_York"));

    // Montreal and Toronto are equivalent year-round.
    assert.ok(timerender.are_timezones_on_same_clock_now("America/Montreal", "America/Toronto"));

    assert.ok(
        !timerender.are_timezones_on_same_clock_now("Invalid/Timezone", "America/Los_Angeles"),
    );
    assert.ok(
        !timerender.are_timezones_on_same_clock_now("America/New_York", "America/Los_Angeles"),
    );
});

run_test("are_timezones_on_same_clock_now (Phoenix)", () => {
    /*
        If you live in Phoenix and make the short flight to Los Angeles during
        Daylight Savings time, you don't need to reset your watch.

        Likewise, during the winter, if you go to Denver, you don't need to
        reset your watch.

        We err on the side of not nagging Phoenix folks to change their
        "Zulip clock" for these short trips.
    */
    assert.ok(
        timerender.are_timezones_on_same_clock_now("America/Phoenix", "America/Los_Angeles") ||
            timerender.are_timezones_on_same_clock_now("America/Phoenix", "America/Denver"),
    );
});

run_test("is_browser_timezone_same_as", () => {
    assert.equal(timerender.is_browser_timezone_same_as(timerender.browser_time_zone()), true);

    // This just ensures that the function doesn't always return true
    assert.equal(timerender.is_browser_timezone_same_as("Invalid/Timezone"), false);
});

run_test("time zone helpers", () => {
    function get_time_in_timezone(date, timezone) {
        return timerender.get_time_in_timezone(date, timezone);
    }

    function get_offset_difference_at_date(tz1, tz2, reference_date) {
        return timerender.get_offset_difference_at_date(tz1, tz2, reference_date);
    }

    // The current time in America/Phoenix does equal the current time
    // in one of the two other time zones
    const now = new Date();
    const now_phoenix = get_time_in_timezone(now, "America/Phoenix");
    const now_denver = get_time_in_timezone(now, "America/Denver");
    const now_la = get_time_in_timezone(now, "America/Los_Angeles");

    // Both conditions cannot simultaneously be true since we know we can
    // tell timezones apart and DST will not cease to be observed any time soon.
    // So we can OR the two conditions.
    assert.equal(now_denver === now_phoenix || now_la === now_phoenix, true);

    const dst_date = new Date("Sat, 17 Jul 2024 11:05:12 GMT");
    // The offset difference between America/Phoenix and America/Los_Angeles is 0
    // when DST is in effect in America/Los_Angeles.
    assert.equal(
        get_offset_difference_at_date("America/Los_Angeles", "America/Phoenix", dst_date),
        0,
    );
    // and similarly for Phoenix and Denver, the offset difference is -1 hour.
    assert.equal(
        get_offset_difference_at_date("America/Phoenix", "America/Denver", dst_date),
        -3600000,
    );

    const non_dst_date = new Date("Sat, 17 Feb 2024 11:05:12 GMT");
    // The offset difference between America/Phoenix and America/Los_Angeles is -1 hour
    // when DST is not in effect in America/Los_Angeles.
    assert.equal(
        get_offset_difference_at_date("America/Los_Angeles", "America/Phoenix", non_dst_date),
        -3600000,
    );
    // and similarly for Phoenix and Denver, the offset difference is 0.
    assert.equal(
        get_offset_difference_at_date("America/Phoenix", "America/Denver", non_dst_date),
        0,
    );
});

run_test("automatic calendar and manual overrides", ({override}) => {
    const date = new Date("2024-03-20T12:00:00Z");
    override(user_settings, "default_language", "en-US");
    override(user_settings, "timezone", "Iran/Tehran");
    timerender.initialize();
    assert.equal(timerender.display_time_zone, "Asia/Tehran");
    assert.equal(
        timerender.get_localized_date_or_time_for_format(date, "dayofyear_year"),
        "۱ فروردین ۱۴۰۳",
    );

    timerender.initialize("gregory");
    assert.equal(
        timerender.get_localized_date_or_time_for_format(date, "dayofyear_year"),
        "Mar 20, 2024",
    );

    override(user_settings, "timezone", "UTC");
    timerender.initialize();
    assert.equal(
        timerender.get_localized_date_or_time_for_format(date, "dayofyear_year"),
        "Mar 20, 2024",
    );

    override(user_settings, "timezone", "");
    timerender.initialize();
    assert.equal(timerender.display_time_zone, timerender.browser_time_zone());
});
