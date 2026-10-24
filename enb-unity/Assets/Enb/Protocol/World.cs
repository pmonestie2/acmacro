using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace Enb.Protocol
{
    public readonly struct Position
    {
        public readonly float X, Y, Z;
        /// <summary>Store coordinates in server meters.</summary>
        public Position(float x, float y, float z) { X = x; Y = y; Z = z; }
        /// <returns>The Euclidean distance in server meters.</returns>
        public float Distance(Position other)
        { float x = X - other.X, y = Y - other.Y, z = Z - other.Z; return (float)Math.Sqrt(x * x + y * y + z * z); }
    }

    public sealed class MapObject
    {
        public readonly uint Id;
        public readonly string Name;
        public readonly byte Kind;
        public readonly ushort Asset;
        public readonly Position Position;
        public readonly bool Navigable;
        public readonly float Scale;
        public readonly float[] Orientation;

        /// <summary>Decode a validated map-object record.</summary>
        public MapObject(byte[] body)
        {
            Wire.Require(body, 0, 60);
            int length = Wire.U16(body, 58);
            if (length == 0 || body.Length != 60 + length) throw new InvalidDataException("Invalid map-object name.");
            Id = Wire.U32(body, 0); Kind = body[4]; Asset = Wire.U16(body, 5);
            Name = Wire.Text(body, 60, length);
            Scale = Wire.Float(body, 7);
            if (Scale <= 0 || Scale > 10000) throw new InvalidDataException("Invalid object scale.");
            Orientation = new[] { Wire.Float(body, 37), Wire.Float(body, 41), Wire.Float(body, 45), Wire.Float(body, 49) };
            Position = new Position(Wire.Float(body, 25), Wire.Float(body, 29), Wire.Float(body, 33));
            if (Math.Abs(Position.X) >= 1e9 || Math.Abs(Position.Y) >= 1e9 || Math.Abs(Position.Z) >= 1e9)
                throw new InvalidDataException("Map coordinates exceed limits.");
            Navigable = (body[57] & 0x10) != 0;
        }
    }

    /// <summary>Server-owned station identity and available interior services.</summary>
    public sealed class StationState
    {
        public readonly uint Id, Kind, Rooms, Terminals;
        public readonly IReadOnlyList<uint> NpcIds;
        public readonly string Name;

        /// <summary>Keep station details immutable across display snapshots.</summary>
        public StationState(uint id, uint kind = 0, uint rooms = 0, uint terminals = 0, uint[] npcIds = null, string name = "")
        { Id = id; Kind = kind; Rooms = rooms; Terminals = terminals; NpcIds = Array.AsReadOnly((uint[])(npcIds ?? Array.Empty<uint>()).Clone()); Name = name; }

        /// <returns>The same station with its authoritative interior layout counts.</returns>
        public StationState WithLayout(byte[] body)
        {
            Wire.Require(body, 0, 8);
            uint kind = Wire.U32(body, 0), rooms = Wire.U32(body, 4);
            if (rooms == 0 || rooms > 5) throw new InvalidDataException("Invalid station room count.");
            int offset = checked(8 + (int)rooms * 28);
            Wire.Require(body, offset, 4);
            uint terminals = Wire.U32(body, offset);
            if (terminals > 15) throw new InvalidDataException("Invalid station terminal count.");
            offset += 4 + (int)terminals * 16;
            Wire.Require(body, offset, 4);
            uint npcs = Wire.U32(body, offset);
            if (npcs > 30 || offset + 4 + (int)npcs * 265 != body.Length)
                throw new InvalidDataException("Invalid station NPC layout.");
            var npcIds = new uint[(int)npcs];
            for (int i = 0; i < npcIds.Length; i++) npcIds[i] = Wire.U32(body, offset + 4 + i * 265 + 8);
            return new StationState(Id, kind, rooms, terminals, npcIds, Name);
        }

        /// <returns>The same station with its announced name.</returns>
        public StationState WithName(string name)
        { return new StationState(Id, Kind, Rooms, Terminals, NpcIds.ToArray(), name); }
    }

    /// <summary>Worker-owned contents and depletion state for a mineable resource.</summary>
    public sealed class ResourceState
    {
        public readonly uint Id, Level;
        public readonly string Name;
        public readonly float Fraction;
        public readonly ItemState[] Items;

        /// <summary>Keep one authoritative resource revision together.</summary>
        public ResourceState(uint id, string name, uint level, float fraction, ItemState[] items)
        { Id = id; Name = name; Level = level; Fraction = fraction; Items = items; }
    }

    /// <summary>Immutable resource rows for the main-thread prospecting panel.</summary>
    public sealed class ResourceView
    {
        public readonly uint Id, Level;
        public readonly string Name;
        public readonly float Fraction;
        public readonly ItemView[] Items;

        /// <summary>Resolve server-owned ore templates for display.</summary>
        public ResourceView(ResourceState state, IReadOnlyDictionary<uint, ItemTemplateInfo> catalog)
        {
            Id = state.Id; Name = state.Name; Level = state.Level; Fraction = state.Fraction;
            Items = new ItemView[state.Items.Length];
            for (int i = 0; i < Items.Length; i++)
                if (state.Items[i]?.Present == true) Items[i] = new ItemView(i, state.Items[i], catalog);
        }
    }

    /// <summary>One server-offered NPC conversation branch.</summary>
    public sealed class DialogueChoice
    {
        public readonly byte Node;
        public readonly string Text;
        /// <summary>Keep the native node identity with its displayed choice.</summary>
        public DialogueChoice(byte node, string text) { Node = node; Text = text; }
    }

    /// <summary>The active station conversation and its authoritative choices.</summary>
    public sealed class DialogueState
    {
        public readonly uint NpcId;
        public readonly string Text;
        public readonly DialogueChoice[] Choices;
        public readonly bool VendorOpen;

        /// <summary>Store one immutable conversation view.</summary>
        public DialogueState(uint npcId, string text = "", DialogueChoice[] choices = null, bool vendorOpen = false)
        { NpcId = npcId; Text = text; Choices = choices ?? Array.Empty<DialogueChoice>(); VendorOpen = vendorOpen; }

        /// <returns>A conversation updated from the native talk-tree packet.</returns>
        public DialogueState WithTree(byte[] body)
        {
            int end = Array.IndexOf(body, (byte)0);
            if (end < 0 || end + 2 >= body.Length || body[end + 1] != 0)
                throw new InvalidDataException("Invalid NPC dialogue text.");
            int count = body[end + 2], offset = end + 3;
            if (count > 20) throw new InvalidDataException("Too many NPC choices.");
            var choices = new DialogueChoice[count];
            for (int i = 0; i < count; i++)
            {
                Wire.Require(body, offset, 4);
                uint node = Wire.U32(body, offset); offset += 4;
                if (node > byte.MaxValue) throw new InvalidDataException("Invalid NPC choice identity.");
                int labelEnd = Array.IndexOf(body, (byte)0, offset);
                if (labelEnd < 0) throw new InvalidDataException("Unterminated NPC choice.");
                choices[i] = new DialogueChoice((byte)node, Wire.Text(body, offset, labelEnd - offset));
                offset = labelEnd + 1;
            }
            if (offset != body.Length) throw new InvalidDataException("Unexpected NPC dialogue data.");
            return new DialogueState(NpcId, Wire.Text(body, 0, end), choices, VendorOpen);
        }

        /// <returns>The same conversation with its vendor status changed.</returns>
        public DialogueState WithVendor(bool open)
        { return new DialogueState(NpcId, Text, Choices, open); }
    }

    /// <summary>An immutable visible ship contact copied across the network boundary.</summary>
    public sealed class ShipActor
    {
        public readonly uint Id;
        public readonly ushort Asset;
        public readonly float Scale;
        public readonly string Name;
        public readonly Position Position;
        public readonly float[] Orientation;
        public readonly bool HasPosition;
        public readonly bool IsPlayer, Hostile;
        public readonly float Shield, Hull, MaxHull;

        /// <summary>Store a server actor and its latest known pose.</summary>
        public ShipActor(uint id, ushort asset, float scale, string name, Position position, float[] orientation, bool hasPosition, bool isPlayer = true, bool hostile = false, float shield = -1, float hull = -1, float maxHull = -1)
        {
            Id = id; Asset = asset; Scale = scale; Name = name; Position = position;
            Orientation = (float[])orientation.Clone(); HasPosition = hasPosition;
            IsPlayer = isPlayer; Hostile = hostile; Shield = shield; Hull = hull; MaxHull = maxHull;
        }

        /// <returns>A new actor with an authoritative pose, leaving old snapshots unchanged.</returns>
        public ShipActor WithPose(byte[] body)
        {
            var position = new Position(Wire.Float(body, 10), Wire.Float(body, 14), Wire.Float(body, 18));
            if (Math.Abs(position.X) >= 1e9 || Math.Abs(position.Y) >= 1e9 || Math.Abs(position.Z) >= 1e9)
                throw new InvalidDataException("Player coordinates exceed limits.");
            var orientation = new[] { Wire.Float(body, 22), Wire.Float(body, 26), Wire.Float(body, 30), Wire.Float(body, 34) };
            return new ShipActor(Id, Asset, Scale, Name, position, orientation, true, IsPlayer, Hostile, Shield, Hull, MaxHull);
        }

        /// <returns>A new actor with its public avatar name.</returns>
        public ShipActor WithName(string name)
        { return new ShipActor(Id, Asset, Scale, name, Position, Orientation, HasPosition, IsPlayer, Hostile, Shield, Hull, MaxHull); }
    }

    /// <summary>A server-published shot between visible ship identities.</summary>
    public sealed class ShotEvent
    {
        public readonly long Sequence;
        public readonly uint Source, Target;
        public readonly ushort Asset;
        public readonly ushort Duration;
        /// <summary>Store the native weapon-effect identity and display duration.</summary>
        public ShotEvent(long sequence, uint source, uint target, ushort asset, ushort duration)
        { Sequence = sequence; Source = source; Target = target; Asset = asset; Duration = duration; }
    }

    /// <summary>A server-reported impact for transient damage feedback.</summary>
    public sealed class DamageEvent
    {
        public readonly long Sequence;
        public readonly uint Source, Target;
        public readonly float Amount;
        /// <summary>Store one bounded combat result.</summary>
        public DamageEvent(long sequence, uint source, uint target, float amount)
        { Sequence = sequence; Source = source; Target = target; Amount = amount; }
    }

    public sealed class World
    {
        public uint PlayerId { get; private set; }
        private long nextShot, nextDamage;
        public readonly Queue<DamageEvent> Damages = new Queue<DamageEvent>();
        public Dictionary<uint, ItemState[]> Loot { get; } = new Dictionary<uint, ItemState[]>();
        public uint OpenLootId { get; private set; }
        /// <summary>Record the server acknowledgement of a wreck window.</summary>
        public void SetOpenLoot(uint id) { OpenLootId = id; }
        public readonly Queue<ShotEvent> Shots = new Queue<ShotEvent>();
        public GroupState Group { get; private set; } = new GroupState(false, Array.Empty<GroupMember>());
        private string invitation = "";
        private long invitationAt;
        public string Invitation => (System.Diagnostics.Stopwatch.GetTimestamp() - invitationAt) / (double)System.Diagnostics.Stopwatch.Frequency < 30 ? invitation : "";
        /// <summary>Dismiss an invitation after a local answer.</summary>
        public void ClearInvitation() { invitation = ""; }
        public readonly Queue<string> Messages = new Queue<string>();
        public EquipmentState Equipment { get; } = new EquipmentState();
        public Dictionary<uint, ItemTemplateInfo> ItemCatalog { get; } = new Dictionary<uint, ItemTemplateInfo>();
        public Dictionary<uint, ShipActor> Mobs { get; } = new Dictionary<uint, ShipActor>();
        public string Sector { get; private set; } = "Connecting";
        public StationState Station { get; private set; }
        public DialogueState Dialogue { get; private set; }
        public ulong Credits { get; private set; }
        public uint[] AvailableAbilities { get; private set; } = Array.Empty<uint>();
        public uint CastingAbilityId { get; private set; } = uint.MaxValue;
        public long CastEndsAt { get; private set; }
        public Dictionary<uint, long> AbilityCooldownEnds { get; } = new Dictionary<uint, long>();
        public ItemState[] Vendor { get; private set; } = Array.Empty<ItemState>();
        public Dictionary<uint, ResourceState> Resources { get; } = new Dictionary<uint, ResourceState>();
        public uint ResourceTargetId { get; private set; }
        public bool ResourceWindowOpen { get; private set; }
        /// <summary>Track the selected resource while awaiting its native prospecting window.</summary>
        public void SetResourceTarget(uint id) { ResourceTargetId = id; ResourceWindowOpen = false; }
        /// <summary>Track the requested NPC while waiting for its talk tree.</summary>
        public void StartDialogue(uint npcId) { Dialogue = new DialogueState(npcId); }
        /// <summary>Clear the local conversation after the server closes it.</summary>
        public void CloseDialogue() { Dialogue = null; }
        public Dictionary<uint, MapObject> Objects { get; } = new Dictionary<uint, MapObject>();
        public Dictionary<uint, ShipActor> Players { get; } = new Dictionary<uint, ShipActor>();
        public Position Position { get; private set; }
        public float[] Orientation { get; private set; } = { 0, 0, 0, 1 };
        public bool HasPosition { get; private set; }
        public float Speed { get; private set; }
        public float TargetSpeed { get; private set; }
        public float Acceleration { get; private set; }
        public float AngularY { get; private set; }
        public float AngularZ { get; private set; }

        /// <summary>Update server-authoritative display state.</summary>
        public void Apply(Frame frame, uint player)
        {
            PlayerId = player;
            byte[] b = frame.Body;
            switch (frame.Opcode)
            {
                case 0x97:
                    if (b.Length < 15 || Wire.U32(b, 0) != 4 || Wire.U32(b, 4) != b.Length - 8)
                        throw new InvalidDataException("Invalid sector description.");
                    int firstEnd = Array.IndexOf(b, (byte)0, 12);
                    if (firstEnd < 0 || firstEnd + 1 >= b.Length) throw new InvalidDataException("Missing sector name.");
                    string name = Wire.Text(b, firstEnd + 1, b.Length - firstEnd - 1);
                    if (name.Length == 0) throw new InvalidDataException("Empty sector name.");
                    if (Sector != name) { Objects.Clear(); Players.Clear(); Mobs.Clear(); Loot.Clear(); Resources.Clear(); ResourceTargetId = 0; ResourceWindowOpen = false; OpenLootId = 0; Shots.Clear(); Damages.Clear(); nextShot = nextDamage = 0; }
                    Sector = name;
                    break;
                case 0x4f:
                    if (b.Length != 6) throw new InvalidDataException("Invalid station identity.");
                    Station = b[4] == 0 ? new StationState(Wire.U32(b, 0)) : null;
                    Dialogue = null; Vendor = Array.Empty<ItemState>();
                    ResourceTargetId = 0; ResourceWindowOpen = false;
                    if (Station != null) Sector = "Station " + Station.Id;
                    break;
                case 0x52:
                    if (Station == null) throw new InvalidDataException("Station layout arrived before its identity.");
                    Station = Station.WithLayout(b);
                    break;
                case 0x54:
                    Dialogue = (Dialogue ?? new DialogueState(0)).WithTree(b);
                    break;
                case 0x56:
                    if (b.Length != 4) throw new InvalidDataException("Invalid NPC control.");
                    int control = unchecked((int)Wire.U32(b, 0));
                    if (control == -32) Dialogue = null;
                    else if (control == 6 && Dialogue != null) Dialogue = Dialogue.WithVendor(true);
                    break;
                case 0x0b:
                    Wire.Require(b, 0, 23);
                    if (Wire.U16(b, 0) != 7) break;
                    uint source = Wire.U32(b, 2), victim = Wire.U32(b, 6);
                    ushort duration = Wire.U16(b, 21);
                    if (duration > 0 && duration <= 10000)
                    {
                        Shots.Enqueue(new ShotEvent(++nextShot, source, victim, Wire.U16(b, 10), duration));
                        while (Shots.Count > 32) Shots.Dequeue();
                    }
                    break;
                case 0x64:
                    Wire.Require(b, 0, 24);
                    float amount = Wire.Float(b, 0);
                    if (amount > 0 && amount < 1e9f)
                    {
                        Damages.Enqueue(new DamageEvent(++nextDamage, Wire.U32(b, 16), Wire.U32(b, 20), amount));
                        while (Damages.Count > 32) Damages.Dequeue();
                    }
                    break;
                case 0x2018:
                    var item = new MapObject(b);
                    if (!Objects.ContainsKey(item.Id) && Objects.Count >= 10000) throw new InvalidDataException("World object limit exceeded.");
                    Objects[item.Id] = item;
                    break;
                case 4:
                    Wire.Require(b, 0, 23);
                    uint actorId = Wire.U32(b, 0);
                    if (actorId == player || b[10] > 1) break;
                    float scale = Wire.Float(b, 4);
                    if (scale <= 0 || scale > 10000) throw new InvalidDataException("Invalid player scale.");
                    var actors = b[10] == 1 ? Players : Mobs;
                    if (!actors.ContainsKey(actorId) && actors.Count >= 1024)
                        throw new InvalidDataException("Visible player limit exceeded.");
                    actors[actorId] = new ShipActor(actorId, Wire.U16(b, 8), scale, (b[10] == 1 ? "Player " : "Mob ") + actorId,
                        default, new[] { 0f, 0f, 0f, 1f }, false, b[10] == 1);
                    break;
                case 0x1e:
                    Wire.Require(b, 0, 3);
                    invitation = Wire.Text(b, 3, b.Length - 3); invitationAt = System.Diagnostics.Stopwatch.GetTimestamp();
                    break;
                case 0x1d:
                    Wire.Require(b, 0, 3);
                    string message = Wire.Text(b, 3, b.Length - 3);
                    Messages.Enqueue(message);
                    if (Station != null && message.StartsWith("Welcome aboard ", StringComparison.Ordinal))
                    {
                        int end = message.IndexOf('.', 15);
                        if (end > 15) { Station = Station.WithName(message.Substring(15, end - 15)); Sector = Station.Name; }
                    }
                    while (Messages.Count > 6) Messages.Dequeue();
                    break;
                case 0x25:
                    ItemTemplateInfo template = ItemTemplateInfo.Decode(b);
                    if (!ItemCatalog.ContainsKey(template.Id) && ItemCatalog.Count >= 1000)
                        throw new InvalidDataException("Item template limit exceeded.");
                    ItemCatalog[template.Id] = template;
                    break;
                case 0x1b:
                    Wire.Require(b, 0, 8);
                    if (Wire.U32(b, 0) == 0)
                    {
                        ulong? credits = AuxState.ReadCredits(b);
                        if (credits.HasValue) Credits = credits.Value;
                        uint[] abilities = AuxState.ReadAvailableAbilities(b);
                        if (abilities != null) AvailableAbilities = abilities;
                        AbilityProgress progress = AuxState.ReadAbilityProgress(b);
                        if (progress != null)
                        {
                            CastingAbilityId = progress.Id;
                            CastEndsAt = progress.Id == uint.MaxValue ? 0 :
                                System.Diagnostics.Stopwatch.GetTimestamp() +
                                (long)(progress.DurationMs * (double)System.Diagnostics.Stopwatch.Frequency / 1000);
                        }
                        SkillCooldown[] cooldowns = AuxState.ReadSkillCooldowns(b);
                        if (cooldowns != null)
                            foreach (SkillCooldown cooldown in cooldowns)
                                AbilityCooldownEnds[cooldown.SkillId] = System.Diagnostics.Stopwatch.GetTimestamp() +
                                    (long)(cooldown.DurationMs * (double)System.Diagnostics.Stopwatch.Frequency / 1000);
                        ItemState[] vendor = AuxState.ReadVendor(b);
                        if (vendor != null) Vendor = vendor;
                        GroupState group = AuxState.ReadGroup(b);
                        if (group != null) { Group = group; if (group.Joined) ClearInvitation(); }
                    }
                    else if (Wire.U32(b, 0) == player) AuxState.ReadOwn(b, Equipment);
                    else
                    {
                        uint id = Wire.U32(b, 0);
                        if (Objects.TryGetValue(id, out MapObject corpse) && corpse.Kind == 25)
                        {
                            ItemState[] contents = AuxState.ReadCorpse(b);
                            if (contents != null) Loot[id] = contents;
                            break;
                        }
                        if (Objects.TryGetValue(id, out MapObject resource) && resource.Kind == 38)
                        {
                            Resources.TryGetValue(id, out ResourceState previous);
                            Resources[id] = AuxState.ReadResource(b, id, previous);
                            break;
                        }
                        var contacts = Players.ContainsKey(id) ? Players : Mobs;
                        if (contacts.TryGetValue(id, out ShipActor contact)) contacts[id] = AuxState.ReadShip(b, contact);
                    }
                    break;
                case 0x89:
                    Wire.Require(b, 0, 8);
                    uint relationId = Wire.U32(b, 0, true);
                    if (Mobs.TryGetValue(relationId, out ShipActor related))
                        Mobs[relationId] = new ShipActor(related.Id, related.Asset, related.Scale, related.Name, related.Position,
                            related.Orientation, related.HasPosition, false, Wire.U32(b, 4) == 0, related.Shield, related.Hull, related.MaxHull);
                    break;
                case 0x61:
                    Wire.Require(b, 0, 24);
                    uint avatarId = Wire.U32(b, 0);
                    if (Players.TryGetValue(avatarId, out ShipActor avatar))
                    {
                        string avatarName = Wire.Text(b, 4, 20);
                        if (avatarName.Length > 0) Players[avatarId] = avatar.WithName(avatarName);
                    }
                    break;
                case 7:
                    if (b.Length != 4) throw new InvalidDataException("Invalid removal.");
                    Objects.Remove(Wire.U32(b, 0));
                    Loot.Remove(Wire.U32(b, 0));
                    Resources.Remove(Wire.U32(b, 0));
                    if (OpenLootId == Wire.U32(b, 0)) OpenLootId = 0;
                    if (ResourceTargetId == Wire.U32(b, 0)) SetResourceTarget(0);
                    Players.Remove(Wire.U32(b, 0)); Mobs.Remove(Wire.U32(b, 0));
                    break;
                case 0x3e:
                    Wire.Require(b, 0, 38);
                    uint movingId = Wire.U32(b, 2);
                    if (movingId != player)
                    {
                        if (Players.TryGetValue(movingId, out ShipActor moving)) Players[movingId] = moving.WithPose(b);
                        if (Mobs.TryGetValue(movingId, out ShipActor mob)) Mobs[movingId] = mob.WithPose(b);
                        break;
                    }
                    Position = new Position(Wire.Float(b, 10), Wire.Float(b, 14), Wire.Float(b, 18));
                    Orientation = new[] { Wire.Float(b, 22), Wire.Float(b, 26), Wire.Float(b, 30), Wire.Float(b, 34) };
                    HasPosition = true;
                    if (b.Length >= 70)
                    {
                        Speed = Wire.Float(b, 42) * 1000;
                        TargetSpeed = Wire.Float(b, 46) * 1000;
                        Acceleration = Wire.Float(b, 50) * 1000;
                        AngularY = Wire.Float(b, 54);
                        AngularZ = Wire.Float(b, 62);
                    }
                    else if (b.Length >= 46) Speed = Wire.Float(b, 42) * 1000;
                    break;
                case 0x66:
                    if (b.Length != 8 || Wire.U32(b, 4) != 0 || Wire.U32(b, 0) > 1)
                        throw new InvalidDataException("Invalid prospecting window state.");
                    ResourceWindowOpen = Wire.U32(b, 0) == 0;
                    if (!ResourceWindowOpen) ResourceTargetId = 0;
                    break;
            }
        }
    }

    /// <summary>Display copy of the slots inside one visible wreck.</summary>
    public sealed class CorpseLoot
    {
        public readonly uint Id;
        public readonly ItemView[] Items;
        /// <summary>Resolve visible wreck items against known templates.</summary>
        public CorpseLoot(uint id, ItemState[] contents, IReadOnlyDictionary<uint, ItemTemplateInfo> catalog)
        {
            Id = id; Items = new ItemView[contents.Length];
            for (int i = 0; i < contents.Length; i++)
                if (contents[i]?.Present == true) Items[i] = new ItemView(i, contents[i], catalog);
        }
    }

    public sealed class Snapshot
    {
        public readonly string Sector, Status;
        public readonly Position Position;
        public readonly float[] Orientation;
        public readonly MapObject[] Objects;
        public readonly ShipActor[] Players, Mobs;
        public readonly ShotEvent[] Shots;
        public readonly DamageEvent[] Damages;
        public readonly uint PlayerId;
        public readonly GroupState Group;
        public readonly StationState Station;
        public readonly DialogueState Dialogue;
        public readonly ulong Credits;
        public readonly uint[] AvailableAbilities;
        public readonly uint CastingAbilityId;
        public readonly float CastRemainingSeconds;
        public readonly IReadOnlyDictionary<uint, float> AbilityCooldownSeconds;
        public readonly ItemView[] Vendor;
        public readonly ResourceView[] Resources;
        public readonly uint ResourceTargetId;
        public readonly bool ResourceWindowOpen;
        public readonly string Invitation;
        public readonly string[] Messages;
        public readonly bool Ready, HasPosition;
        public readonly float Speed, Energy, Shield, Hull, MaxEnergy, MaxShield, MaxHull;
        public readonly float TargetSpeed, Acceleration, AngularY, AngularZ;
        public readonly ItemView[] Cargo, Mounted, Ammo;
        public readonly CorpseLoot[] Loot;
        public readonly uint OpenLootId;
        public readonly uint WeaponMounts, DeviceMounts, SelectedTargetId;
        public readonly bool WeaponsFiring;

        /// <summary>Copy worker-owned state for main-thread rendering.</summary>
        public Snapshot(World world, string status, bool ready)
        {
            Sector = world.Sector; Status = status; Position = world.Position;
            Orientation = (float[])world.Orientation.Clone(); HasPosition = world.HasPosition;
            Objects = world.Objects.Values.OrderBy(x => x.Name).ToArray(); Ready = ready;
            Speed = world.Speed;
            TargetSpeed = world.TargetSpeed; Acceleration = world.Acceleration;
            AngularY = world.AngularY; AngularZ = world.AngularZ;
            Players = world.Players.Values.ToArray(); Mobs = world.Mobs.Values.ToArray();
            PlayerId = world.PlayerId; Shots = world.Shots.ToArray(); Damages = world.Damages.ToArray();
            Group = world.Group; Station = world.Station; Dialogue = world.Dialogue; Credits = world.Credits;
            Vendor = new ItemView[world.Vendor.Length];
            AvailableAbilities = (uint[])world.AvailableAbilities.Clone();
            long now = System.Diagnostics.Stopwatch.GetTimestamp();
            CastingAbilityId = world.CastingAbilityId;
            CastRemainingSeconds = (float)Math.Max(0,
                (world.CastEndsAt - now) / (double)System.Diagnostics.Stopwatch.Frequency);
            AbilityCooldownSeconds = world.AbilityCooldownEnds.ToDictionary(entry => entry.Key,
                entry => (float)Math.Max(0,
                    (entry.Value - now) / (double)System.Diagnostics.Stopwatch.Frequency));
            for (int i = 0; i < Vendor.Length; i++)
                if (world.Vendor[i]?.Present == true) Vendor[i] = new ItemView(i, world.Vendor[i], world.ItemCatalog);
            Resources = world.Resources.Values.Select(state => new ResourceView(state, world.ItemCatalog)).ToArray();
            ResourceTargetId = world.ResourceTargetId; ResourceWindowOpen = world.ResourceWindowOpen;
            Invitation = world.Invitation; Messages = world.Messages.ToArray();
            OpenLootId = world.OpenLootId;
            Loot = world.Loot.Select(entry => new CorpseLoot(entry.Key, entry.Value, world.ItemCatalog)).ToArray();
            EquipmentState equipment = world.Equipment;
            Energy = equipment.Energy; Shield = equipment.Shield; Hull = equipment.Hull;
            MaxEnergy = equipment.MaxEnergy; MaxShield = equipment.MaxShield; MaxHull = equipment.MaxHull;
            WeaponMounts = equipment.WeaponMounts; DeviceMounts = equipment.DeviceMounts;
            SelectedTargetId = equipment.TargetId;
            WeaponsFiring = equipment.Mounted.Any(item => item != null && (item.WeaponState & 1) != 0);
            Cargo = new ItemView[equipment.Cargo.Length]; Mounted = new ItemView[equipment.Mounted.Length];
            Ammo = new ItemView[equipment.Ammo.Length];
            for (int i = 0; i < Cargo.Length; i++)
                if (equipment.Cargo[i]?.Present == true) Cargo[i] = new ItemView(i, equipment.Cargo[i], world.ItemCatalog);
            for (int i = 0; i < Mounted.Length; i++)
                if (equipment.Mounted[i]?.Present == true) Mounted[i] = new ItemView(i, equipment.Mounted[i], world.ItemCatalog);
            for (int i = 0; i < Ammo.Length; i++)
                if (equipment.Ammo[i]?.Present == true) Ammo[i] = new ItemView(i, equipment.Ammo[i], world.ItemCatalog);
        }
    }
}
