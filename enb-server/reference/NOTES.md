# C++ compatibility notes

Reference: `therealKyp/Earth-and-Beyond-server`, revision
`3b46ad7c8baecca61adf8a0bedd2b23cb1936b9d`. This archived source predates the
installed Net7Proxy 2.46. Confirm uncertain layouts against captured traffic;
do not assume its code exactly matches today's live service.

For example, Net7Proxy 2.46's account connection opens TCP **3807** (the
installed binary passes `0x0edf` to its connection helper). The archived
`UDPProxyToGlobal.cpp` uses 3809 instead. Both endpoints are supported here;
the installed proxy's local HTTPS `AuthLogin` was verified against TCP 3807.

The proxy also rebuilds `GlobalTicket::join_data` before sending it to the
graphical client (`Connection::SendGlobalTicket`), replacing the ticket text
with the fixed zero-padded `MY_Avatar_Ticket` value. Proxy-selected characters
therefore expect that value on sector entry. Direct clients retain the random
ticket value returned by the Rust global server. Both paths check character
and sector identity and pin UDP traffic to the selected peer/handoff endpoint.

| Reference path under trunk | What was carried into this prototype |
|---|---|
| `Net7/ServerManager.cpp` | Split global, proxy bridge, registration, master and sector endpoints. |
| `Net7/Connection.cpp`, `WestwoodRSA.*`, `WestwoodRC4.*` | Public legacy key material, 64-byte RSA key block, reversed eight-byte RC4 key and independent cipher directions. |
| `Net7/PacketStructures.h`, `PacketMethods.h` | Packed Win32 field widths and explicit byte order. |
| `Net7/ClientToGlobalServer.cpp`, `AccountManager.cpp` | Version 42, account ticket, five-slot character list and character join ticket. |
| `Net7/UDP_MVAS.cpp`, `UDP_Global.cpp`, `UDP_Client.cpp` | Registration before authentication, fixed communication port, handoff and selected-sector endpoint. |
| `Net7/SectorManager.cpp`, `PlayerClass.cpp` | Galaxy/sector description, player AUX, ship creation, ship AUX, parameters, navpoints, start acknowledgment and gate sequence. |
| `Net7/AuxClasses/AuxBase.cpp`, `AuxPlayerIndex.*`, `AuxShipIndex.*`, `AuxItem.*` | Two-bit AUX flags, default nested records, empty inventory item IDs and packet lengths. |
| `Net7/PlayerConnection.cpp` | Target selection, warp waypoint list, actions 18/19, position updates and logout. |
| `Net7/PlayerClass.cpp`, `ObjectClassMovement.cpp`, `UDP_MVAS.cpp` | Impulse command meanings, acceleration/braking, quaternion steering, forward +X, packet speeds in meters/ms, and proxy position/heading feed. |
| `Net7/MOBClass.cpp`, `MOBSpawnClass.cpp`, `MOBDatabaseSQL.cpp` | MOB lifecycle, scan-range formula, chase/attack separation, leash and respawn responsibilities. |
| `Net7Proxy/Net7.cpp`, `ServerManager.cpp`, `UDPProxyToGlobal.cpp`, `ClientToMasterServer.cpp` | `/LC` mode: the proxy owns the original client's local TLS/master/sector interfaces and forwards to the backend. |

The database dump examined here contains 2,042 `mob_base` definitions. The content
importer reads only that public content table and exports a level-one Achilles
Drone. The dump itself is not needed to run the server and is not committed.

Important mixed layouts confirmed against the relogin capture:

- TCP frames use a four-byte little-endian size/opcode header.
- UDP adds little-endian player ID and sequence to make a 12-byte header.
- A large AUX frame continues across `0x2016` and `0x201a` datagrams.
- Character-list `AvatarInfo` numbers use big-endian, but `AvatarData` race and
  profession use little-endian.
- Galaxy count and port use big-endian. Galaxy ID/count fields use little-endian;
  the IPv4 integer appears reversed on the wire (`01 00 00 7f` for localhost).
- Account ticket length is little-endian and includes its terminating NUL.
- The original client assumes fixed legacy key material even though a public
  key is advertised. This is compatibility cryptography, not modern TLS.

Intentional differences: account data is local JSON with a salted password hash;
only position is persisted; there is one sector/character; the MOB damage model
is simplified. The C++ spawn/faction/equipment/loot database and hundreds of
ability/mission handlers are not reproduced. There is no copied player database,
live-service account, global packet replay, or forwarding to production.

`data/asteroid_belt_alpha.json` exports the archived sector 1076 bounds, skybox,
static models, transforms, names, nav flags and destinations. Zero scale becomes
one as in `Object::SetScale`; zero orientation becomes identity. Type-zero MOB
spawn rules and type-38 resource fields are omitted rather than rendered as
static objects. The exporter preserves source IDs independently of runtime IDs.
