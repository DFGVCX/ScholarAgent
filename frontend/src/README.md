# Frontend module boundary

`src/app` owns composition and routing. `src/features/<domain>` owns domain API adapters,
view models and components. `src/api` is the only HTTP boundary; `src/types` contains
transport contracts; `src/styles` contains shared tokens and shell styles.

The current `LegacyConsoleBridge` is an explicit strangler boundary. Existing users keep
the stable `app.html` console while pages are migrated one domain at a time. New business
logic must not be added to `dist/app.html`; it belongs in a feature package first.
