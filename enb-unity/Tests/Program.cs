using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading;
using Enb.Protocol;

internal static class Program
{
    /// <summary>Run pure protocol checks, with explicit opt-in to a disposable local server.</summary>
    private static void Main(string[] args)
    {
        CheckReassembly(); CheckWorld(); CheckAddress(); PeerChecks.Decode();
        Console.WriteLine("PASS: framing, ordering, fragmentation, duplicates, bounds, world decoding, private addresses");
        if (args.Length == 2 && args[0] == "--server") { PeerChecks.Live(args[1]); CheckServer(args[1]); }
        else if (args.Length != 0) throw new ArgumentException("Usage: ProtocolChecks [--server PRIVATE_IP]");
    }

    /// <summary>Fail with a useful behavior description.</summary>
    private static void Assert(bool condition, string message)
    { if (!condition) throw new Exception(message); }

    /// <summary>Require malformed input to be rejected.</summary>
    private static void Reject(Action operation)
    {
        try { operation(); }
        catch (InvalidDataException) { return; }
        throw new Exception("Expected invalid packet rejection.");
    }

    /// <summary>Exercise reordering, fragmented frames, bounded gaps, and duplicate integrity.</summary>
    private static void CheckReassembly()
    {
        byte[] frame = Wire.Encode(0x2020, new byte[] { 1, 2, 3, 4 });
        var stream = new SectorStream(123);
        byte[] later = Wire.Encode(0x201a, Wire.Slice(frame, 3, frame.Length - 3), 123, 1);
        Assert(stream.Feed(later).Count == 0 && stream.MissingCount == 1, "Gap was not retained.");
        Assert(stream.Feed(later).Count == 0, "Identical duplicate produced a frame.");
        byte[] conflict = (byte[])later.Clone(); conflict[12] ^= 1;
        Reject(() => stream.Feed(conflict));
        var frames = stream.Feed(Wire.Encode(0x2016, Wire.Slice(frame, 0, 3), 123, 0));
        Assert(frames.Count == 1 && frames[0].Opcode == 0x2020 && frames[0].Body.SequenceEqual(new byte[] { 1, 2, 3, 4 }), "Fragment reassembly failed.");
        Assert(stream.Expected == 2 && stream.Feed(later).Count == 0, "Duplicate delivery advanced the stream.");
        Reject(() => stream.Feed(Wire.Encode(0x2016, frame, 456, 2)));
        Reject(() => stream.Feed(Wire.Encode(0x2016, frame, 123, 258)));
        Reject(() => new SectorStream(1).Feed(Wire.Encode(0x2016, new byte[] { 3, 0, 5, 0 }, 1)));
        Reject(() => Wire.Datagram(new byte[4], 0));
        Reject(() => Wire.Float(BitConverter.GetBytes(float.NaN), 0));
    }

    /// <summary>Check map bounds, movement ownership, and immutable snapshot separation.</summary>
    private static void CheckWorld()
    {
        byte[] body = new byte[64]; body[0] = 77; body[4] = 11; body[57] = 0x10; body[58] = 4;
        Array.Copy(BitConverter.GetBytes(2f), 0, body, 7, 4);
        Array.Copy(BitConverter.GetBytes(1f), 0, body, 49, 4);
        Array.Copy(System.Text.Encoding.ASCII.GetBytes("Gate"), 0, body, 60, 4);
        var world = new World(); world.Apply(new Frame(0x2018, body), 1);
        var snapshot = new Snapshot(world, "test", true);
        Assert(snapshot.Objects[0].Scale == 2 && snapshot.Objects[0].Orientation[3] == 1, "Object transform was not decoded.");
        world.Apply(new Frame(7, new byte[] { 77, 0, 0, 0 }), 1);
        Assert(snapshot.Objects.Length == 1 && snapshot.Objects[0].Kind == 11 && world.Objects.Count == 0, "Snapshot retained mutable world storage.");
        body[58] = 5; Reject(() => new MapObject(body));
        byte[] movement = new byte[38]; movement[2] = 2;
        world.Apply(new Frame(0x3e, movement), 1);
        Assert(!world.HasPosition, "Accepted a different player's movement.");
        movement[2] = 1; world.Apply(new Frame(0x3e, movement), 1);
        Assert(world.HasPosition, "Ignored matching player movement.");
    }

    /// <summary>Ensure development credentials cannot be submitted to public endpoints.</summary>
    private static void CheckAddress()
    {
        foreach (string address in new[] { "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.47.128" }) LocalSession.LocalAddress(address);
        foreach (string address in new[] { "8.8.8.8", "0.0.0.0", "172.32.0.1", "example.com", "::1" })
        {
            bool rejected = false;
            try { LocalSession.LocalAddress(address); } catch (ArgumentException) { rejected = true; }
            Assert(rejected, "Accepted a non-private IPv4 endpoint.");
        }
    }

    /// <summary>Wait for a concrete server state with a monotonic deadline.</summary>
    private static void Wait(LocalSession session, Func<bool> predicate, double seconds, string reason)
    {
        var timer = Stopwatch.StartNew();
        while (!predicate()) { if (timer.Elapsed.TotalSeconds > seconds) throw new TimeoutException(reason); session.Poll(); }
    }

    /// <summary>Verify actual local login, motion, warp, gate handoff, logout, and worker cancellation.</summary>
    private static void CheckServer(string host)
    {
        using (var session = new LocalSession(CancellationToken.None))
        {
            session.Connect(host, "local", "local");
            Wait(session, () => session.Ready && session.World.HasPosition && session.World.Objects.Values.Any(x => x.Kind == 11), 15, "No initial map.");
            Console.WriteLine("PASS: local bridge login, character selection, UDP entry, map, position");
            Position initial = session.World.Position;
            session.Thrust(2);
            Wait(session, () => session.World.Position.Distance(initial) > 20, 5, "Thrust did not move ship.");
            session.Thrust(4);
            float previousHeading = session.World.Orientation[2];
            session.Steer(.5f, 0);
            Wait(session, () => Math.Abs(session.World.Orientation[2] - previousHeading) > .02, 5, "Steering did not rotate ship.");
            session.Steer(0, 0);
            MapObject gate = session.World.Objects.Values.First(x => x.Kind == 11);
            session.Warp(gate.Id);
            Wait(session, () => session.World.Position.Distance(gate.Position) < 1800 && Math.Abs(session.World.Speed) < 1, 40, "Warp did not approach gate.");
            Console.WriteLine("PASS: server-authoritative impulse, steering, warp");
            session.Gate(gate.Id);
            Wait(session, () => session.Status == "Receiving sector", 45, "Gate handoff did not occur.");
            Wait(session, () => session.Ready && session.World.HasPosition && session.World.Objects.Count > 0, 15, "Destination map did not arrive.");
            Console.WriteLine("PASS: correlated gate activation, completion, destination handoff");
        }
        using (var worker = new ClientWorker(host, "local", "local"))
        {
            var timer = Stopwatch.StartNew();
            while (worker.Latest == null || !worker.Latest.Ready)
            {
                if (worker.Error != null) throw new Exception(worker.Error);
                if (timer.Elapsed.TotalSeconds > 15) throw new TimeoutException("Re-login after logout failed.");
                Thread.Sleep(20);
            }
            worker.Submit(new ClientCommand(CommandKind.Thrust, 4));
            timer.Restart(); worker.Dispose();
            Assert(!worker.Running && timer.Elapsed.TotalSeconds < 3, "Worker cancellation did not join promptly.");
        }
        Console.WriteLine("PASS: explicit logout, re-login, cancellation and worker join");
        using (var cancelled = new ClientWorker(host, "local", "local"))
        {
            var timer = Stopwatch.StartNew(); cancelled.Dispose();
            Assert(!cancelled.Running && timer.Elapsed.TotalSeconds < 3, "Cancellation during connection did not join promptly.");
        }
        Console.WriteLine("PASS: cancellation during connection");
    }
}
