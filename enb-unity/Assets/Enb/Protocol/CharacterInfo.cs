using System;
using System.IO;
using System.Diagnostics;
using System.Net.Sockets;
using System.Text;
using System.Threading;

namespace Enb.Protocol
{
    /// <summary>A character slot offered by the local account bridge.</summary>
    public sealed class CharacterInfo
    {
        public readonly int Slot;
        public readonly string Name;

        /// <summary>Keep the wire slot beside its displayed character name.</summary>
        public CharacterInfo(int slot, string name)
        { Slot = slot; Name = name; }
    }

    /// <summary>Authenticate and read the available characters without entering one.</summary>
    public sealed class CharacterPickerWorker : IDisposable
    {
        private readonly CancellationTokenSource stop = new CancellationTokenSource();
        private readonly Thread thread;
        private readonly string host, username;
        private string password;
        private TcpClient client;
        private CharacterInfo[] characters;
        private string error;
        private int disposed;

        public CharacterInfo[] Characters => Volatile.Read(ref characters);
        public string Error => Volatile.Read(ref error);
        public bool Running => thread.IsAlive;

        /// <summary>Start the cancellable account lookup away from the Unity thread.</summary>
        public CharacterPickerWorker(string host, string username, string password)
        {
            this.host = host; this.username = username; this.password = password;
            thread = new Thread(Run) { IsBackground = true, Name = "ENB character picker" };
            thread.Start();
        }

        private void Run()
        {
            try
            {
                var address = LocalSession.LocalAddress(host);
                using (var registration = new UdpClient(AddressFamily.InterNetwork))
                {
                    registration.Client.ReceiveTimeout = 100;
                    registration.Connect(address, 3806);
                    byte[] request = Wire.Encode(0x1000, Wire.Body(writer => writer.Write(246u)), 0, 0);
                    registration.Send(request, request.Length);
                    var registrationClock = Stopwatch.StartNew();
                    while (registrationClock.Elapsed.TotalSeconds < 10)
                    {
                        stop.Token.ThrowIfCancellationRequested();
                        try
                        {
                            System.Net.IPEndPoint endpoint = null;
                            byte[] reply = registration.Receive(ref endpoint);
                            if (Wire.Datagram(reply, 0) == 0x1001) break;
                        }
                        catch (SocketException exception) when (exception.SocketErrorCode == SocketError.TimedOut || exception.SocketErrorCode == SocketError.WouldBlock)
                        {
                            continue;
                        }
                    }
                    if (registrationClock.Elapsed.TotalSeconds >= 10)
                        throw new TimeoutException("The VM did not answer UDP registration on port 3806.");
                }
                client = new TcpClient(AddressFamily.InterNetwork);
                IAsyncResult connecting = client.BeginConnect(address, 3807, null, null);
                var connectClock = Stopwatch.StartNew();
                using (WaitHandle handle = connecting.AsyncWaitHandle)
                {
                    while (!handle.WaitOne(50))
                    {
                        stop.Token.ThrowIfCancellationRequested();
                        if (connectClock.Elapsed.TotalSeconds > 10)
                            throw new TimeoutException("The account bridge did not answer at " + host + ":3807.");
                    }
                }
                client.EndConnect(connecting);
                NetworkStream stream = client.GetStream();
                byte[] user = Encoding.UTF8.GetBytes(username), secret = Encoding.UTF8.GetBytes(password);
                if (user.Length == 0 || user.Length > 256 || secret.Length > 1024)
                    throw new ArgumentException("Invalid credential length.");
                byte[] credentials = Wire.Body(writer =>
                { writer.Write((ushort)user.Length); writer.Write(user); writer.Write((ushort)secret.Length); writer.Write(secret); });
                try { Write(stream, 0x2000, credentials); }
                finally { Array.Clear(secret, 0, secret.Length); Array.Clear(credentials, 0, credentials.Length); password = null; }
                Frame auth = ReadFrame();
                if (auth.Opcode != 0x2001) throw new InvalidDataException("Unexpected authentication reply.");
                string ticket = null; bool valid = false;
                foreach (string line in Encoding.ASCII.GetString(auth.Body).Split('\n'))
                {
                    string field = line.Trim();
                    if (field == "Valid=TRUE") valid = true;
                    if (field.StartsWith("Ticket=", StringComparison.Ordinal)) ticket = field.Substring(7);
                }
                if (!valid || string.IsNullOrEmpty(ticket) || ticket.Length > 512)
                    throw new InvalidDataException("Local account authentication rejected.");
                Write(stream, 0x2002, Encoding.ASCII.GetBytes(ticket));
                Frame response = ReadFrame();
                if (response.Opcode != 0x70 || response.Body.Length < 1874)
                    throw new InvalidDataException("Character list rejected.");
                uint count = Wire.U32(response.Body, 8, true);
                if (count > 5 || 133 + count * 374 > response.Body.Length)
                    throw new InvalidDataException("Invalid character list.");
                var found = new System.Collections.Generic.List<CharacterInfo>();
                for (int slot = 0; slot < count; slot++)
                {
                    string name = Wire.Text(response.Body, 133 + slot * 374, 20);
                    if (name.Length > 0) found.Add(new CharacterInfo(slot, name));
                }
                if (found.Count == 0) throw new InvalidOperationException("This account has no characters.");
                Volatile.Write(ref characters, found.ToArray());
            }
            catch (OperationCanceledException) { }
            catch (Exception exception)
            {
                string message = exception is System.Net.Sockets.SocketException socketError
                    ? "Cannot reach " + host + ":3807 (" + socketError.SocketErrorCode + ")."
                    : exception.Message;
                Volatile.Write(ref error, message);
            }
            finally { password = null; client?.Close(); }
        }

        private static void Write(NetworkStream stream, ushort opcode, byte[] body)
        { byte[] packet = Wire.Encode(opcode, body); stream.Write(packet, 0, packet.Length); }

        private Frame ReadFrame()
        {
            byte[] header = ReadExact(4); int size = Wire.U16(header, 0);
            if (size < 4 || size > 32767) throw new InvalidDataException("Invalid TCP frame size.");
            return new Frame(Wire.U16(header, 2), ReadExact(size - 4));
        }

        private byte[] ReadExact(int size)
        {
            byte[] result = new byte[size]; int offset = 0;
            var clock = Stopwatch.StartNew();
            while (offset < size)
            {
                stop.Token.ThrowIfCancellationRequested();
                if (clock.Elapsed.TotalSeconds > 15) throw new TimeoutException("The local server stopped replying.");
                if (!client.Client.Poll(100000, SelectMode.SelectRead)) continue;
                int count = client.Client.Receive(result, offset, size - offset, SocketFlags.None);
                if (count == 0) throw new EndOfStreamException("Local bridge closed the connection.");
                offset += count;
            }
            return result;
        }

        /// <summary>Cancel lookup and wait for its socket-owning thread to finish.</summary>
        public void Dispose()
        {
            if (Interlocked.Exchange(ref disposed, 1) != 0) return;
            stop.Cancel(); client?.Close();
            if (Thread.CurrentThread != thread) thread.Join();
            stop.Dispose();
        }
    }
}
