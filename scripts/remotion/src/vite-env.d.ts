// Phase 15 / DEMO-03 -- ambient type declaration for `import.meta.glob`.
//
// `import.meta.glob` is a Vite-style bundler helper that resolves a
// glob pattern at bundle time and returns an object whose keys are
// the matched file paths. RESEARCH §Pattern 3 + §Code Example 2 use
// this pattern in src/assetSelector.ts to drive the placeholder->demo
// asset-swap (CONTEXT D-09).
//
// Remotion's bundler is webpack-based (not Vite); on first CI render
// (15-HUMAN-UAT Test 3) the operator may need to migrate the asset
// detection to a webpack-friendly equivalent (e.g.
// `require.context()`) -- this is documented in `15-HUMAN-UAT.md` /
// future plan as a Wave-3 follow-up. For unit-test + tsc --noEmit
// purposes this ambient declaration keeps the surface lint-clean:
// the call type-checks and the bundle-time behaviour resolves
// per whichever bundler Remotion ships at the time.
//
// The declaration is intentionally minimal -- it types the helper
// just enough to keep tsc happy without drawing in the full Vite
// client surface (which would require a `vite` devDependency).
interface ImportMeta {
  glob(
    pattern: string,
    options?: { eager?: boolean }
  ): Record<string, unknown>;
}
