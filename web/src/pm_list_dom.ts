import {$} from "jquery";
import _ from "lodash";

import render_pm_list_item from "../templates/pm_list_item.hbs";

import type {PMListConversation} from "./pm_list_data.ts";
import {current_user} from "./state_data.ts";
import * as typing_data from "./typing_data.ts";
import * as vdom from "./vdom.ts";

export type PMNode = {conversation: PMListConversation};

export function update_typing_indicators(): void {
    for (const avatar_indicator of $("#direct-messages-list .dm-avatar-typing")) {
        const user_id = Number(
            avatar_indicator.closest(".dm-box")?.getAttribute("data-user-ids-string"),
        );
        const is_typing = typing_data
            .get_group_typists([current_user.user_id, user_id])
            .includes(user_id);
        avatar_indicator.hidden = !is_typing;
    }
}

export function keyed_pm_li(conversation: PMListConversation): vdom.Node<PMNode> {
    const render = (): string => render_pm_list_item(conversation);

    const eq = (other: PMNode): boolean => _.isEqual(conversation, other.conversation);

    const key = conversation.user_ids_string;

    return {
        key,
        render,
        eq,
        conversation,
    };
}

export function pm_ul(nodes: vdom.Node<PMNode>[]): vdom.Tag<PMNode> {
    const attrs: [string, string][] = [
        ["class", "dm-list"],
        ["data-name", "private"],
    ];
    return vdom.ul({
        attrs,
        keyed_nodes: nodes,
    });
}
