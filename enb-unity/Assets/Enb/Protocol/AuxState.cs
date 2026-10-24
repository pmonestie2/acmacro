using System;
using System.Collections.Generic;
using System.IO;

namespace Enb.Protocol
{
    public sealed class GroupMember
    {
        public readonly uint Id;
        public readonly string Name;
        public readonly bool Formed;
        /// <summary>Store one authoritative group member and formation status.</summary>
        public GroupMember(uint id, string name, bool formed = false)
        { Id = id; Name = name; Formed = formed; }
    }

    public sealed class GroupState
    {
        public readonly bool IsLeader, Formed;
        public readonly string Formation;
        public readonly GroupMember[] Members;
        public bool Joined => IsLeader || Members.Length > 0;
        /// <summary>Store the local role and other members of a server group.</summary>
        public GroupState(bool isLeader, GroupMember[] members, string formation = "", bool formed = false)
        { IsLeader = isLeader; Members = members; Formation = formation; Formed = formed; }
    }

    /// <summary>One native cast progress revision for the character.</summary>
    public sealed class AbilityProgress
    {
        public readonly uint Id, DurationMs;
        /// <summary>Keep the advertised ability and remaining cast duration together.</summary>
        public AbilityProgress(uint id, uint durationMs) { Id = id; DurationMs = durationMs; }
    }

    /// <summary>One server-owned skill cooldown revision.</summary>
    public sealed class SkillCooldown
    {
        public readonly uint SkillId, DurationMs;
        /// <summary>Keep the skill identity with its recovery duration.</summary>
        public SkillCooldown(uint skillId, uint durationMs)
        { SkillId = skillId; DurationMs = durationMs; }
    }

    /// <summary>Read the supported prefixes of native AUX records without guessing unknown layouts.</summary>
    internal sealed class AuxState
    {
        private readonly byte[] body;
        private int offset = 7;
        private sealed class Flags
        {
            public readonly bool[] Present, Default;
            /// <summary>Allocate a bounded native record mask.</summary>
            public Flags(int count) { Present = new bool[count]; Default = new bool[count]; }
        }
        /// <summary>Validate the envelope before reading nested fields.</summary>
        private AuxState(byte[] body)
        {
            this.body = body;
            if (Wire.U16(body, 4) != body.Length - 6 || body[6] != 1)
                throw new InvalidDataException("Invalid AUX envelope.");
        }
        /// <returns>The next native field-presence mask.</returns>
        private Flags Mask(int count)
        {
            Wire.Require(body, offset, 1);
            var flags = new Flags(count);
            if (body[offset] == 5) { offset++; return flags; }
            int control = body[offset] & 15;
            if (control != 2 && control != 6) throw new InvalidDataException("Invalid AUX flags for " + count + " fields at byte " + offset + " of " + body.Length + " (value " + body[offset] + ").");
            int length = (4 + count * (control == 6 ? 2 : 1) + 7) / 8;
            Wire.Require(body, offset, length);
            for (int i = 0; i < count; i++)
            {
                int bit = 4 + i;
                flags.Present[i] = (body[offset + bit / 8] & (1 << (bit % 8))) != 0;
                bit += count;
                flags.Default[i] = control == 6 && (body[offset + bit / 8] & (1 << (bit % 8))) != 0;
            }
            offset += length; return flags;
        }
        /// <returns>The next length-prefixed UTF-8 value.</returns>
        private string Text()
        {
            int length = Wire.U16(body, offset); offset += 2;
            string value = Wire.Text(body, offset, length); offset += length; return value;
        }
        /// <returns>The next little-endian integer.</returns>
        private uint UInt() { uint value = Wire.U32(body, offset); offset += 4; return value; }
        /// <returns>The next finite resource value.</returns>
        private float Float() { float value = Wire.Float(body, offset); offset += 4; return value; }
        /// <returns>The next byte.</returns>
        private byte Byte() { Wire.Require(body, offset, 1); return body[offset++]; }
        /// <returns>The current fraction from a three-field resource record.</returns>
        private float Percent()
        {
            Flags flags = Mask(3); float result = -1;
            for (int i = 0; i < 3; i++) if (flags.Present[i]) { float value = Float(); if (i == 2) result = value; }
            return result;
        }
        /// <returns>An updated contact with supported name, shield and hull fields.</returns>
        public static ShipActor ReadShip(byte[] body, ShipActor ship)
        {
            var reader = new AuxState(body); Flags flags = reader.Mask(58);
            string name = ship.Name; float shield = ship.Shield, hull = ship.Hull, maxHull = ship.MaxHull;
            for (int i = 0; i <= 9; i++)
            {
                if ((i == 4 || i == 6) && (flags.Present[i] || flags.Default[i]))
                { float value = reader.Percent(); if (i == 6 && value >= 0) shield = value; continue; }
                if (!flags.Present[i]) continue;
                if (i == 0) { string value = reader.Text(); if (value.Length > 0) name = value; }
                else if (i >= 5)
                { float value = reader.Float(); if (i == 8) hull = value; if (i == 9) maxHull = value; }
                else return ship; // Fields 1–3 are not emitted by this server; never infer their width.
            }
            return new ShipActor(ship.Id, ship.Asset, ship.Scale, name, ship.Position, ship.Orientation,
                ship.HasPosition, ship.IsPlayer, ship.Hostile, shield, hull, maxHull);
        }
        /// <summary>Read owned ship resources and inventory without interpreting unrelated AUX fields.</summary>
        public static void ReadOwn(byte[] body, EquipmentState equipment)
        {
            var reader = new AuxState(body); Flags fields = reader.Mask(58);
            for (int i = 0; i <= 36; i++)
            {
                if (!fields.Present[i] && fields.Default[i] && (i == 4 || i == 6 || i == 35 || i == 36))
                { if (reader.Byte() != 5) throw new InvalidDataException("Invalid nested AUX reset."); continue; }
                if (!fields.Present[i]) continue;
                switch (i)
                {
                    case 0: case 1: reader.Text(); break;
                    case 4: equipment.Energy = reader.Percent(); break;
                    case 5: equipment.MaxEnergy = reader.Float(); break;
                    case 6: equipment.Shield = reader.Percent(); break;
                    case 7: equipment.MaxShield = reader.Float(); break;
                    case 8: equipment.Hull = reader.Float(); break;
                    case 9: equipment.MaxHull = reader.Float(); break;
                    case 10: case 11: case 12: case 13: case 14: case 15: reader.Float(); break;
                    case 16: case 17: case 18: case 19: case 20: case 21: reader.Byte(); break;
                    case 27: equipment.TargetId = reader.UInt(); break;
                    case 26: case 31: case 32: case 33: case 34: reader.UInt(); break;
                    case 36: reader.Inventory(equipment); break;
                    default: return;
                }
            }
        }
        /// <summary>Read ship inventory updates in native field order.</summary>
        private void Inventory(EquipmentState equipment)
        {
            Flags fields = Mask(11);
            for (int i = 0; i < 11; i++)
            {
                if (!fields.Present[i] && fields.Default[i] && (i == 2 || i == 3 || i >= 6))
                { if (Byte() != 5) throw new InvalidDataException("Invalid inventory reset."); continue; }
                if (!fields.Present[i]) continue;
                switch (i)
                {
                    case 0: UInt(); break;
                    case 1: Text(); break;
                    case 2: MountLayout(); break;
                    case 4: equipment.WeaponMounts = UInt(); break;
                    case 5: equipment.DeviceMounts = UInt(); break;
                    case 6: ReadSlots(equipment.Cargo, 10); break;
                    case 7: ReadSlots(equipment.Mounted, 14); break;
                    case 8: ReadSlots(equipment.Ammo, 10); break;
                    case 10: SkipSlots(6, 10); break;
                    default: return;
                }
            }
        }
        /// <summary>Skip mount masks while preserving inventory packet alignment.</summary>
        private void MountLayout()
        {
            Flags fields = Mask(20);
            for (int i = 0; i < 20; i++) if (fields.Present[i]) { UInt(); UInt(); UInt(); }
        }
        /// <summary>Apply individual cargo or equipment slot updates.</summary>
        private void ReadSlots(ItemState[] slots, int fieldsPerItem)
        {
            Flags flags = Mask(slots.Length);
            for (int i = 0; i < slots.Length; i++)
            {
                if (flags.Present[i]) slots[i] = ReadItem(fieldsPerItem, slots[i]);
                else if (flags.Default[i]) { if (Byte() != 5) throw new InvalidDataException("Invalid item reset."); slots[i] = null; }
            }
        }
        /// <summary>Consume unused native slot collections.</summary>
        private void SkipSlots(int count, int fieldsPerItem)
        {
            Flags flags = Mask(count);
            for (int i = 0; i < count; i++)
            {
                if (flags.Present[i]) ReadItem(fieldsPerItem, null);
                else if (flags.Default[i] && Byte() != 5) throw new InvalidDataException("Invalid item reset.");
            }
        }
        /// <returns>An updated item record, retaining unchanged weapon status fields.</returns>
        private ItemState ReadItem(int count, ItemState previous)
        {
            Flags flags = Mask(count); ItemState item = previous?.Copy() ?? new ItemState();
            for (int i = 0; i < count; i++)
            {
                if (!flags.Present[i] && flags.Default[i] && i == 13)
                { if (Byte() != 5) throw new InvalidDataException("Invalid equipment reset."); continue; }
                if (!flags.Present[i]) continue;
                switch (i)
                {
                    case 0: item.Template = UInt(); break;
                    case 1: item.Quantity = UInt(); break;
                    case 2: item.Price = UInt() | ((ulong)UInt() << 32); break;
                    case 4: item.Structure = Float(); break;
                    case 5: item.Quality = Float(); break;
                    case 6: item.Info = Text(); break;
                    case 9: Text(); break;
                    case 10: UInt(); break;
                    case 11: item.Range = Float(); break;
                    case 12: item.WeaponState = UInt(); break;
                    default: return item;
                }
            }
            return item;
        }

        /// <returns>Authoritative corpse slots, or null for a name-only corpse update.</returns>
        public static ItemState[] ReadCorpse(byte[] body)
        {
            if (body.Length < 10 || body[7] != 0x16 || body[8] != 0xec || body[9] != 0x0f)
                return null;
            var reader = new AuxState(body);
            reader.offset = 10; reader.Text();
            for (int i = 0; i < 4; i++) if (reader.Byte() != 5)
                throw new InvalidDataException("Invalid corpse inventory reset.");
            reader.SkipSlots(20, 10);
            var contents = new ItemState[40];
            reader.ReadSlots(contents, 10);
            return contents;
        }

        /// <returns>The server-owned credit balance when this global AUX updates it.</returns>
        public static ulong? ReadCredits(byte[] body)
        {
            var reader = new AuxState(body); Flags fields = reader.Mask(18);
            if (!fields.Present[0]) return null;
            return reader.UInt() | ((ulong)reader.UInt() << 32);
        }

        /// <returns>uint[]: Ability IDs advertised for this character, or null for another AUX update.</returns>
        public static uint[] ReadAvailableAbilities(byte[] body)
        {
            var reader = new AuxState(body); Flags outer = reader.Mask(18);
            if (!outer.Present[6]) return null;
            for (int field = 0; field < 6; field++)
            {
                if (outer.Present[field])
                {
                    if (field == 0) { reader.UInt(); reader.UInt(); }
                    else if (field == 2) reader.SkipSlots(96, 10);
                    else return null;
                }
                else if (outer.Default[field] && field >= 2 && reader.Byte() != 5)
                    throw new InvalidDataException("Invalid global AUX reset before skills.");
            }
            Flags rpg = reader.Mask(16);
            if (!rpg.Present[3]) return null;
            if (rpg.Present[0]) reader.UInt();
            if (rpg.Present[1]) reader.UInt();
            if (rpg.Present[2]) reader.SkipSkillSlots();
            else if (rpg.Default[2] && reader.Byte() != 5)
                throw new InvalidDataException("Invalid skill reset.");
            Flags slots = reader.Mask(170);
            var available = new List<uint>();
            for (uint id = 0; id < 170; id++)
            {
                if (slots.Present[id])
                {
                    Flags ability = reader.Mask(7);
                    for (int field = 0; field < 7; field++)
                    {
                        if (!ability.Present[field]) continue;
                        if (field == 0) { if (reader.Byte() != 0) available.Add(id); }
                        else if (field == 3 || field == 4) reader.UInt();
                        else throw new InvalidDataException("Unknown advertised ability field.");
                    }
                }
                else if (slots.Default[id] && reader.Byte() != 5)
                    throw new InvalidDataException("Invalid ability reset.");
            }
            return available.ToArray();
        }

        /// <summary>Consume the server's skill ranks before its ability list.</summary>
        private void SkipSkillSlots()
        {
            Flags slots = Mask(64);
            for (int skill = 0; skill < 64; skill++)
            {
                if (slots.Present[skill])
                {
                    Flags fields = Mask(7);
                    for (int field = 0; field < 7; field++)
                    {
                        if (!fields.Present[field]) continue;
                        if (field == 3) { UInt(); UInt(); UInt(); UInt(); }
                        else UInt();
                    }
                }
                else if (slots.Default[skill] && Byte() != 5)
                    throw new InvalidDataException("Invalid skill slot reset.");
            }
        }

        /// <returns>AbilityProgress: Native cast progress, or null for another AUX update.</returns>
        public static AbilityProgress ReadAbilityProgress(byte[] body)
        {
            var reader = new AuxState(body); Flags outer = reader.Mask(18);
            if (!outer.Present[6]) return null;
            for (int i = 0; i < 6; i++) if (outer.Present[i]) return null;
            Flags rpg = reader.Mask(16);
            if (!rpg.Present[13] || !rpg.Present[14] || !rpg.Present[15]) return null;
            for (int i = 0; i < 13; i++) if (rpg.Present[i]) return null;
            uint start = reader.UInt(), finish = reader.UInt(), id = reader.UInt();
            uint duration = unchecked(finish - start);
            if (duration > 120000) throw new InvalidDataException("Invalid ability charge duration.");
            return new AbilityProgress(id, duration);
        }

        /// <returns>SkillCooldown[]: Native recovery updates, or null for another AUX update.</returns>
        public static SkillCooldown[] ReadSkillCooldowns(byte[] body)
        {
            var reader = new AuxState(body); Flags outer = reader.Mask(18);
            if (!outer.Present[6]) return null;
            for (int i = 0; i < 6; i++) if (outer.Present[i]) return null;
            Flags rpg = reader.Mask(16);
            if (!rpg.Present[2]) return null;
            if (rpg.Present[0] || rpg.Present[1]) return null;
            Flags slots = reader.Mask(64);
            var cooldowns = new List<SkillCooldown>();
            for (uint skill = 0; skill < 64; skill++)
            {
                if (!slots.Present[skill]) continue;
                Flags fields = reader.Mask(7);
                uint duration = 0;
                for (int field = 0; field < 7; field++)
                {
                    if (!fields.Present[field]) continue;
                    if (field == 1) duration = reader.UInt();
                    else if (field == 2) reader.UInt();
                    else throw new InvalidDataException("Unknown cooldown field.");
                }
                if (duration > 120000) throw new InvalidDataException("Invalid skill cooldown.");
                cooldowns.Add(new SkillCooldown(skill, duration));
            }
            return cooldowns.ToArray();
        }

        /// <returns>A complete vendor stock update, or null for another global AUX.</returns>
        public static ItemState[] ReadVendor(byte[] body)
        {
            var reader = new AuxState(body); Flags fields = reader.Mask(18);
            if (!fields.Present[3]) return null;
            for (int i = 0; i < 3; i++) if (fields.Present[i] || fields.Default[i]) return null;
            var stock = new ItemState[128];
            reader.ReadSlots(stock, 10);
            return stock;
        }

        /// <returns>The resource contents and level after one native AUX revision.</returns>
        public static ResourceState ReadResource(byte[] body, uint id, ResourceState previous)
        {
            var reader = new AuxState(body); Flags fields = reader.Mask(4);
            string name = previous?.Name ?? "";
            uint level = previous?.Level ?? 0;
            float fraction = previous?.Fraction ?? 1;
            ItemState[] items = previous == null ? new ItemState[40] : (ItemState[])previous.Items.Clone();
            if (fields.Present[0]) name = reader.Text();
            if (fields.Present[1]) reader.ReadSlots(items, 10);
            else if (fields.Default[1])
            {
                if (reader.Byte() != 5) throw new InvalidDataException("Invalid resource inventory reset.");
                items = new ItemState[40];
            }
            if (fields.Present[2]) fraction = reader.Float();
            if (fields.Present[3]) level = reader.UInt();
            if (fraction < 0 || fraction > 1 || level > 10)
                throw new InvalidDataException("Invalid resource level or depletion.");
            return new ResourceState(id, name, level, fraction, items);
        }

        /// <returns>The server's standalone group update, or null for unrelated player AUX.</returns>
        public static GroupState ReadGroup(byte[] body)
        {
            var reader = new AuxState(body); Flags outer = reader.Mask(18);
            for (int i = 0; i < 17; i++) if (outer.Present[i] || outer.Default[i]) return null;
            if (!outer.Present[17]) return null;
            Flags info = reader.Mask(11); bool leader = false, formed = false; string formation = "";
            var members = new List<GroupMember>();
            for (int i = 0; i < 10; i++)
            {
                if (!info.Present[i]) continue;
                if (i <= 6) { byte value = reader.Byte(); if (i == 0) leader = value != 0; }
                else if (i == 7) formation = reader.Text();
                else { uint value = reader.UInt(); if (i == 8) formed = value != 0; }
            }
            if (info.Present[10])
            {
                Flags slots = reader.Mask(5);
                for (int i = 0; i < 5; i++)
                {
                    if (!slots.Present[i] && !slots.Default[i]) continue;
                    Flags member = reader.Mask(4); string name = ""; uint id = 0, memberFormation = 0;
                    if (member.Present[0]) name = reader.Text();
                    if (member.Present[1]) id = reader.UInt();
                    if (member.Present[2]) memberFormation = reader.UInt();
                    if (member.Present[3]) reader.UInt();
                    if (id != 0) members.Add(new GroupMember(id, name, memberFormation != 0));
                }
            }
            return new GroupState(leader, members.ToArray(), formation, formed);
        }
    }
}
