# acmacro coding standard

These instructions apply to this repository. Follow the user's current instructions
when they override a convention here. Apply the `acmacro-python` skill when it is
available; this file remains the coding standard even without that skill.

## Design code a maintainer can follow

- Prefer ordinary typed classes with ordinary methods. Keep controller behavior in
  method bodies, not callable-valued dataclass fields or `SimpleNamespace` objects
  that impersonate controllers. Callbacks are appropriate for actual callbacks,
  such as reporting an accepted job, not for constructing an entire object API.
- Use concrete dependencies when there is one implementation. Introduce an
  interface only for genuinely interchangeable implementations. Prefer explicit
  inheritance for repository-owned interfaces so implementations are discoverable.
  Use a `Protocol` when structural compatibility serves a real purpose; do not
  introduce one merely to rename a concrete class's existing methods.
- An adapter should have one obvious dependency and explicit delegating methods.
  Use normal typed attributes for stored objects. Do not create getter properties
  solely to work around a poorly designed interface. Properties should represent
  meaningful computed or controlled access.
- Names must describe current responsibilities. For example, shared position,
  mission, and job capture belongs in `protocol/game_packet_monitor.py` as
  `GamePacketMonitor`, not a gate-distance class. Rename the file, class, imports,
  consumers, tests, and relevant docs together when responsibility changes.
- Keep lifecycle logic in named methods. Avoid large nested functions when an
  operation has its own responsibility, errors, or cleanup path.
- Type new and modified function parameters, returns, callbacks, and state fields.
  Use domain records (`TypedDict` or dataclasses) for structured packet data.
  Give generic wait helpers a typed result. Keep `Any` and unchecked casts at
  unavoidable external-library boundaries, not throughout the application.

## Preserve the macro's operational rules

- UI input, shared-focus operations, and route/player-state mutation belong on the
  main thread. Background packet work must not call UI fallbacks or borrow the
  foreground controller's input methods.
- Pin background work to the client and proxy PID/birth identities. Cancel and
  join workers before their capture/controller is closed, the client is replaced,
  or the ship undocks. Propagate worker failures before departure.
- Coordinate terminal packets with vendor interaction. Do not accept new job
  cargo until the vendor's sell-all operation has finished. Do not let a worker
  change a vendor session while the main thread is using it.
- Gameplay deadlines include lost-focus time. Explicit user pause is excluded.
  Pausing and stopping must also stop new background submissions.
- Count an accepted job only after its matching server acknowledgment. An
  uncertain acceptance stops pickup without retry or UI fallback. Malformed job
  descriptions are skipped and logged; they do not poison the entire picker.
- Packet mode stays packet-only. Keep the original UI picker as an explicitly
  selected alternative. Chat-generation failures produce `STOP`, not retries.
- Honor `mission_count` (1 through 9), destination policy, and user overrides.
  The same-station lock is disabled by default at all stations; `--station-lock`
  enables it. Preserve input-focus coordination independently of that option.
- Log routine start, confirmed acceptances, and stop with counts, target, and
  reason, including failures and cancellation, to the per-character macro log.
  Preserve useful packet diagnostics without exposing credentials.

## Verify the complete change before handing it back

- Read the relevant implementation and its callers before choosing an abstraction.
  Inspect existing changes and preserve work outside the requested scope.
- Review the finished change as a maintainer: locate each implementation from its
  interface, follow the real call path, inspect names and types, and account for
  ownership, concurrency, errors, and cleanup. Resolve related issues in that
  path together instead of waiting for the user to point them out individually.
- Run relevant behavioral tests with `C:\PYTHON\python.exe -m unittest ...`.
  Check concurrency, cancellation, reply correlation, and configuration boundaries
  when those behaviors change. Do not add tests that only assert source wording.
- Type-check changed typed modules when tooling is available. Mypy needs
  `--explicit-package-bases` for this repository's namespace packages. A scoped
  check may use `--follow-imports=skip --ignore-missing-imports`; report its scope
  accurately rather than claiming the entire repository was type-checked.
- Tests must not drive live clients. Live play, restarts, or shutdowns must be
  within the user's authorized task. Distinguish unit-tested behavior from live
  verification, and do not claim an inferred crash cause as established fact.
- Report what changed, checks that passed, and material limitations. Do not claim
  a local edit is committed or loaded into an already-running macro.
