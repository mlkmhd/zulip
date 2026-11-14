import * as blueslip from "./blueslip.ts";
import * as buddy_data from "./buddy_data.ts";
import * as hash_util from "./hash_util.ts";
import {$t} from "./i18n.ts";
import * as narrow_state from "./narrow_state.ts";
import * as people from "./people.ts";
import * as pm_conversations from "./pm_conversations.ts";
import * as unread from "./unread.ts";
import * as user_status from "./user_status.ts";
import type {UserStatusEmojiInfo} from "./user_status.ts";
import * as util from "./util.ts";

export function get_active_user_ids_string(): string | undefined {
    const filter = narrow_state.filter();

    if (!filter) {
        return undefined;
    }

    const user_ids = filter.terms_with_operator("dm")[0]?.operand;

    if (!user_ids || user_ids.length === 0) {
        return undefined;
    }

    if (!people.is_valid_user_ids(user_ids)) {
        blueslip.warn("Invalid user_ids", {user_ids});
        return undefined;
    }

    return people.sorted_other_user_ids(user_ids).join(",");
}

export type PMListConversation = {
    user_ids_string: string;
    can_pin?: boolean;
};

export type DisplayObject = {
    recipients: string;
    is_pinned?: true;
    user_ids_string: string;
    unread: number;
    is_zero: boolean;
    is_active: boolean;
    url: string;
    status_emoji_info: UserStatusEmojiInfo | undefined;
    avatar_url: string | undefined;
    avatar_initials: string | undefined;
    user_circle_class: string | undefined;
    is_group: boolean;
    is_bot: boolean;
    has_unread_mention: boolean;
    includes_deactivated_user: boolean;
};

export function get_conversations(search_string = ""): DisplayObject[] {
    const conversations = pm_conversations.recent.get();
    const pinned_conversations: DisplayObject[] = [];
    const unpinned_conversations: DisplayObject[] = [];

    // The user_ids_string for the current view, if any.
    const active_user_ids_string = get_active_user_ids_string();

    if (
        active_user_ids_string !== undefined &&
        !conversations
            .map((conversation) => conversation.user_ids_string)
            .includes(active_user_ids_string)
    ) {
        conversations.unshift({
            user_ids_string: active_user_ids_string,
            max_message_id: -1,
            local_message_count: 0,
        });
    }

    const saved_messages_name = $t({defaultMessage: "Saved Messages"});
    const matches_saved_messages =
        search_string !== "" &&
        util.prefix_match({value: saved_messages_name, search_term: search_string});
    const self_user_ids_string = String(people.my_current_user_id());
    if (
        matches_saved_messages &&
        conversations.every((conversation) => conversation.user_ids_string !== self_user_ids_string)
    ) {
        conversations.unshift({
            user_ids_string: self_user_ids_string,
            max_message_id: -1,
            local_message_count: 0,
        });
    }

    for (const conversation of conversations) {
        const user_ids_string = conversation.user_ids_string;

        const user_ids = people.user_ids_string_to_ids_array(user_ids_string);
        const users = people.get_users_from_ids(user_ids);
        const is_self_dm = people.is_direct_message_conversation_with_self(user_ids);
        if (
            !(is_self_dm && matches_saved_messages) &&
            !people.dm_matches_search_string(users, search_string)
        ) {
            // Skip adding the conversation to the display_objects array if it does
            // not match the search_term.
            continue;
        }

        const recipients_string = is_self_dm
            ? saved_messages_name
            : people.format_recipients(user_ids_string, "narrow");

        const num_unread = unread.num_unread_for_user_ids_string(user_ids_string);
        const has_unread_mention =
            unread.num_unread_mentions_for_user_ids_strings(user_ids_string) > 0;
        const is_group = user_ids_string.includes(",");
        const is_active = user_ids_string === active_user_ids_string;
        const includes_deactivated_user = user_ids.some(
            (id) => !people.is_active_user_or_system_bot(id),
        );

        let avatar_url: string | undefined;
        let avatar_initials: string | undefined;
        let user_circle_class: string | undefined;
        let status_emoji_info: UserStatusEmojiInfo | undefined;
        let is_bot = false;

        if (!is_group) {
            const user_id = Number.parseInt(user_ids_string, 10);
            user_circle_class = buddy_data.get_user_circle_class(
                user_id,
                includes_deactivated_user,
            );
            const recipient_user_obj = people.get_by_user_id(user_id);
            avatar_initials = people.default_avatar_initials(recipient_user_obj);
            if (!avatar_initials) {
                avatar_url = people.small_avatar_url_for_person(recipient_user_obj);
            }

            if (recipient_user_obj.is_bot) {
                is_bot = true;
            } else {
                status_emoji_info = user_status.get_status_emoji(user_id);
            }
        }

        const display_object: DisplayObject = {
            recipients: recipients_string,
            user_ids_string,
            unread: num_unread,
            is_zero: num_unread === 0,
            is_active,
            url: hash_util.pm_with_url(user_ids_string),
            status_emoji_info,
            avatar_url,
            avatar_initials,
            user_circle_class,
            is_group,
            is_bot,
            has_unread_mention,
            includes_deactivated_user,
        };
        if (pm_conversations.recent.is_pinned(user_ids_string)) {
            display_object.is_pinned = true;
            pinned_conversations.push(display_object);
        } else {
            unpinned_conversations.push(display_object);
        }
    }

    return [...pinned_conversations, ...unpinned_conversations];
}
