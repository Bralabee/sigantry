/**
 * Phase 15 / DEMO-03 / CONTEXT D-09 -- placeholder -> demo asset swap.
 *
 * `pickAsset(name)` returns a Remotion `staticFile()` URL pointing at
 * `assets/demo/<name>` if a matching file exists at bundle time, and
 * otherwise falls back to `assets/placeholder/<name>`.
 *
 * RESEARCH §Pattern 3 + §Code Example 2 specified `import.meta.glob`
 * (Vite-style bundler API). Remotion's bundler is webpack-based,
 * which doesn't implement `import.meta.glob` at runtime -- so this
 * implementation uses webpack's native `require.context()` instead.
 * Same bundle-time semantics, same fallback chain, same UX: drop
 * PNGs into `assets/demo/`, re-render, get a tenant-real mp4.
 *
 * The `import.meta.glob` reference is preserved in this comment for
 * traceability against RESEARCH §Pattern 3; the runtime call below
 * uses `require.context` because that's what webpack ships.
 *
 * Wave 0 default (only `.gitkeep` under assets/demo/) -- every
 * pickAsset() call falls back to the placeholder path. Operator
 * captures real screenshots per 15-HUMAN-UAT.md Test 3 and drops
 * them in; the next `npm run render` picks them up automatically.
 */

import { staticFile } from "remotion";

// Webpack `require.context` -- enumerates files matching the regex
// at bundle time. Returns a function with `.keys()` listing all
// matched paths. Equivalent to Vite's `import.meta.glob('/assets/
// demo/*.{png,jpg,webp}', { eager: true })` from RESEARCH §Pattern 3.
//
// The path is relative to assetSelector.ts; ../../assets/demo
// resolves to scripts/remotion/assets/demo/. The third arg `false`
// means non-recursive; the regex matches png/jpg/webp.
declare const require: {
  context(
    directory: string,
    useSubdirectories: boolean,
    regExp: RegExp
  ): { keys(): string[] };
};

let demoAssetKeys: Set<string> = new Set();
try {
  const ctx = require.context("../assets/demo", false, /\.(png|jpe?g|webp)$/);
  // ctx.keys() returns paths like "./scene1-workitem.png"; strip the
  // leading "./" so we can match against bare filenames in pickAsset().
  demoAssetKeys = new Set(ctx.keys().map((k) => k.replace(/^\.\//, "")));
} catch {
  // Test env or non-webpack bundler -- fall through to placeholder
  // for every asset (operator can still ship real screenshots once
  // the runtime is in place).
  demoAssetKeys = new Set();
}

// staticFile() resolves paths relative to remotion.config.ts's
// publicDir setting. We point publicDir at "assets/" (one level up
// from this file's bundle root), so the staticFile arg is the
// `<demo|placeholder>/<name>` suffix below.
export function pickAsset(name: string): string {
  if (demoAssetKeys.has(name)) {
    return staticFile(`demo/${name}`);
  }
  return staticFile(`placeholder/${name}`);
}
