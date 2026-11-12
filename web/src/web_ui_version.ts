import * as channel from "./channel.ts";
import * as feedback_widget from "./feedback_widget.ts";
import {$t} from "./i18n.ts";
import * as reload from "./reload.ts";

export function save(version: "classic" | "modern", on_error: () => void): void {
    void channel.patch({
        url: "/json/settings/web_ui_version",
        data: {version},
        success() {
            reload.initiate({immediate: true});
        },
        error() {
            on_error();
            feedback_widget.show({
                title_text: $t({defaultMessage: "Could not update the web interface"}),
                populate($container) {
                    $container.text($t({defaultMessage: "Please try again."}));
                },
            });
        },
    });
}
