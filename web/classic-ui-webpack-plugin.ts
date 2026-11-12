import fs from "node:fs";
import path from "node:path";

import type webpack from "webpack";

const web = import.meta.dirname;
const overrides = path.join(web, "classic");
export const classic_web = path.resolve(web, "../var/classic-ui/web");
const source_directories = ["src", "styles", "templates"];

function link_asset(source: string, destination: string): void {
    const target = path.relative(path.dirname(destination), source);
    if (fs.lstatSync(destination, {throwIfNoEntry: false})?.isSymbolicLink()) {
        if (fs.readlinkSync(destination) === target) {
            return;
        }
        fs.unlinkSync(destination);
    }
    fs.symlinkSync(target, destination);
}

export class ClassicUIWebpackPlugin implements webpack.WebpackPluginInstance {
    apply(compiler: webpack.Compiler): void {
        const files = new Set<string>();
        const directories = new Set<string>();

        function sync_sources(source: string, destination: string, replacement: string): void {
            if (fs.existsSync(source)) {
                directories.add(source);
            }
            if (fs.existsSync(replacement)) {
                directories.add(replacement);
            }
            fs.mkdirSync(destination, {recursive: true});
            const entries = new Set([
                ...(fs.existsSync(source) ? fs.readdirSync(source) : []),
                ...(fs.existsSync(replacement) ? fs.readdirSync(replacement) : []),
            ]);
            for (const name of fs.readdirSync(destination)) {
                if (!entries.has(name)) {
                    fs.rmSync(path.join(destination, name), {recursive: true, force: true});
                }
            }
            for (const name of entries) {
                const original = path.join(source, name);
                const classic = path.join(replacement, name);
                const output = path.join(destination, name);
                const input = fs.existsSync(classic) ? classic : original;
                if (fs.statSync(input).isDirectory()) {
                    sync_sources(original, output, classic);
                } else {
                    files.add(input);
                    const content = fs.readFileSync(input);
                    if (!fs.existsSync(output) || !content.equals(fs.readFileSync(output))) {
                        fs.writeFileSync(output, content);
                    }
                }
            }
        }

        compiler.hooks.beforeCompile.tap("ClassicUIWebpackPlugin", () => {
            files.clear();
            directories.clear();
            for (const name of source_directories) {
                sync_sources(
                    path.join(web, name),
                    path.join(classic_web, name),
                    path.join(overrides, name),
                );
            }
            for (const name of [
                "debug-require.cjs",
                "fonts",
                "generated",
                "icons",
                "images",
                "third",
            ]) {
                link_asset(path.join(web, name), path.join(classic_web, name));
            }
            link_asset(path.resolve(web, "../static"), path.resolve(classic_web, "../static"));
        });
        compiler.hooks.afterCompile.tap("ClassicUIWebpackPlugin", (compilation) => {
            compilation.fileDependencies.addAll(files);
            compilation.contextDependencies.addAll(directories);
        });
    }
}
