# Building while the server remains playable

`tools/restart_server.py` builds into the persistent `target/restart-build`
Cargo cache while the owned server continues running. It never executes that
cached binary directly, so Windows can link later staged builds without a
running-image lock. The first cache population takes longer; later builds are
incremental. A compile failure leaves the current server untouched.

After compilation, the tool rechecks the process's executable and birth time,
backs up the current binary and requests its normal graceful checkpoint/stop.
Only after the pinned process exits does it atomically install the staged image
at `target/debug/enb-server` (or `.exe`). The replacement reuses the owned command
arguments. Neither the graphical game nor proxy is stopped; eligible native
UDP sessions resume from their checkpoints.

A terminal new-process failure attempts the saved old executable. A readiness
timeout while the replacement remains alive leaves it running and reports the
PID; it does not terminate it or launch a competing fallback. A changed process
identity or an occupied server address also prevents a duplicate launch. Startup
still needs a valid registration response, with a30-second observation window;
printing a ready log line alone is insufficient. Configuration/state errors can
also prevent the fallback starting and require inspection of `logs/server.log`.

`--no-build` keeps the existing executable for a plain restart. The staged-build
path works with the existing portable Cargo/linker setup on Windows, Linux and
macOS; this VM is the live verification environment. Mocked lifecycle tests use
temporary files and processes supplied by test doubles, without driving clients.
They cover compile-before-stop, compiler failure, changed ownership, atomic
install, terminal startup fallback and preserving live processes on observation
timeout. The complete Python tool suite passes55 tests and scoped type checks
cover the modified build/restart modules and tests.
