# Local protocol investigation

Static analysis and a live capture of the installed `C:\Program Files (x86)\Net-7\bin\net7proxy.exe`, 2026-09-22. No web sources were used for these findings. No game files were patched. The captured server-to-proxy UDP chat packets decoded directly as plaintext.

## Live capture result

An elevated, 30-second Packet Monitor capture filtered to the game's current TCP and UDP ports produced `captures/20260922_161409.pcapng`. Its 20 packet observations contain five distinct UDP datagrams: two outgoing `0x3005` packets and three incoming `0x2016` packets. Packet Monitor observed each datagram at four stack locations. All three incoming datagrams contain an inner `0x00a5` event; one includes nonempty channel/message text. The decoder parsed all five distinct datagrams without errors or a cipher key.

The observed remote endpoints are UDP `216.219.87.147:3522` and `:3806`. The message-bearing samples came from port 3522. This establishes plaintext on that UDP path only; it does not establish that login, TLS or every game connection is plaintext.

`decode_capture.py` reads the little-endian Ethernet/IPv4 pcapng produced here, checks lengths, and writes decoded records to JSON lines. It groups identical endpoint/payload observations and retains their count; this grouping does not distinguish stack duplicates from identical retransmissions. It skips non-IPv4, fragmented, non-UDP and non-Ethernet packets. It is not a general pcapng or network reassembly library.

```powershell
C:\PYTHON\python.exe protocol\decode_capture.py protocol\captures\20260922_161409.pcapng --output protocol\captures\decoded_udp.jsonl
```

Observed `0x2016` outer layout:

| Offset | Size | Interpretation |
| --- | --- | --- |
| 0 | 2 | Little-endian datagram length |
| 2 | 2 | Opcode `0x2016` |
| 4 | 4 | Constant token in this sample; session role unverified |
| 8 | 4 | Increasing values 47, 48, 49; likely sequence number |
| 12 | variable | Inner frames with little-endian length/opcode headers |

Observed inner `0x00a5` payload:

| Offset/order | Encoding | Interpretation |
| --- | --- | --- |
| 0 | uint32 little-endian | Event value: 1, 2 or 3 observed; meanings unverified |
| 4 | uint32 little-endian | Zero in all three samples |
| 8 onward | Five strings, each uint16 byte length followed by bytes | Name 1, name 2, unknown empty string, channel, message |
| After strings | Six bytes | Zero trailer in all samples; meaning unverified |

String encoding is only verified for ASCII samples. The names match each other in the samples, so sender/display-name distinctions are not established. Private messages have not been sampled; do not assume their fields or event values match channel messages.

`snapshot_proxy.py` independently verified the connection-constructor signature and read the known connection structures with `OpenProcess`/`ReadProcessMemory`. It found two active objects and one inactive object, and validated RC4 permutations. It saves only connection metadata and cipher states to ignored `captures/cipher_states.json`; it does not save a general memory dump or write to the process. The snapshot was taken after the capture and is not synchronized for TCP decryption.

To repeat the bounded capture from the current unelevated shell:

```powershell
Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Verb RunAs -WindowStyle Hidden -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File "C:\MACROS\acmacro\protocol\capture_admin.ps1"'
```

The helper refuses to disturb an existing Packet Monitor session or filter set, records endpoint metadata, captures for 30 seconds, stops, converts to pcapng and removes its own filters. If endpoints change during capture, newly assigned ports may be missed. Capture artifacts and decoded messages remain under ignored `protocol/captures/`.

Binary: x86, image base `0x00400000`, SHA256 `dbcece6433882234e842696713fef5454da91b90d3fa199e327e3301cd7faf87`. Addresses below are virtual addresses for this exact binary. Function names come from diagnostic strings where available; other descriptions come from disassembly.

## Confirmed legacy TCP framing and cipher

| Offset | Size | Meaning after decryption |
| --- | --- | --- |
| 0 | 2 | Little-endian total packet length, including the four-byte header |
| 2 | 2 | Little-endian opcode |
| 4 | length minus 4 | Payload, layout depends on opcode |

- `0x415010`: standard RC4 key scheduling, 256-byte permutation.
- `0x415090`: standard RC4 stream transformation, retaining its two indices and permutation between calls.
- `0x403430`: initializes two independent cipher states from the same eight-byte key. Receive state is at connection offset `+4`, send state at `+0x10a`.
- `0x403d7c` decrypts the four-byte header. `0x403da2` onward extracts payload length and opcode. `0x403e98` decrypts the body using the same receive state. Do not reset RC4 between header/body or between packets.
- `0x4038a0` constructs the same header for outbound packets; `0x40382b` transforms queued outbound bytes using the send state.
- Receive connection types 9 and 10 bypass RC4 here. The queued send path bypasses it for type 9. These internal types have not yet been mapped to network endpoints.

## Key exchange

`DoKeyExchange` at `0x403460` accepts a 64-byte RSA result, or 65 bytes with an initial zero removed, and calls the routine identified as `WestwoodRSA::Decrypt` at `0x4153e0`. Following success, the last eight output bytes are reversed and passed to `0x403430` as the RC4 key. RSA padding and embedded key material have not been independently characterized.

`DoClientKeyExchange` at `0x403690` explicitly initializes the eight-byte key to zero and initializes both RC4 contexts. It receives 74 bytes, constructs a 68-byte response (four-byte network-order length of 64, followed by a 64-byte result from `0x4152c0`), and sends it. This establishes a zero-key path in this build; endpoint assignment and use in the current session still require a trace. Do not assume every connection uses the zero key.

The binary also imports OpenSSL TLS connect/accept/read/write functions. RC4 describes the legacy path above, not necessarily every client/server connection. TLS versions, endpoint roles, and the separate UDP protocol remain unverified.

## Diagnostics

The command parser recognizes `/OPCODES` (sets opcode and general logging flags) and `/DEBUGL` (sets another debug flag plus general logging). `/OPCODES` is not proven to dump every packet. Raw header/body dumps additionally depend on a per-connection flag at `+0xc731`.

Logging helper `0x4079a0` constructs a relative filename using `%s_%04d_%02d_%02d.log` and the UTC date. The filename prefix and actual working directory need runtime verification. No launch options were changed.

## Offline decoder

`decode_stream.py` uses only the Python standard library. Input must be one reassembled, ordered TCP direction starting at the first encrypted packet after key exchange. Exclude retransmissions, TCP/IP headers and handshake bytes. Maintain separate streams for each direction and connection. It does not read pcap files, recover missing bytes, decode TLS/UDP, or discover keys.

```powershell
C:\PYTHON\python.exe protocol\decode_stream.py protocol\captures\client-stream.bin --client-zero-key
C:\PYTHON\python.exe protocol\decode_stream.py protocol\captures\stream.bin --key-file protocol\captures\session-key.bin
C:\PYTHON\python.exe protocol\decode_stream.py protocol\captures\plaintext.bin --plaintext
```

Use `--skip N` only when the input includes a known number of handshake bytes before the RC4 stream begins. It cannot skip arbitrary encrypted bytes without losing cipher alignment. Output is JSON lines with opcode, length, offset, payload hex and an ASCII preview. A plausible frame is not proof that a key or alignment is correct. Output before a truncation error is partial. Payloads may contain private chat or login data; captures and keys belong in the ignored `protocol/captures/` folder.

## Reproducing static inspection

The helper uses isolated `pefile` and `capstone` dependencies. This machine has ARM64 Python, so analysis used a Capstone build with an ARM64 wheel:

```powershell
C:\PYTHON\python.exe -m pip install --only-binary=:all: --target "$env:TEMP\acmacro-protocol-tools" capstone==6.0.0a11 pefile==2024.8.26
C:\PYTHON\python.exe protocol\inspect_proxy.py 'C:\Program Files (x86)\Net-7\bin\net7proxy.exe' --start 0x415010 --length 0x148
C:\PYTHON\python.exe protocol\inspect_proxy.py 'C:\Program Files (x86)\Net-7\bin\net7proxy.exe' --xref 0x415090 --context 12
C:\PYTHON\python.exe -m unittest discover -s tests -p test_protocol_decode.py
```

The inspector performs linear disassembly; embedded jump tables can appear as instructions. Follow control flow and distinguish data from code before assigning meaning.

## Remaining work

The initial shell received Windows error 5 (`Access denied`). After user-authorized UAC elevation, `probe_admin.ps1` verified administrator privileges and successful read access to both the client and proxy. Results and endpoint metadata are saved in ignored `captures/admin_probe.json`. The original shell remains unelevated; subsequent privileged helpers must also be launched with `Start-Process -Verb RunAs`. Live UDP capture and decoding subsequently succeeded as described above.

Next: sample a recognizable private message to verify its event/field layout; capture connection startup if TCP decryption is needed; map endpoints to the exchange routines above. A complete packet dictionary and encrypted TCP decryption have not been established. The macro continues to use its existing chat-log integration.
