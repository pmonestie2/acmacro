using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;

namespace Enb.Protocol
{
    public sealed class Frame
    {
        public readonly ushort Opcode;
        public readonly byte[] Body;

        /// <summary>Store an owned packet payload.</summary>
        public Frame(ushort opcode, byte[] body) { Opcode = opcode; Body = body; }
    }

    public static class Wire
    {
        /// <summary>Reject a truncated field before decoding it.</summary>
        public static void Require(byte[] bytes, int offset, int count)
        {
            if (offset < 0 || count < 0 || offset > bytes.Length - count)
                throw new InvalidDataException("Truncated protocol field.");
        }

        /// <returns>The unsigned little-endian 16-bit value.</returns>
        public static ushort U16(byte[] b, int i)
        { Require(b, i, 2); return (ushort)(b[i] | b[i + 1] << 8); }

        /// <returns>The unsigned 32-bit value in the requested byte order.</returns>
        public static uint U32(byte[] b, int i, bool big = false)
        {
            Require(b, i, 4);
            return big ? (uint)(b[i] << 24 | b[i + 1] << 16 | b[i + 2] << 8 | b[i + 3])
                : (uint)(b[i] | b[i + 1] << 8 | b[i + 2] << 16 | b[i + 3] << 24);
        }

        /// <returns>A finite little-endian floating-point value.</returns>
        public static float Float(byte[] b, int i)
        {
            byte[] field = Slice(b, i, 4);
            if (!BitConverter.IsLittleEndian) Array.Reverse(field);
            float value = BitConverter.ToSingle(field, 0);
            if (float.IsNaN(value) || float.IsInfinity(value))
                throw new InvalidDataException("Nonfinite world coordinate.");
            return value;
        }

        /// <returns>An owned, bounds-checked copy of the requested bytes.</returns>
        public static byte[] Slice(byte[] b, int i, int count)
        { Require(b, i, count); byte[] result = new byte[count]; Buffer.BlockCopy(b, i, result, 0, count); return result; }

        /// <returns>ASCII text up to the first NUL within the field.</returns>
        public static string Text(byte[] b, int i, int count)
        { Require(b, i, count); int end = i; while (end < i + count && b[end] != 0) end++; return Encoding.ASCII.GetString(b, i, end - i); }

        /// <returns>A body encoded by an explicit packet writer.</returns>
        public static byte[] Body(Action<BinaryWriter> write)
        {
            using (var stream = new MemoryStream())
            using (var writer = new BinaryWriter(stream)) { write(writer); return stream.ToArray(); }
        }

        /// <summary>Write a network-order integer.</summary>
        public static void Big(BinaryWriter writer, uint value)
        { writer.Write(new[] { (byte)(value >> 24), (byte)(value >> 16), (byte)(value >> 8), (byte)value }); }

        /// <returns>A bounded TCP frame or UDP datagram.</returns>
        public static byte[] Encode(ushort opcode, byte[] body, uint? player = null, uint sequence = 0)
        {
            int size = body.Length + (player.HasValue ? 12 : 4);
            if (size > (player.HasValue ? 65507 : 32767)) throw new InvalidDataException("Oversized packet.");
            return Body(w => { w.Write((ushort)size); w.Write(opcode); if (player.HasValue) { w.Write(player.Value); w.Write(sequence); } w.Write(body); });
        }

        /// <returns>The validated opcode of a datagram belonging to the specified player.</returns>
        public static ushort Datagram(byte[] packet, uint player)
        {
            Require(packet, 0, 12);
            if (U16(packet, 0) != packet.Length || U32(packet, 4) != player)
                throw new InvalidDataException("Datagram length or player identity mismatch.");
            return U16(packet, 2);
        }
    }

    public sealed class SectorStream
    {
        private readonly uint player;
        private readonly SortedDictionary<uint, byte[]> waiting = new SortedDictionary<uint, byte[]>();
        private readonly List<byte> pending = new List<byte>();
        public uint Expected { get; private set; }
        public uint MissingCount => waiting.Count == 0 ? 0 : waiting.First().Key - Expected;

        /// <summary>Bind reassembly to one selected character.</summary>
        public SectorStream(uint playerId) { player = playerId; }

        /// <returns>Complete frames in sequence order, retaining bounded partial frames.</returns>
        public List<Frame> Feed(byte[] packet)
        {
            ushort opcode = Wire.Datagram(packet, player);
            if (opcode != 0x2016 && opcode != 0x201a) throw new InvalidDataException("Unexpected sector envelope.");
            uint sequence = Wire.U32(packet, 8);
            var frames = new List<Frame>();
            if (sequence < Expected) return frames;
            if (sequence - Expected > 255) throw new InvalidDataException("Sequence window exceeded.");
            byte[] payload = Wire.Slice(packet, 12, packet.Length - 12);
            if (waiting.TryGetValue(sequence, out byte[] previous) && !previous.SequenceEqual(payload))
                throw new InvalidDataException("Conflicting duplicate datagram.");
            waiting[sequence] = payload;
            while (waiting.TryGetValue(Expected, out byte[] next))
            {
                waiting.Remove(Expected++);
                pending.AddRange(next);
                int offset = 0;
                while (pending.Count - offset >= 4)
                {
                    int size = pending[offset] | pending[offset + 1] << 8;
                    if (size < 4 || size > 32767) throw new InvalidDataException("Invalid reassembled frame size.");
                    if (pending.Count - offset < size) break;
                    ushort inner = (ushort)(pending[offset + 2] | pending[offset + 3] << 8);
                    frames.Add(new Frame(inner, pending.GetRange(offset + 4, size - 4).ToArray()));
                    offset += size;
                }
                pending.RemoveRange(0, offset);
                if (pending.Count > 32767) throw new InvalidDataException("Partial frame exceeds limit.");
            }
            return frames;
        }
    }
}
