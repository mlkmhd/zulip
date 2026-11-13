import _ from "lodash";

import render_more_private_conversations from "../templates/more_pms.hbs";
import render_pm_list_item from "../templates/pm_list_item.hbs";

import * as pm_conversations from "./pm_conversations.ts";
import type {PMListConversation} from "./pm_list_data.ts";
import * as vdom from "./vdom.ts";

export type PMNode =
    | {
          type: "conversation";
          conversation: PMListConversation;
      }
    | {
          type: "more_items";
          more_conversations_unread_count: number;
      };

export function keyed_pm_li(conversation: PMListConversation): vdom.Node<PMNode> {
    const can_pin =
        (pm_conversations.recent.recent_message_ids.get(conversation.user_ids_string)
            ?.max_message_id ?? -1) > 0;
    const row = {...conversation, can_pin};
    const render = (): string => render_pm_list_item(row);

    const eq = (other: PMNode): boolean =>
        other.type === "conversation" && _.isEqual(row, other.conversation);

    const key = conversation.user_ids_string;

    return {
        key,
        render,
        eq,
        type: "conversation",
        conversation: row,
    };
}

export function more_private_conversations_li(
    more_conversations_unread_count: number,
): vdom.Node<PMNode> {
    const render = (): string =>
        render_more_private_conversations({more_conversations_unread_count});

    // Used in vdom.js to check if an element has changed and needs to
    // be updated in the DOM.
    const eq = (other: PMNode): boolean =>
        other.type === "more_items" &&
        more_conversations_unread_count === other.more_conversations_unread_count;

    // This special key must be impossible as a user_ids_string.
    const key = "more_private_conversations";

    return {
        key,
        render,
        eq,
        type: "more_items",
        more_conversations_unread_count,
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
