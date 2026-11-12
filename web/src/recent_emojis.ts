import * as z from "zod/mini";

import type {EmojiRenderingDetails} from "./emoji.ts";
import {localstorage} from "./localstorage.ts";
import {current_user} from "./state_data.ts";

const MAX_RECENT_EMOJIS = 30;
const storage = localstorage();
const recent_emoji_schema = z.object({
    emoji_code: z.string(),
    reaction_type: z.enum(["unicode_emoji", "realm_emoji", "zulip_extra_emoji"]),
});

type RecentEmoji = z.infer<typeof recent_emoji_schema>;

const session_history = new Map<string, RecentEmoji[]>();

function storage_key(): string {
    return "recent_emojis:" + current_user.user_id;
}

export function get(): RecentEmoji[] {
    if (current_user === undefined) {
        return [];
    }
    const key = storage_key();
    const session_emojis = session_history.get(key);
    if (session_emojis !== undefined) {
        return session_emojis;
    }
    const parsed = z.array(recent_emoji_schema).safeParse(storage.get(key));
    return parsed.success ? parsed.data.slice(0, MAX_RECENT_EMOJIS) : [];
}

export function record({emoji_code, reaction_type}: EmojiRenderingDetails): void {
    if (current_user === undefined) {
        return;
    }
    const key = storage_key();
    const emojis = [
        {emoji_code, reaction_type},
        ...get().filter(
            (entry) => entry.emoji_code !== emoji_code || entry.reaction_type !== reaction_type,
        ),
    ].slice(0, MAX_RECENT_EMOJIS);

    try {
        storage.set(key, emojis);
        session_history.delete(key);
    } catch {
        session_history.set(key, emojis);
    }
}
