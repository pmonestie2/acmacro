# ENB internal web client

A separate application inside the acmacro repository. It connects directly using
the shared `enb-protocol` library; it does not drive the game UI or inject packets
through Net7Proxy. All application processes were stopped at the user's request;
checking out this code does not start them.

Install and start from the repository root:

```powershell
C:\PYTHON\python.exe -m pip install -e ./enb-protocol -e ./enb-web
.\web.cmd
```

Open **http://192.168.47.128:8765**. `--host` and `--port` change the local
listening interface and port; `--server` changes the game server. The last manual
credential version was served on port 8766, selected with `--port 8766`.

The browser asks for an **account name and password**, then presents the
characters returned by the server. Choose a character and select **Enter game**.
No macro config supplies login credentials, and the application does not save
passwords. The password field clears on submission. Pending authenticated
character selection expires after two minutes; it requires a fresh sign-in.

On the live map, select an object in the list or map, then use **Warp to selected**
or **Use selected gate**. Gate activation waits for matching server target
confirmation. Gate completion waits for its camera cue and sequence delay before
following the server's handoff. The map, ship position, connection state, and
navigation status refresh automatically. Log out before using the same character
in another game client.

The initial direct character login and live map were verified. The navigation
commands are unit-tested but have not been verified end to end in live headless
play. NPC and inventory interfaces remain unimplemented. Account rejections show
the server's error message and code; the application does not retry them.

## Legacy authentication certificate

Normal HTTPS certificate validation is the default. The observed game-service
certificate was expired. An explicit pin allows that exact certificate, checked
before credentials are sent. The public SHA256 observed on 2026-10-23 was:

```text
1187a7c1e33fe009d2c9c082f30acc9f03c28cbe2869b0fc5f617aea1206b5f6
```

For that certificate:

```powershell
.\web.cmd --certificate-sha256 1187a7c1e33fe009d2c9c082f30acc9f03c28cbe2869b0fc5f617aea1206b5f6
```

A changed certificate must be checked before updating the pin. Do not replace
this check with unconditional TLS verification bypass.

## Local operation and checks

`--capture PATH --capture-server IPV4` optionally loads an explicitly labeled
offline map snapshot. Captures stay under ignored `diagnostics/` and are never
served over HTTP. Only application assets and explicit API routes are exposed;
the server does not serve the repository or configuration files. There are no
external scripts, fonts, or hosted services.

When started through `web.cmd`, Ctrl+C stops the server and closes its owned
session. Local background runs used `diagnostics/enb_web*.pid` and log files.
The VM firewall permits Python's background process on ports 8765 and 8766 only
from `192.168.47.0/24` to `192.168.47.128`. Configure the corresponding rule if
you change the executable, interface, or port.

Tests use synthetic packets, mock sockets, and loopback HTTP; they do not drive
the game. Run the protocol, web, and navigation checks from the repository root:

```powershell
C:\PYTHON\python.exe -m unittest tests.test_headless_login tests.test_headless_protocol tests.test_protocol_capture tests.test_protocol_decode tests.test_protocol_tracker tests.test_explore_gates tests.test_target_selection
```

Browser screenshot verification was blocked by automatic approval review. HTTP,
API, protocol, and scoped type checks were performed; visual rendering was not
verified by an automated browser.
