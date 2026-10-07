// Bundles the two pages of the plugin into dist/ next to their HTML.
import { cp, mkdir, rm } from "node:fs/promises";
import { build } from "esbuild";

await rm("dist", { recursive: true, force: true });
await mkdir("dist", { recursive: true });
await build({
  entryPoints: { menu: "src/menu.ts", dialog: "src/dialog.ts" },
  outdir: "dist",
  bundle: true,
  format: "iife",
  target: "es2020",
  minify: true,
  logLevel: "info",
});
await cp("static", "dist", { recursive: true });
