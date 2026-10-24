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
        public Dictionary<uint, MapObject> Objects { get; } = new Dictionary<uint, MapObject>();
        public Dictionary<uint, ShipActor> Players { get; } = new Dictionary<uint, ShipActor>();
        public Position Position { get; private set; }
        public float[] Orientation { get; private set; } = { 0, 0, 0, 1 };
        public bool HasPosition { get; private set; }
        public float Speed { get; private set; }

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
                    if (Sector != name) { Objects.Clear(); Players.Clear(); Mobs.Clear(); Loot.Clear(); OpenLootId = 0; Shots.Clear(); Damages.Clear(); nextShot = nextDamage = 0; }
                    Sector = name;
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
                    Messages.Enqueue(Wire.Text(b, 3, b.Length - 3));
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
                    if (OpenLootId == Wire.U32(b, 0)) OpenLootId = 0;
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
                    if (b.Length >= 46) Speed = Wire.Float(b, 42) * 1000;
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
        public readonly string Invitation;
        public readonly string[] Messages;
        public readonly bool Ready, HasPosition;
        public readonly float Speed, Energy, Shield, Hull, MaxEnergy, MaxShield, MaxHull;
        public readonly ItemView[] Cargo, Mounted;
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
            Players = world.Players.Values.ToArray(); Mobs = world.Mobs.Values.ToArray();
            PlayerId = world.PlayerId; Shots = world.Shots.ToArray(); Damages = world.Damages.ToArray();
            Group = world.Group; Invitation = world.Invitation; Messages = world.Messages.ToArray();
            OpenLootId = world.OpenLootId;
            Loot = world.Loot.Select(entry => new CorpseLoot(entry.Key, entry.Value, world.ItemCatalog)).ToArray();
            EquipmentState equipment = world.Equipment;
            Energy = equipment.Energy; Shield = equipment.Shield; Hull = equipment.Hull;
            MaxEnergy = equipment.MaxEnergy; MaxShield = equipment.MaxShield; MaxHull = equipment.MaxHull;
            WeaponMounts = equipment.WeaponMounts; DeviceMounts = equipment.DeviceMounts;
            SelectedTargetId = equipment.TargetId;
            WeaponsFiring = equipment.Mounted.Any(item => item != null && (item.WeaponState & 1) != 0);
            Cargo = new ItemView[equipment.Cargo.Length]; Mounted = new ItemView[equipment.Mounted.Length];
            for (int i = 0; i < Cargo.Length; i++)
                if (equipment.Cargo[i]?.Present == true) Cargo[i] = new ItemView(i, equipment.Cargo[i], world.ItemCatalog);
            for (int i = 0; i < Mounted.Length; i++)
                if (equipment.Mounted[i]?.Present == true) Mounted[i] = new ItemView(i, equipment.Mounted[i], world.ItemCatalog);
        }
    }
}
