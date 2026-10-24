using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Threading;

namespace Enb.Protocol
{
    /// <summary>Single-worker local-server session; never calls an engine API.</summary>
    public sealed class LocalSession : IDisposable
    {
        private readonly CancellationToken cancel;
        private readonly Stopwatch clock = Stopwatch.StartNew();
        private UdpClient comms, sector;
        private IPAddress address;
        private SectorStream stream;
        private uint sequence = 1;
        private double receivedAt, keepaliveAt, resendAt, gateAt, dockAt, enteredAt;
        private uint gate, dock, combatTarget, followTarget, pendingLoot, pendingResource;
        private double combatTargetAt, followTargetAt, pendingLootAt, pendingResourceAt;
        private bool gateActivated, gateCamera, gateFinished, dockRequested, lootRequested, disposed, failed;
        private byte[] handoff;
        public uint Player { get; private set; }
        public World World { get; private set; } = new World();
        public bool Ready { get; private set; }
        public string Status { get; private set; } = "Connecting";

        /// <summary>Bind session lifetime to a worker cancellation token.</summary>
        public LocalSession(CancellationToken token) { cancel = token; }

        /// <returns>A validated explicit loopback or private IPv4 address.</returns>
        public static IPAddress LocalAddress(string host)
        {
            if (!IPAddress.TryParse(host, out IPAddress ip) || ip.AddressFamily != AddressFamily.InterNetwork)
                throw new ArgumentException("Enter a loopback or private LAN IPv4 address.");
            byte[] b = ip.GetAddressBytes();
            if (!(b[0] == 127 || b[0] == 10 || b[0] == 192 && b[1] == 168 || b[0] == 172 && b[1] >= 16 && b[1] <= 31))
                throw new ArgumentException("This client connects only to your local development server.");
            return ip;
        }

        /// <summary>Authenticate through the existing plaintext local bridge and enter space.</summary>
        public void Connect(string host, string username, string password, int characterSlot = 0)
        {
            cancel.ThrowIfCancellationRequested();
            address = LocalAddress(host);
            comms = NewUdp(3806);
            SendUdp(comms, 0x1000, Wire.Body(w => w.Write(246u)), 0, 0);
            byte[] registration = ReceiveUdp(comms, 10);
            if (Wire.Datagram(registration, 0) != 0x1001) throw new InvalidDataException("UDP registration rejected.");
            using (var tcp = new TcpClient(AddressFamily.InterNetwork))
            {
                IAsyncResult connecting = tcp.BeginConnect(address, 3807, null, null);
                using (WaitHandle handle = connecting.AsyncWaitHandle)
                {
                    double deadline = clock.Elapsed.TotalSeconds + 10;
                    while (!handle.WaitOne(50)) { cancel.ThrowIfCancellationRequested(); if (clock.Elapsed.TotalSeconds > deadline) throw new TimeoutException("Local TCP bridge timed out."); }
                }
                tcp.EndConnect(connecting);
                tcp.SendTimeout = 1000;
                NetworkStream network = tcp.GetStream();
                byte[] user = Encoding.UTF8.GetBytes(username), secret = Encoding.UTF8.GetBytes(password);
                if (user.Length == 0 || user.Length > 256 || secret.Length > 1024) throw new ArgumentException("Invalid credential length.");
                byte[] credentials = Wire.Body(w => { w.Write((ushort)user.Length); w.Write(user); w.Write((ushort)secret.Length); w.Write(secret); });
                try { Write(network, 0x2000, credentials); }
                finally { Array.Clear(secret, 0, secret.Length); Array.Clear(credentials, 0, credentials.Length); }
                Frame auth = Read(tcp);
                if (auth.Opcode != 0x2001) throw new InvalidDataException("Unexpected authentication reply.");
                string ticket = null;
                bool valid = false;
                foreach (string line in Encoding.ASCII.GetString(auth.Body).Split('\n'))
                {
                    string field = line.Trim();
                    if (field == "Valid=TRUE") valid = true;
                    if (field.StartsWith("Ticket=", StringComparison.Ordinal)) ticket = field.Substring(7);
                }
                if (!valid || string.IsNullOrEmpty(ticket) || ticket.Length > 512) throw new InvalidDataException("Local account authentication rejected.");
                Write(network, 0x2002, Encoding.ASCII.GetBytes(ticket));
                Frame characters = Read(tcp);
                if (characters.Opcode != 0x70 || characters.Body.Length < 1874) throw new InvalidDataException("Character list rejected.");
                uint slotCount = Wire.U32(characters.Body, 8, true);
                if (characterSlot < 0 || characterSlot >= slotCount || characterSlot >= 5)
                    throw new InvalidDataException("Choose an available character.");
                int characterOffset = 133 + characterSlot * 374;
                if (Wire.Text(characters.Body, characterOffset, 20).Length == 0)
                    throw new InvalidDataException("The selected character slot is empty.");
                Write(network, 0x2004, Wire.Body(w => Wire.Big(w, (uint)characterSlot)));
                Frame selected = Read(tcp);
                if (selected.Opcode != 0x6f || selected.Body.Length != 68 || Wire.U32(selected.Body, 0, true) != 0)
                    throw new InvalidDataException("Character selection rejected; it may already be logged in.");
                byte[] join = Wire.Slice(selected.Body, 4, 64);
                Player = Wire.U32(join, 16, true);
                if (Player == 0) throw new InvalidDataException("Missing player identity.");
                SendUdp(comms, 0x200f, Array.Empty<byte>(), Player, 0);
                sector = NewUdp(3808);
                Enter(join);
            }
        }

        /// <returns>A connected UDP socket with short cancellation-aware polling.</returns>
        private UdpClient NewUdp(int port)
        {
            var socket = new UdpClient(AddressFamily.InterNetwork);
            try { socket.Client.ReceiveTimeout = 100; socket.Client.SendTimeout = 1000; socket.Connect(address, port); return socket; }
            catch { socket.Dispose(); throw; }
        }

        /// <summary>Send an encoded datagram through its owning socket.</summary>
        private static void SendUdp(UdpClient socket, ushort opcode, byte[] body, uint player, uint sequence)
        { byte[] packet = Wire.Encode(opcode, body, player, sequence); socket.Send(packet, packet.Length); }

        /// <returns>A datagram received before the deadline or cancellation.</returns>
        private byte[] ReceiveUdp(UdpClient socket, double timeout)
        {
            double deadline = clock.Elapsed.TotalSeconds + timeout;
            while (true)
            {
                cancel.ThrowIfCancellationRequested();
                try { IPEndPoint endpoint = null; return socket.Receive(ref endpoint); }
                catch (SocketException error) when (error.SocketErrorCode == SocketError.TimedOut || error.SocketErrorCode == SocketError.WouldBlock)
                { if (clock.Elapsed.TotalSeconds >= deadline) throw new TimeoutException("Local server response timed out."); }
            }
        }

        /// <summary>Write a framed request to the local TCP bridge.</summary>
        private static void Write(NetworkStream network, ushort opcode, byte[] body)
        { byte[] packet = Wire.Encode(opcode, body); network.Write(packet, 0, packet.Length); }

        /// <returns>Exactly the requested bytes, honoring TCP fragmentation and cancellation.</returns>
        private byte[] ReadExact(TcpClient tcp, int size)
        {
            byte[] result = new byte[size]; int offset = 0;
            double deadline = clock.Elapsed.TotalSeconds + 10;
            while (offset < size)
            {
                cancel.ThrowIfCancellationRequested();
                if (clock.Elapsed.TotalSeconds > deadline) throw new TimeoutException("Local bridge response timed out.");
                if (!tcp.Client.Poll(100000, SelectMode.SelectRead)) continue;
                int count = tcp.Client.Receive(result, offset, size - offset, SocketFlags.None);
                if (count == 0) throw new EndOfStreamException("Local bridge closed the connection.");
                offset += count;
            }
            return result;
        }

        /// <returns>A bounded complete TCP frame.</returns>
        private Frame Read(TcpClient tcp)
        {
            byte[] header = ReadExact(tcp, 4); int size = Wire.U16(header, 0);
            if (size < 4 || size > 32767) throw new InvalidDataException("Invalid TCP frame size.");
            return new Frame(Wire.U16(header, 2), ReadExact(tcp, size - 4));
        }

        /// <summary>Perform a fresh sector handoff using the existing UDP socket.</summary>
        private void Enter(byte[] join)
        {
            uint owner = Wire.U32(join, 16, true), destination = Wire.U32(join, 20, true);
            if (owner != Player || destination == 0) throw new InvalidDataException("Invalid sector handoff identity.");
            sector.Connect(address, 3808);
            SendUdp(sector, 0x2008, Wire.Body(w => { w.Write(destination); w.Write((byte)1); }), Player, 0);
            byte[] reply = ReceiveUdp(sector, 15);
            if (Wire.Datagram(reply, Player) != 0x2009 || reply.Length < 22 || Wire.U32(reply, 18) != Player)
                throw new InvalidDataException("Sector handoff identity mismatch.");
            int port = Wire.U16(reply, 16);
            if (port == 0) throw new InvalidDataException("Missing sector port.");
            sector.Connect(address, port);
            stream = new SectorStream(Player); World = new World(); sequence = 1; Ready = false;
            gate = dock = followTarget = pendingResource = 0; handoff = null;
            byte[] body = Wire.Body(w =>
            {
                w.Write(join); w.Write(unchecked((uint)Environment.TickCount)); w.Write(new byte[40]);
                w.Write(Encoding.ASCII.GetBytes(DateTime.Now.ToString("MM/dd/yy HH:mm:ss", CultureInfo.InvariantCulture) + "\0"));
                w.Write(new byte[11]);
            });
            Send(2, body);
            receivedAt = enteredAt = keepaliveAt = clock.Elapsed.TotalSeconds;
            Status = "Receiving sector";
        }

        /// <summary>Send one input with this session's monotonically increasing sequence.</summary>
        private void Send(ushort opcode, byte[] body)
        { cancel.ThrowIfCancellationRequested(); SendUdp(sector, opcode, body, Player, sequence++); }

        /// <summary>Submit forward, reverse, or brake input.</summary>
        public void Thrust(byte command)
        {
            if (!Ready || gate != 0) return;
            if (command != 2 && command != 3 && command != 4) throw new ArgumentOutOfRangeException(nameof(command));
            followTarget = 0;
            Send(0x14, Wire.Body(w => { w.Write(Player); w.Write(command); }));
        }

        /// <summary>Send a bounded local chat or developer command.</summary>
        public void Chat(string text, byte channel = 4)
        {
            if (!Ready || gate != 0) return;
            if (string.IsNullOrWhiteSpace(text) || text.Length > 180 || text.IndexOf('\0') >= 0)
                throw new ArgumentException("Enter a chat line up to 180 characters.", nameof(text));
            if (channel != 1 && channel != 4) throw new ArgumentOutOfRangeException(nameof(channel));
            if (text.Trim().Equals("/accept", StringComparison.OrdinalIgnoreCase) || text.Trim().Equals("/decline", StringComparison.OrdinalIgnoreCase)) World.ClearInvitation();
            byte[] encoded = Encoding.UTF8.GetBytes(text);
            Send(0x33, Wire.Body(w => { w.Write(Player); w.Write(channel); w.Write((ushort)encoded.Length); w.Write(encoded); }));
        }

        /// <summary>Submit yaw and pitch intensities in the range minus one to one.</summary>
        public void Steer(float yaw, float pitch)
        {
            if (!Ready || gate != 0) return;
            if (float.IsNaN(yaw) || float.IsNaN(pitch) || Math.Abs(yaw) > 1 || Math.Abs(pitch) > 1) throw new ArgumentOutOfRangeException(nameof(yaw));
            followTarget = 0;
            Send(0x12, Wire.Body(w => { w.Write(Player); w.Write(yaw); }));
            Send(0x13, Wire.Body(w => { w.Write(Player); w.Write(pitch); }));
        }

        /// <summary>Request server-authoritative warp to a current navigation object.</summary>
        public void Warp(uint target)
        {
            if (!Ready || gate != 0 || !World.Objects.TryGetValue(target, out MapObject item) || !item.Navigable) return;
            followTarget = 0;
            Send(0x9b, Wire.Body(w => { w.Write(Player & 0xffffff); w.Write((ushort)1); w.Write(target); }));
            Status = "Warp requested: " + item.Name;
        }

        /// <summary>Select a gate once, waiting for its matching acknowledgment before activation.</summary>
        public void Gate(uint target)
        {
            if (!Ready || gate != 0 || !World.Objects.TryGetValue(target, out MapObject item) || item.Kind != 11) return;
            followTarget = 0;
            gate = target; gateActivated = gateCamera = gateFinished = false; gateAt = clock.Elapsed.TotalSeconds;
            Send(0x17, Wire.Body(w => { w.Write(Player); w.Write(target); }));
            Send(0x5a, Wire.Body(w => { Wire.Big(w, Player); Wire.Big(w, target); w.Write(1u); }));
            Status = "Selecting gate: " + item.Name;
        }

        /// <summary>Select a nearby station and wait for clearance before docking.</summary>
        public void Dock(uint target)
        {
            if (!Ready || World.Station != null || gate != 0 || dock != 0 ||
                !World.Objects.TryGetValue(target, out MapObject item) || item.Kind != 12 ||
                !World.HasPosition || World.Position.Distance(item.Position) > 5000)
                return;
            followTarget = 0;
            dock = target; dockAt = clock.Elapsed.TotalSeconds; dockRequested = false;
            Send(0x17, Wire.Body(w => { w.Write(Player); w.Write(target); }));
            Status = "Requesting docking clearance: " + item.Name;
        }

        /// <summary>Leave the current station through its native launch request.</summary>
        public void Launch()
        {
            if (!Ready || World.Station == null || gate != 0 || dock != 0) return;
            Send(0x4e, Wire.Body(w => { w.Write(Player); w.Write(World.Station.Id); w.Write((byte)1); }));
            Status = "Launching from " + World.Sector;
        }

        /// <summary>Open one NPC present in the authoritative station layout.</summary>
        public void OpenNpc(uint npcId)
        {
            if (!Ready || World.Station == null || World.Dialogue != null ||
                !World.Station.NpcIds.Contains(npcId)) return;
            World.StartDialogue(npcId);
            Send(0x4e, Wire.Body(w => { w.Write(Player); w.Write(npcId); w.Write((byte)4); }));
            Status = "Talking to station contact";
        }

        /// <summary>Choose only a branch offered by the current NPC talk tree.</summary>
        public void ChooseNpc(byte node)
        {
            if (!Ready || World.Station == null || World.Dialogue == null ||
                node != 230 && !World.Dialogue.Choices.Any(choice => choice.Node == node)) return;
            Send(0x55, Wire.Body(w => { w.Write(Player); w.Write(node); }));
            Status = node == 0 || node == 230 ? "Conversation closed" : "Waiting for station contact";
        }

        /// <summary>Buy a bounded quantity from a server-listed vendor slot.</summary>
        public void BuyVendor(int slot, uint quantity = 1)
        {
            if (!Ready || World.Station == null || World.Dialogue?.VendorOpen != true ||
                slot < 0 || slot >= World.Vendor.Length || World.Vendor[slot]?.Present != true ||
                quantity == 0 || quantity > 99) return;
            Send(0x27, Wire.Body(w =>
            {
                Wire.Big(w, Player); Wire.Big(w, 4u); Wire.Big(w, (uint)slot);
                Wire.Big(w, 1u); Wire.Big(w, uint.MaxValue); Wire.Big(w, quantity);
            }));
            Status = "Buying from station vendor";
        }

        /// <summary>Sell an owned cargo stack through the active vendor.</summary>
        public void SellVendor(int slot, uint quantity = 1)
        {
            if (!Ready || World.Station == null || World.Dialogue?.VendorOpen != true ||
                slot < 0 || slot >= World.Equipment.Cargo.Length ||
                World.Equipment.Cargo[slot]?.Present != true || quantity == 0 ||
                quantity > World.Equipment.Cargo[slot].Quantity) return;
            Send(0x27, Wire.Body(w =>
            {
                Wire.Big(w, Player); Wire.Big(w, 1u); Wire.Big(w, (uint)slot);
                Wire.Big(w, 4u); Wire.Big(w, 0u); Wire.Big(w, quantity);
            }));
            Status = "Selling cargo to station vendor";
        }

        /// <summary>Install owned cargo with the native inventory transfer packet.</summary>
        public void EquipCargo(int cargoSlot, int mountSlot)
        {
            if (!Ready || gate != 0 || cargoSlot < 0 || cargoSlot >= World.Equipment.Cargo.Length ||
                mountSlot < 0 || mountSlot >= World.Equipment.Mounted.Length ||
                World.Equipment.Cargo[cargoSlot]?.Present != true) return;
            SendInventoryTransfer(1, (uint)cargoSlot, 2, (uint)mountSlot, 1);
            Status = "Installing ship equipment";
        }

        /// <summary>Move an installed item into the first empty cargo slot.</summary>
        public void UnequipMount(int mountSlot)
        {
            if (!Ready || gate != 0 || mountSlot < 0 || mountSlot >= World.Equipment.Mounted.Length ||
                World.Equipment.Mounted[mountSlot]?.Present != true) return;
            int cargoSlot = Array.FindIndex(World.Equipment.Cargo, item => item?.Present != true);
            if (cargoSlot < 0) { Status = "Cargo hold is full"; return; }
            SendInventoryTransfer(2, (uint)mountSlot, 1, (uint)cargoSlot, 1);
            Status = "Moving equipment to cargo";
        }

        /// <summary>Submit a bounded owned-inventory move through the server's durable transfer path.</summary>
        private void SendInventoryTransfer(uint source, uint sourceSlot, uint destination, uint destinationSlot, uint quantity)
        {
            Send(0x27, Wire.Body(w =>
            {
                Wire.Big(w, Player); Wire.Big(w, source); Wire.Big(w, sourceSlot);
                Wire.Big(w, destination); Wire.Big(w, destinationSlot); Wire.Big(w, quantity);
            }));
        }

        /// <summary>Select a visible contact for group targeting and combat.</summary>
        public void SelectTarget(uint target)
        {
            if (!Ready || gate != 0 || (!World.Mobs.ContainsKey(target) && !World.Players.ContainsKey(target))) return;
            if (followTarget != target) followTarget = 0;
            World.SetOpenLoot(0);
            Send(0x17, Wire.Body(w => { w.Write(Player); w.Write(target); }));
        }

        /// <summary>Follow a visible ship after its matching target acknowledgment.</summary>
        public void FollowTarget(uint target)
        {
            if (!Ready || gate != 0 || (!World.Mobs.ContainsKey(target) && !World.Players.ContainsKey(target))) return;
            followTarget = target; followTargetAt = clock.Elapsed.TotalSeconds;
            Send(0x17, Wire.Body(w => { w.Write(Player); w.Write(target); }));
            Status = "Selecting ship to follow";
        }

        /// <summary>Invite one visible online player through the native group action.</summary>
        public void InvitePlayer(uint target)
        {
            if (!Ready || gate != 0 || !World.Players.ContainsKey(target)) return;
            Send(0x2c, Wire.Body(w =>
            { w.Write(Player); w.Write(10u); w.Write(target); w.Write(0u); }));
            Status = "Requesting group invitation";
        }

        /// <summary>Stop active weapons even after the target leaves visibility.</summary>
        public void StopWeapons()
        {
            if (!Ready || gate != 0 || !World.Equipment.Mounted.Any(item => item != null && (item.WeaponState & 1) != 0)) return;
            Send(0x2c, Wire.Body(w => { w.Write(Player); w.Write(9u); w.Write(0u); w.Write(0u); }));
        }

        /// <summary>Select a visible wreck before requesting its server loot window.</summary>
        public void OpenLoot(uint target)
        {
            if (!Ready || gate != 0 || pendingLoot != 0 || !World.Objects.TryGetValue(target, out MapObject corpse) || corpse.Kind != 25)
                return;
            followTarget = 0;
            pendingLoot = target; pendingLootAt = clock.Elapsed.TotalSeconds; lootRequested = false;
            World.SetOpenLoot(0);
            Send(0x17, Wire.Body(w => { w.Write(Player); w.Write(target); }));
            Status = "Selecting wreck: " + corpse.Name;
        }

        /// <summary>Begin the server's timed tractor collection for one visible wreck slot.</summary>
        public void TakeLoot(uint corpse, int slot)
        {
            if (!Ready || gate != 0 || World.OpenLootId != corpse || slot < 0 || slot >= 40 ||
                !World.Loot.TryGetValue(corpse, out ItemState[] contents) || contents[slot]?.Present != true)
                return;
            Send(0x27, Wire.Body(w =>
            {
                Wire.Big(w, Player); Wire.Big(w, 6u); Wire.Big(w, (uint)slot);
                Wire.Big(w, 1u); Wire.Big(w, 0u); Wire.Big(w, 1u);
            }));
            Status = "Tractor beam: slot " + slot;
        }

        /// <summary>Select a visible asteroid before opening its native prospecting window.</summary>
        public void OpenResource(uint target)
        {
            if (!Ready || gate != 0 || pendingResource != 0 ||
                !World.Objects.TryGetValue(target, out MapObject resource) || resource.Kind != 38) return;
            followTarget = 0; pendingResource = target; pendingResourceAt = clock.Elapsed.TotalSeconds;
            World.SetResourceTarget(target);
            Send(0x17, Wire.Body(w => { w.Write(Player); w.Write(target); }));
            Status = "Selecting asteroid: " + resource.Name;
        }

        /// <summary>Close the currently open prospecting window.</summary>
        public void CloseResource()
        {
            if (!Ready || gate != 0 || !World.ResourceWindowOpen || World.ResourceTargetId == 0) return;
            Send(0x2c, Wire.Body(w =>
            { w.Write(Player); w.Write(17u); w.Write(World.ResourceTargetId); w.Write(0u); }));
            Status = "Closing prospecting window";
        }

        /// <summary>Prospect one server-listed ore slot through native inventory transfer.</summary>
        public void ProspectResource(int slot)
        {
            if (!Ready || gate != 0 || !World.ResourceWindowOpen ||
                !World.Resources.TryGetValue(World.ResourceTargetId, out ResourceState resource) ||
                slot < 0 || slot >= resource.Items.Length || resource.Items[slot]?.Present != true) return;
            SendInventoryTransfer(18, (uint)slot, 0, 0, 0);
            Status = "Prospecting " + resource.Items[slot].Template;
        }

        /// <summary>Send one native group formation action after local validation.</summary>
        public void Formation(uint action, uint target = 0)
        {
            if (!Ready || gate != 0 || !((action >= 4 && action <= 9) || action == 12)) return;
            Send(0xbc, Wire.Body(w => { w.Write(Player); w.Write(target); w.Write(action); }));
        }

        /// <summary>Select a visible MOB and toggle equipped weapons only after its matching acknowledgment.</summary>
        public void ToggleWeapons(uint target)
        {
            if (!Ready || gate != 0 || combatTarget != 0 || !World.Mobs.TryGetValue(target, out ShipActor mob) || !mob.HasPosition)
                return;
            combatTarget = target; combatTargetAt = clock.Elapsed.TotalSeconds;
            Send(0x17, Wire.Body(w => { w.Write(Player); w.Write(target); }));
            Status = "Selecting " + mob.Name;
        }

        /// <summary>Send a gate-stage action without repeating an uncertain activation.</summary>
        private void GateAction(uint action)
        { Send(0x2c, Wire.Body(w => { w.Write(Player); w.Write(action); w.Write(gate); w.Write(0u); })); }

        /// <summary>Apply a world frame and answer required session control messages.</summary>
        private void Apply(Frame frame)
        {
            World.Apply(frame, Player); byte[] b = frame.Body;
            switch (frame.Opcode)
            {
                case 0x2020: if (b.Length == 4) Send(0x2021, b); break;
                case 5:
                    if (b.Length != 4) throw new InvalidDataException("Invalid sector start.");
                    Send(6, b); Send(0x3004, Wire.Body(w => w.Write(Player))); Ready = true; Status = "Connected"; break;
                case 0x19:
                    if (b.Length == 8 && dock != 0 && !dockRequested && Wire.U32(b, 0) == dock && Wire.U32(b, 4) == uint.MaxValue)
                    {
                        dockRequested = true;
                        Send(0x2c, Wire.Body(w => { w.Write(Player); w.Write(28u); w.Write(dock); w.Write(0u); }));
                        Status = "Waiting for docking clearance";
                    }
                    if (b.Length == 8 && pendingLoot != 0 && !lootRequested && Wire.U32(b, 0) == pendingLoot && Wire.U32(b, 4) == uint.MaxValue)
                    {
                        lootRequested = true;
                        Send(0x51, Wire.Body(w => { w.Write(Player); w.Write(0u); }));
                        Status = "Opening wreck";
                    }
                    if (b.Length == 8 && pendingResource != 0 && Wire.U32(b, 0) == pendingResource && Wire.U32(b, 4) == uint.MaxValue)
                    {
                        Send(0x2c, Wire.Body(w =>
                        { w.Write(Player); w.Write(17u); w.Write(pendingResource); w.Write(0u); }));
                        pendingResource = 0; Status = "Opening prospecting window";
                    }
                    if (b.Length == 8 && combatTarget != 0 && Wire.U32(b, 0) == combatTarget && Wire.U32(b, 4) == uint.MaxValue)
                    {
                        Send(0x2c, Wire.Body(w => { w.Write(Player); w.Write(9u); w.Write(combatTarget); w.Write(0u); }));
                        combatTarget = 0; Status = "Weapon toggle sent";
                    }
                    if (b.Length == 8 && followTarget != 0 && Wire.U32(b, 0) == followTarget && Wire.U32(b, 4) == uint.MaxValue)
                    {
                        Send(0x14, Wire.Body(w => { w.Write(Player); w.Write((byte)12); }));
                        followTarget = 0; Status = "Following selected ship";
                    }
                    if (b.Length == 8 && gate != 0 && Wire.U32(b, 0) == gate && Wire.U32(b, 4) == uint.MaxValue && !gateActivated)
                    { GateAction(18); gateActivated = true; gateAt = clock.Elapsed.TotalSeconds; Status = "Gate sequence running"; }
                    break;
                case 0x8c:
                    if (b.Length == 4 && pendingLoot != 0 && Wire.U32(b, 0, true) == pendingLoot)
                    { World.SetOpenLoot(pendingLoot); pendingLoot = 0; Status = "Wreck open"; }
                    break;
                case 0x92:
                    if (b.Length == 8 && dock != 0 && dockRequested && Wire.U32(b, 0, true) == 5 && Wire.U32(b, 4, true) == dock)
                    {
                        Send(0x2c, Wire.Body(w => { w.Write(Player); w.Write(7u); w.Write(dock); w.Write(0u); }));
                        dock = 0; Status = "Entering station";
                    }
                    if (b.Length == 8 && gate != 0 && Wire.U32(b, 0, true) == 4 && Wire.U32(b, 4, true) == gate) gateCamera = true;
                    break;
                case 0x1d:
                    if (b.Length > 3)
                    {
                        Status = Wire.Text(b, 3, b.Length - 3);
                        string lower = Status.ToLowerInvariant();
                        if (lower.Contains("cannot gate") || lower.Contains("unable to gate") || lower.Contains("gate inoperative") || lower.Contains("gate malfunction") || lower.Contains("sector is offline")) gate = 0;
                    }
                    break;
                case 0x66:
                    Status = World.ResourceWindowOpen ? "Prospecting window open" : "Prospecting window closed";
                    break;
                case 0x3a: handoff = Wire.Slice(b, 0, 64); Ready = false; break;
                case 0xba: throw new IOException("Server ended the character session.");
            }
        }

        /// <summary>Receive one batch and service retransmission, gate, and keepalive deadlines.</summary>
        public void Poll()
        {
            cancel.ThrowIfCancellationRequested();
            try
            {
                byte[] packet = null;
                try { IPEndPoint endpoint = null; packet = sector.Receive(ref endpoint); }
                catch (SocketException e) when (e.SocketErrorCode == SocketError.TimedOut || e.SocketErrorCode == SocketError.WouldBlock) { }
                double now = clock.Elapsed.TotalSeconds;
                if (packet != null)
                {
                    ushort opcode = Wire.Datagram(packet, Player);
                    if (opcode == 0x2016 || opcode == 0x201a)
                    {
                        foreach (Frame frame in stream.Feed(packet)) { Apply(frame); if (handoff != null) break; }
                        receivedAt = now;
                    }
                }
                if (handoff != null) { Enter(handoff); return; }
                if (pendingLoot != 0 && now - pendingLootAt > 5)
                { pendingLoot = 0; Status = "Wreck did not open"; }
                if (pendingResource != 0 && now - pendingResourceAt > 5)
                { pendingResource = 0; World.SetResourceTarget(0); Status = "Asteroid did not acknowledge selection"; }
                if (dock != 0 && now - dockAt > 8)
                { dock = 0; Status = "Docking timed out"; }
                if (combatTarget != 0 && now - combatTargetAt > 5)
                { combatTarget = 0; Status = "Target did not acknowledge weapon command"; }
                if (followTarget != 0 && now - followTargetAt > 5)
                { followTarget = 0; Status = "Follow target did not acknowledge"; }
                if (gate != 0)
                {
                    double elapsed = now - gateAt;
                    if (gateActivated && gateCamera && !gateFinished && elapsed >= 15)
                    { GateAction(19); gateFinished = true; Status = "Waiting for destination"; }
                    else if (elapsed > (gateActivated ? 45 : 5)) { gate = 0; Status = "Gate timed out; no activation retry sent"; }
                }
                if (stream.MissingCount > 0 && now - resendAt >= .25)
                { Send(0x2017, Wire.Body(w => { w.Write(stream.Expected); w.Write(stream.MissingCount); })); resendAt = now; }
                if (now - keepaliveAt >= 15)
                { Send(0x3005, Array.Empty<byte>()); SendUdp(comms, 0x3005, Array.Empty<byte>(), Player, 0); keepaliveAt = now; }
                if (now - receivedAt > 45 || !Ready && now - enteredAt > 30) throw new TimeoutException("Sector stopped responding.");
            }
            catch (OperationCanceledException) { throw; }
            catch { failed = true; throw; }
        }

        /// <summary>Log out on deliberate closure and release worker-owned sockets.</summary>
        public void Dispose()
        {
            if (disposed) return; disposed = true;
            try
            {
                if (!failed && Player != 0 && comms != null)
                    SendUdp(comms, 0x1008, Wire.Body(w => w.Write(Player)), Player, 0);
            }
            catch (SocketException) { }
            finally { sector?.Dispose(); comms?.Dispose(); }
        }
    }
}
