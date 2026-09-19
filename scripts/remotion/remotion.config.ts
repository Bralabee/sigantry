/**
 * Remotion build config -- Sigantry walkthrough (Phase 15 / DEMO-03).
 *
 * Cloned shape from /remotion-tutorial/remotion.config.ts (which uses
 * the same `Config.set*` API). The demo subproject does NOT enable
 * Tailwind -- it ships plain CSS via src/index.css to keep the
 * dependency surface minimal.
 *
 * Codec + JPEG quality defaults are set here so `npm run render`
 * (without explicit flags) produces the ~20MB mp4 target for an
 * ~80-second walkthrough (RESEARCH §Pitfall 4 -- mp4 size budget).
 * The CI workflow + scripts/build-walkthrough.sh ALSO pass these
 * flags explicitly on the command line for belt-and-braces.
 *
 * Note: when using the Node.JS APIs, this config file does NOT apply.
 * Pass options directly to the APIs in that case.
 *
 * All configuration options: https://remotion.dev/docs/config
 */

import { Config } from "@remotion/cli/config";

Config.setVideoImageFormat("jpeg");
Config.setOverwriteOutput(true);
Config.setCodec("h264");
Config.setJpegQuality(80);

// Phase 15 / DEMO-03 -- staticFile() resolves files relative to a
// project-root `public/` directory by default. Plan 15-00 stamped
// the placeholder PNGs at scripts/remotion/assets/placeholder/,
// not under public/, so we point Remotion's public dir at `assets/`
// to preserve the Wave 0 layout (which the docs + asset-swap
// instructions reference extensively). This is a one-line override
// rather than a directory rename + path rewrite across the repo.
Config.setPublicDir("assets");
