using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using Enb.Protocol;

internal static class PeerChecks
{
    /// <summary>Verify player lifecycle packets without opening any network connection.</summary>
    public static void Decode()
    {
        const uint self = 0x40000001, peer = 0x40000002;
        var world = new World();
        world.Apply(Create(self, 1), self);
        world.Apply(Create(200, 0), self);
        world.Apply(Move(peer, 100), self);
        Require(world.Players.Count == 0, "Self, NPC, or unknown movement created a player.");
        Require(world.Mobs.Count == 1 && !world.Mobs[200].HasPosition, "Mob creation was not separated from players.");
        world.Apply(Move(200, 700), self);
        world.Apply(new Frame(0x89, Wire.Body(w => { Wire.Big(w, 200u); w.Write(0u); w.Write((byte)0); })), self);
        Require(world.Mobs[200].Hostile, "Hostile relation was not decoded with its big-endian identity.");
        Snapshot mobSnapshot = new Snapshot(world, "ready", true);
        world.Apply(Move(200, 800), self);
        Require(mobSnapshot.Mobs[0].Position.X == 700 && world.Mobs[200].Position.X == 800, "Mob snapshots share mutable poses.");
        world.Apply(new Frame(7, BitConverter.GetBytes(200u)), self);
        world.Apply(Move(200, 900), self);
        Require(world.Mobs.Count == 0, "Removed mob was resurrected by late movement.");
        world.Apply(Create(peer, 1), self);
        Require(!world.Players[peer].HasPosition, "A new player was placed at the origin before movement.");
        byte[] avatar = new byte[260];
        Array.Copy(BitConverter.GetBytes(peer), avatar, 4);
        Array.Copy(Encoding.ASCII.GetBytes("Visitor"), 0, avatar, 4, 7);
        world.Apply(new Frame(0x61, avatar), self);
        world.Apply(Move(peer, 100), self);
        Snapshot old = new Snapshot(world, "ready", true);
        world.Apply(Move(peer, 250), self);
        Require(old.Players[0].Name == "Visitor" && old.Players[0].Position.X == 100,
            "An existing snapshot was modified by later player movement.");
        Require(!world.HasPosition && world.Players[peer].Position.X == 250,
            "Remote movement changed the local player or was discarded.");
        world.Apply(new Frame(7, BitConverter.GetBytes(peer)), self);
        world.Apply(Move(peer, 999), self);
        Require(world.Players.Count == 0, "Movement resurrected a removed player.");
        world.Apply(Create(peer, 1), self);
        Require(!world.Players[peer].HasPosition, "Reentry retained an old pose.");
        byte[] sector = Wire.Body(w => { w.Write(4u); w.Write(0u); w.Write(1u); w.Write(Encoding.ASCII.GetBytes("Sol\0Saturn\0\0")); });
        Array.Copy(BitConverter.GetBytes((uint)sector.Length - 8), 0, sector, 4, 4);
        world.Apply(new Frame(0x97, sector), self);
        Require(world.Players.Count == 0 && old.Players.Length == 1, "Sector transition leaked player models or changed an old snapshot.");
        bool rejected = false;
        try { world.Apply(new Frame(4, new byte[10]), self); } catch (InvalidDataException) { rejected = true; }
        Require(rejected, "Truncated creation was accepted.");
        Console.WriteLine("PASS: peer creation, identity, movement, immutable snapshots, despawn, reentry, sector cleanup");
    }

    /// <returns>A native player or MOB creation packet.</returns>
    private static Frame Create(uint id, byte kind)
    { return new Frame(4, Wire.Body(w => { w.Write(id); w.Write(1f); w.Write((ushort)123); w.Write(kind); w.Write(new byte[12]); })); }

    /// <returns>A native position packet for a particular actor.</returns>
    private static Frame Move(uint id, float x)
    { return new Frame(0x3e, Wire.Body(w => { w.Write((ushort)0); w.Write(id); w.Write(0u); w.Write(x); w.Write(0f); w.Write(0f); w.Write(0f); w.Write(0f); w.Write(0f); w.Write(1f); w.Write(0u); })); }

    /// <summary>Fail when a peer lifecycle invariant does not hold.</summary>
    private static void Require(bool condition, string message)
    { if (!condition) throw new Exception(message); }

    /// <summary>Exercise two accounts only on the harness-owned disposable server.</summary>
    public static void Live(string host)
    {
        using (var observer = new LocalSession(CancellationToken.None))
        {
            observer.Connect(host, "local", "local");
            Wait(observer, null, () => observer.World.Equipment.Mounted[3]?.Present == true &&
                observer.World.ItemCatalog.TryGetValue(observer.World.Equipment.Mounted[3].Template, out ItemTemplateInfo template) &&
                template.Name.Length > 0, "Starter weapon or item template was not decoded.");
            uint peerId;
            using (var visitor = new LocalSession(CancellationToken.None))
            {
                visitor.Connect(host, "peer", "pass"); peerId = visitor.Player;
                Wait(observer, visitor, () => observer.Ready && visitor.Ready &&
                    observer.World.Players.TryGetValue(peerId, out ShipActor p) && p.HasPosition && p.Name == "Visitor" &&
                    visitor.World.Players.ContainsKey(observer.Player), "Mutual player visibility did not arrive.");
                uint starterWeapon = observer.World.Equipment.Mounted[3].Template;
                observer.UnequipMount(3);
                Wait(observer, null, () => observer.World.Equipment.Mounted[3]?.Present != true &&
                    observer.World.Equipment.Cargo[0]?.Template == starterWeapon, "Server unequip update did not move the weapon to cargo.");
                observer.EquipCargo(0, 3);
                Wait(observer, null, () => observer.World.Equipment.Mounted[3]?.Template == starterWeapon &&
                    observer.World.Equipment.Cargo[0]?.Present != true, "Server equip update did not restore the weapon.");
                Console.WriteLine("PASS: item template, cargo and hardpoint decoding; authoritative unequip and equip");
                Wait(observer, visitor, () => observer.World.Objects.Values.Any(item => item.Kind == 38),
                    "Disposable asteroid was not visible to the pilot.");
                MapObject resource = observer.World.Objects.Values.First(item => item.Kind == 38);
                observer.OpenResource(resource.Id);
                Wait(observer, visitor, () => observer.World.ResourceWindowOpen &&
                    observer.World.Resources.TryGetValue(resource.Id, out ResourceState stock) &&
                    stock.Items.Any(item => item?.Present == true),
                    "Native prospecting window or asteroid stock did not arrive.");
                ResourceState ore = observer.World.Resources[resource.Id];
                int oreSlot = Array.FindIndex(ore.Items, item => item?.Present == true);
                uint oreTemplate = ore.Items[oreSlot].Template;
                observer.ProspectResource(oreSlot);
                Wait(observer, visitor, () => observer.World.Equipment.Cargo.Any(item => item?.Template == oreTemplate),
                    "Prospecting did not award server-owned ore into cargo.");
                observer.CloseResource();
                Wait(observer, visitor, () => !observer.World.ResourceWindowOpen,
                    "Native prospecting window did not close.");
                Console.WriteLine("PASS: asteroid selection, native prospect window, ore tractor and cargo award");
                Position before = observer.World.Players[peerId].Position;
                visitor.Thrust(2);
                Wait(observer, visitor, () => observer.World.Players[peerId].Position.Distance(before) > 1900,
                    "Remote ship did not move far enough to test follow.");
                visitor.Thrust(4);
                Wait(observer, visitor, () => Math.Abs(visitor.World.Speed) < 1,
                    "Remote ship did not brake before follow.");
                float followDistance = visitor.World.Position.Distance(observer.World.Position);
                visitor.FollowTarget(observer.Player);
                Wait(observer, visitor, () => visitor.World.Position.Distance(observer.World.Position) < followDistance - 150,
                    "Native follow did not approach the selected player.");
                visitor.Thrust(4);
                Wait(observer, visitor, () => Math.Abs(visitor.World.Speed) < 1,
                    "Manual braking did not cancel follow.");
                Console.WriteLine("PASS: correlated native follow and manual-flight cancellation");
                observer.InvitePlayer(peerId);
                Wait(observer, visitor, () => visitor.World.Invitation.Contains("SandboxPilot"), "Group invitation did not arrive.");
                visitor.Chat("/decline");
                Wait(observer, visitor, () => visitor.World.Invitation == "", "Declined invitation remained visible.");
                // Ensure the server consumes the decline before inviting again.
                visitor.Chat("/where");
                Wait(observer, visitor, () => visitor.World.Messages.Any(m => m.Contains("Rust Sandbox")), "Decline barrier did not arrive.");
                observer.Chat("/invite Visitor");
                Wait(observer, visitor, () => visitor.World.Invitation.Length > 0, "Second invitation did not arrive.");
                visitor.Chat("/accept");
                Wait(observer, visitor, () => observer.World.Group.IsLeader && observer.World.Group.Members.Any(m => m.Id == peerId) &&
                    visitor.World.Group.Members.Any(m => m.Id == observer.Player), "Group roster did not converge.");
                observer.Formation(4);
                Wait(observer, visitor, () => observer.World.Group.Formation == "Slot Back" &&
                    observer.World.Group.Formed && visitor.World.Group.Formation == "Slot Back" &&
                    !visitor.World.Group.Formed, "Leader formation choice was not published.");
                visitor.Formation(7);
                Wait(observer, visitor, () => visitor.World.Group.Formed &&
                    observer.World.Group.Members.Any(m => m.Id == peerId && m.Formed), "Follower did not form up.");
                visitor.Formation(8);
                Wait(observer, visitor, () => !visitor.World.Group.Formed, "Follower did not leave formation.");
                observer.Formation(6);
                Wait(observer, visitor, () => visitor.World.Group.Formation == "Pipe", "Formation shape change was not published.");
                observer.Formation(9);
                Wait(observer, visitor, () => observer.World.Group.Formation == "" &&
                    visitor.World.Group.Formation == "", "Formation release was not published.");
                observer.SelectTarget(peerId);
                Wait(observer, visitor, () => observer.World.Equipment.TargetId == peerId, "Group leader target did not reach the server.");
                observer.Formation(12);
                Wait(observer, visitor, () => visitor.World.Equipment.TargetId == peerId, "Shared target did not reach the member.");
                Console.WriteLine("PASS: formation selection, form-up, leave, shape change, release and target sharing");
                visitor.Chat("group smoke message", 1);
                Wait(observer, visitor, () => observer.World.Messages.Any(m => m.Contains("group smoke message")), "Group chat was not delivered.");
                observer.Chat("/kick Visitor");
                Wait(observer, visitor, () => !visitor.World.Group.Joined && observer.World.Group.Members.Length == 0, "Kicked member remained grouped.");
                observer.Chat("/invite Visitor");
                Wait(observer, visitor, () => visitor.World.Invitation.Length > 0, "Reinvite did not arrive.");
                visitor.Chat("/accept");
                Wait(observer, visitor, () => visitor.World.Group.Joined, "Rejoin did not arrive.");
                visitor.Chat("/leave");
                Wait(observer, visitor, () => !visitor.World.Group.Joined && observer.World.Group.Members.Length == 0, "Leave did not clear membership.");
                observer.Chat("/invite Visitor");
                Wait(observer, visitor, () => visitor.World.Invitation.Length > 0, "Disband setup invitation did not arrive.");
                visitor.Chat("/accept");
                Wait(observer, visitor, () => observer.World.Group.Members.Length == 1 && visitor.World.Group.Joined, "Disband setup did not join.");
                observer.Chat("/disband");
                Wait(observer, visitor, () => !observer.World.Group.Joined && !visitor.World.Group.Joined, "Disband did not clear both clients.");
                observer.Chat("/d_spawn Ishuan Revenant Young 1");
                Wait(observer, visitor, () => observer.World.Mobs.Values.Any(m => m.HasPosition && m.Name.Contains("Ishuan Revenant Young") && m.Shield >= 0),
                    "Spawned mob identity, pose, and shield did not arrive.");
                ShipActor spawned = observer.World.Mobs.Values.First(m => m.Name.Contains("Ishuan Revenant Young") && m.HasPosition);
                observer.Thrust(2);
                Wait(observer, visitor, () => observer.World.Position.Distance(spawned.Position) < 700,
                    "Pilot did not close to starter-beam range.");
                observer.Thrust(4);
                observer.ToggleWeapons(spawned.Id);
                Wait(observer, visitor, () => observer.World.Equipment.Mounted[3] != null &&
                    (observer.World.Equipment.Mounted[3].WeaponState & 1) != 0, "Weapon toggle was not acknowledged by the server.");
                Wait(observer, visitor, () => observer.World.Shots.Any(shot =>
                    shot.Source == observer.Player && shot.Target == spawned.Id), "No server weapon effect reached the client.");
                Wait(observer, visitor, () => !observer.World.Mobs.ContainsKey(spawned.Id) ||
                    observer.World.Mobs.TryGetValue(spawned.Id, out ShipActor damaged) &&
                    (damaged.Shield < spawned.Shield || damaged.Hull < spawned.Hull), "Target health did not change after firing.");
                observer.Thrust(2);
                Wait(observer, visitor, () => observer.World.Position.Distance(spawned.Position) < 700,
                    "Pilot did not close to starter-beam range.");
                observer.Thrust(4);
                observer.ToggleWeapons(spawned.Id);
                Wait(observer, visitor, () => observer.World.Equipment.Mounted[3] != null &&
                    (observer.World.Equipment.Mounted[3].WeaponState & 1) == 0, "Weapon stop was not acknowledged by the server.");
                if (observer.World.Mobs.ContainsKey(spawned.Id) &&
                    !observer.World.Objects.Values.Any(item => item.Kind == 25))
                    observer.ToggleWeapons(spawned.Id);
                Wait(observer, visitor, () => observer.World.Objects.Values.Any(item => item.Kind == 25),
                    "The defeated MOB did not publish a wreck.");
                MapObject wreck = observer.World.Objects.Values.First(item => item.Kind == 25);
                observer.OpenLoot(wreck.Id);
                Wait(observer, visitor, () => observer.World.OpenLootId == wreck.Id &&
                    observer.World.Loot.ContainsKey(wreck.Id) ||
                    !observer.World.Objects.ContainsKey(wreck.Id) &&
                    observer.World.Messages.Any(message => message.StartsWith("Looted ")),
                    "Wreck selection, permission or contents did not arrive.");
                ItemState[] stock = observer.World.Loot.TryGetValue(wreck.Id, out ItemState[] contents)
                    ? contents : Array.Empty<ItemState>();
                int lootSlot = Array.FindIndex(stock, item => item?.Present == true);
                Require(lootSlot >= 0, "Guaranteed Ishuan Revenant Young drop was missing from the corpse inventory.");
                uint template = stock[lootSlot].Template;
                observer.TakeLoot(wreck.Id, lootSlot);
                Wait(observer, visitor, () => observer.World.Equipment.Cargo.Any(item => item?.Template == template),
                    "Tractor collection did not add the wreck item to cargo.");
                Console.WriteLine("PASS: wreck targeting, open permission, guaranteed item stock and tractor collection");
                Console.WriteLine("PASS: group invitation, decline, accept, roster, group chat, kick, rejoin, leave, disband; server mob spawn/status, weapon effects and damage");
            }
            Wait(observer, null, () => !observer.World.Players.ContainsKey(peerId), "Logged-out player remained visible.");
            using (var visitor = new LocalSession(CancellationToken.None))
            {
                visitor.Connect(host, "peer", "pass");
                Wait(observer, visitor, () => observer.World.Players.TryGetValue(peerId, out ShipActor p) && p.HasPosition,
                    "Reconnected player did not reappear.");
            }
        }
        Console.WriteLine("PASS: two-client visibility, player names, authoritative remote movement, logout removal and reentry");
    }

    /// <summary>Poll both independent clients until the expected server event arrives.</summary>
    private static void Wait(LocalSession observer, LocalSession visitor, Func<bool> condition, string message)
    {
        var clock = Stopwatch.StartNew();
        while (!condition())
        {
            if (clock.Elapsed.TotalSeconds > 12) throw new TimeoutException(message + " Status=" + observer.Status +
                " open=" + observer.World.OpenLootId + " loot=" + string.Join(",", observer.World.Loot.Keys) +
                " Equipment: " + string.Join(",", observer.World.Equipment.Mounted.Select((item,index) => item != null ? $"{index}:{item.Template}:{item.Quantity}" : "null")) + " Catalog=" + observer.World.ItemCatalog.Count + " Resources=" + observer.World.Equipment.Hull + "/" + observer.World.Equipment.MaxHull + " Contacts: " + string.Join("; ", observer.World.Mobs.Values.Select(m => $"{m.Id}:{m.Name} posed={m.HasPosition} hostile={m.Hostile} shield={m.Shield} hull={m.Hull}/{m.MaxHull}")) + " Messages: " + string.Join("; ", observer.World.Messages));
            observer.Poll(); visitor?.Poll();
        }
    }
}
