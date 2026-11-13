import {$} from "jquery";
import _ from "lodash";
import * as z from "zod/mini";

import * as channel from "./channel.ts";
import type {Filter} from "./filter.ts";
import {$t} from "./i18n.ts";
import {localstorage} from "./localstorage.ts";
import * as mouse_drag from "./mouse_drag.ts";
import * as muted_users from "./muted_users.ts";
import * as people from "./people.ts";
import * as pm_conversations from "./pm_conversations.ts";
import * as pm_list_data from "./pm_list_data.ts";
import * as pm_list_dom from "./pm_list_dom.ts";
import type {PMNode} from "./pm_list_dom.ts";
import * as resize from "./resize.ts";
import * as scroll_util from "./scroll_util.ts";
import * as ui_util from "./ui_util.ts";
import type {FullUnreadCountsData} from "./unread.ts";
import * as util from "./util.ts";
import * as vdom from "./vdom.ts";

export const LEFT_SIDEBAR_DIRECT_MESSAGES_TITLE = $t({defaultMessage: "DIRECT MESSAGES"});

let prior_dom: vdom.Tag<PMNode> | undefined;

// This module manages the direct messages section in the upper
// left corner of the app.  This was split out from stream_list.ts.

const ls_key = "left_sidebar_direct_messages_collapsed_state";
const ls_schema = z._default(z.boolean(), false);
const ls = localstorage();
let private_messages_collapsed = false;
let last_direct_message_count: number | undefined;

function get_private_messages_section_header(): JQuery {
    return $("#direct-messages-section-header");
}

export function set_count(count: number): void {
    ui_util.update_unread_count_in_dom(get_private_messages_section_header(), count);
}

export function close(): void {
    private_messages_collapsed = true;
    ls.set(ls_key, private_messages_collapsed);
    update_private_messages();
}

const preview_response_schema = z.object({
    messages: z.array(z.object({id: z.number(), content: z.string()})),
});

type DMPreview = {
    conversation_version: number;
    pending: boolean;
    message_id?: number | undefined;
    text?: string | undefined;
};

const dm_previews = new Map<string, DMPreview>();

export function invalidate_message_previews(message_ids: ReadonlySet<number>): boolean {
    if (message_ids.size === 0) {
        return false;
    }

    let changed = false;
    for (const [key, preview] of dm_previews) {
        if (
            preview.pending ||
            (preview.message_id !== undefined && message_ids.has(preview.message_id))
        ) {
            dm_previews.delete(key);
            changed = true;
        }
    }
    return changed;
}

function preview_version(key: string): number | undefined {
    if (
        key.includes(",") ||
        people.is_my_user_id(Number(key)) ||
        muted_users.is_user_muted(Number(key))
    ) {
        return undefined;
    }
    return pm_conversations.recent.recent_message_ids.get(key)?.max_message_id;
}

function fetch_preview(key: string): void {
    const version = preview_version(key);
    const cached = dm_previews.get(key);
    if (version === undefined || cached?.conversation_version === version) {
        return;
    }
    // Keep the previous preview visible while checking for a newer message.
    const preview: DMPreview = {...cached, conversation_version: version, pending: true};
    dm_previews.set(key, preview);
    void channel.get({
        url: "/json/messages",
        data: {
            narrow: JSON.stringify([{operator: "dm", operand: [Number(key)]}]),
            anchor: "newest",
            num_before: 1,
            num_after: 0,
        },
        success(raw_data) {
            if (dm_previews.get(key) !== preview) {
                return;
            }

            const {messages} = preview_response_schema.parse(raw_data);
            const message = messages[0];
            preview.pending = false;
            preview.message_id = message?.id;
            preview.text = undefined;
            if (message !== undefined) {
                const template = document.createElement("template");
                template.innerHTML = message.content;
                for (const image of template.content.querySelectorAll("img")) {
                    image.replaceWith(image.alt);
                }
                for (const block of template.content.querySelectorAll(
                    "p, li, br, pre, blockquote",
                )) {
                    block.after(" ");
                }
                const text = (template.content.textContent ?? "").replaceAll(/\s+/g, " ").trim();
                preview.text = text === "" ? $t({defaultMessage: "Attachment"}) : text;
            }
            refresh_previews();
        },
        error() {
            if (dm_previews.get(key) === preview) {
                dm_previews.delete(key);
            }
        },
    });
}

const refresh_previews = _.debounce(update_private_messages, 50);
let preview_observer: IntersectionObserver | undefined;

function set_dom_to(new_dom: vdom.Tag<PMNode>): void {
    const $container = $("#direct-messages-list");

    function replace_content(html: string): void {
        $container.html(html);
    }

    function find(): JQuery {
        return $container.find("ul");
    }

    const focused_pin = $container.find(".dm-pin-button:focus").get(0);
    const focused_conversation = focused_pin
        ?.closest(".dm-list-item")
        ?.getAttribute("data-user-ids-string");
    vdom.update(replace_content, find, new_dom, prior_dom);
    if (focused_conversation && !focused_pin?.isConnected) {
        const $row = $container.find(
            `.dm-list-item[data-user-ids-string="${CSS.escape(focused_conversation)}"]`,
        );
        $row.find(".dm-pin-button").get(0)?.focus({preventScroll: true});
    }
    prior_dom = new_dom;
    pm_list_dom.update_typing_indicators();

    preview_observer?.disconnect();
    if (preview_observer) {
        for (const row of $container.find(".dm-list-item")) {
            const key = row.dataset["userIdsString"]!;
            const version = preview_version(key);
            if (version !== undefined && dm_previews.get(key)?.conversation_version !== version) {
                preview_observer.observe(row);
            }
        }
    }
}

export function update_private_messages(): void {
    const is_left_sidebar_search_active = ui_util.get_left_sidebar_search_term() !== "";
    const is_dm_section_expanded = is_left_sidebar_search_active || !private_messages_collapsed;
    $("#toggle-direct-messages-section-icon")
        .toggleClass("rotate-icon-down", is_dm_section_expanded)
        .toggleClass("rotate-icon-right", !is_dm_section_expanded);

    let search_term = ui_util.get_left_sidebar_search_term();
    if (util.prefix_match({value: LEFT_SIDEBAR_DIRECT_MESSAGES_TITLE, search_term})) {
        search_term = "";
    }

    const conversations = pm_list_data.get_conversations(search_term);
    const is_header_visible = conversations.length > 0 || !search_term;
    $("#left-sidebar").toggleClass("direct-messages-hidden-by-filters", !is_header_visible);

    // Keep the selected conversation highlighted when the section is collapsed.
    const visible_conversations = is_dm_section_expanded
        ? conversations
        : conversations.filter((conversation) => conversation.is_active);
    const nodes = visible_conversations.map((conversation) => {
        const key = conversation.user_ids_string;
        const preview = preview_version(key) === undefined ? undefined : dm_previews.get(key)?.text;
        const can_pin =
            (pm_conversations.recent.recent_message_ids.get(key)?.max_message_id ?? -1) > 0;
        return pm_list_dom.keyed_pm_li({...conversation, preview, can_pin});
    });
    set_dom_to(pm_list_dom.pm_ul(nodes));
    // Make sure to update the left sidebar heights after updating
    // direct messages.
    setTimeout(resize.resize_stream_filters_container, 0);
}

export function expand(): void {
    private_messages_collapsed = false;
    ls.set(ls_key, private_messages_collapsed);
    update_private_messages();
}

export function update_dom_with_unread_counts(
    counts: FullUnreadCountsData,
    skip_animations = false,
): void {
    // In theory, we could support passing the counts object through
    // to pm_list_data, rather than fetching it directly there. But
    // it's not an important optimization, because it's unlikely a
    // user would have 10,000s of unread direct messages where it
    // could matter.
    update_private_messages();

    // This is just the global unread count.
    const new_direct_message_count = counts.direct_message_count;
    set_count(new_direct_message_count);

    if (last_direct_message_count === undefined) {
        // We don't want to animate the DM header
        // when Zulip first loads, but we must update
        // the last DM count to correctly animate
        // the arrival of new unread DMs.
        last_direct_message_count = new_direct_message_count;
        return;
    }

    if (new_direct_message_count > last_direct_message_count && !skip_animations) {
        const $dm_header = $("#direct-messages-section-header");
        const $top_dm_item = $(".dm-list .dm-list-item:first-child");
        const top_item_is_pinned = $top_dm_item.find(".dm-is-pinned").length > 0;
        const top_item_active = $top_dm_item.hasClass("active-sub-filter");
        const top_item_no_unreads = $top_dm_item.hasClass("zero-dm-unreads");
        const $scroll_wrapper = $("#left_sidebar_scroll_container .simplebar-content-wrapper");
        let dms_scrolled_up = false;

        if ($scroll_wrapper.length > 0) {
            const scroll_top = $scroll_wrapper.scrollTop() ?? 0;
            dms_scrolled_up = scroll_top > 0;
        }
        // If the DMs area is scrolled up at all, we highlight the
        // DM header's count. It is possible for the DMs section to
        // be collapsed *and* the active conversation be scrolled
        // out of view, too, so we err on the side of highlighting
        // the header row.
        // If the DMs area is collapsed without the top item being
        // active, as is the case when narrowed to a DM, or if the
        // active DM item has the .zero-dm-unreads class, we highlight
        // the DM header's count.
        // That makes the assumption that a new DM has arrived in a
        // conversation other than the active one. Note that that will
        // fail animate anything--the header or the row--when an unread
        // arrives for a conversion other than the active one. But in
        // typical active DMing, unreads will be cleared immediately,
        // so that should be a fairly rare edge case.
        if (
            dms_scrolled_up ||
            (is_private_messages_collapsed() && !top_item_active) ||
            top_item_no_unreads ||
            top_item_is_pinned
        ) {
            ui_util.do_new_unread_animation($dm_header);
        }
        // Unless the top item has the active-sub-filter class, which
        // we won't highlight to avoid annoying users in an active,
        // ongoing conversation, we highlight the top DM row, where
        // the newly arrived unread message will be, as the DM list
        // will be resorted by the time this logic runs.
        else if (!top_item_active) {
            ui_util.do_new_unread_animation($top_dm_item);
        }
    }

    last_direct_message_count = new_direct_message_count;
}

function highlight_all_private_messages_view(): void {
    $(".direct-messages-container.zoomed-out").addClass("active-direct-messages-section");
}

function unhighlight_all_private_messages_view(): void {
    $(".direct-messages-container.zoomed-out").removeClass("active-direct-messages-section");
}

function scroll_pm_into_view($target_li: JQuery): void {
    const $container = $("#left_sidebar_scroll_container");
    const pm_header_height = $("#direct-messages-section-header").outerHeight();
    if ($target_li.length > 0) {
        scroll_util.scroll_element_into_container($target_li, $container, pm_header_height);
    }
}

function scroll_all_private_into_view(): void {
    const $container = $("#left_sidebar_scroll_container");
    const $scroll_element = scroll_util.get_scroll_element($container);
    $scroll_element.scrollTop(0);
}

export function handle_narrow_activated(filter: Filter): void {
    const active_filter = filter;
    const is_all_private_message_view = _.isEqual(active_filter.sorted_term_types(), ["is-dm"]);
    const narrow_to_private_messages_section = active_filter.terms_with_operator("dm").length > 0;
    const is_private_messages_in_view = active_filter.has_operator("dm");

    if (is_all_private_message_view) {
        // In theory, this should get expanded when we scroll to the
        // top, but empirically that doesn't occur, so we just ensure the
        // section is expanded before scrolling.
        expand();
        highlight_all_private_messages_view();
        scroll_all_private_into_view();
    } else {
        unhighlight_all_private_messages_view();
    }
    if (narrow_to_private_messages_section) {
        const current_user_ids_string = pm_list_data.get_active_user_ids_string();
        if (current_user_ids_string !== undefined) {
            const $active_filter_li = $(
                `li[data-user-ids-string='${CSS.escape(current_user_ids_string)}']`,
            );
            scroll_pm_into_view($active_filter_li);
        }
        update_private_messages();
    } else if (!is_private_messages_in_view) {
        update_private_messages();
    }
}

export function handle_message_view_deactivated(): void {
    // Since one can renarrow via the keyboard shortcut or similar, we
    // avoid disturbing the zoomed state here.
    unhighlight_all_private_messages_view();
    update_private_messages();
}

export function is_private_messages_collapsed(): boolean {
    return private_messages_collapsed;
}

export function toggle_private_messages_section(): void {
    // change the state of direct message section depending on
    // the previous state.
    if (private_messages_collapsed) {
        expand();
    } else {
        close();
    }
}

export function initialize(): void {
    if (typeof IntersectionObserver !== "undefined") {
        preview_observer = new IntersectionObserver((entries, observer) => {
            for (const entry of entries) {
                if (entry.isIntersecting && entry.target instanceof HTMLElement) {
                    observer.unobserve(entry.target);
                    fetch_preview(entry.target.dataset["userIdsString"]!);
                }
            }
        });
    }

    // Restore collapsed status.
    private_messages_collapsed = ls_schema.parse(ls.get(ls_key));
    if (private_messages_collapsed) {
        close();
    } else {
        expand();
    }

    $("#direct-messages-list").on("click", ".dm-box", (e) => {
        // To avoid the click behavior if a dm box is selected.
        if (mouse_drag.is_drag(e)) {
            e.preventDefault();
        }
    });

    $("#direct-messages-list").on("click", ".dm-pin-button", function (this: HTMLElement) {
        const user_ids_string = this.closest(".dm-list-item")?.getAttribute("data-user-ids-string");
        if (user_ids_string === null || user_ids_string === undefined) {
            return;
        }

        const user_ids = people
            .user_ids_string_to_ids_array(user_ids_string)
            .filter((id) => !people.is_my_user_id(id));
        void channel.post({
            url: "/json/users/me/dm_conversations/pin",
            data: {
                user_ids: JSON.stringify(user_ids),
                pinned: JSON.stringify(!pm_conversations.recent.is_pinned(user_ids_string)),
            },
        });
    });

    $(".direct-messages-container").on("mouseenter", () => {
        $("#direct-messages-section-header").addClass("hover-over-dm-section");
    });

    $(".direct-messages-container").on("mouseleave", () => {
        $("#direct-messages-section-header").removeClass("hover-over-dm-section");
    });
}
