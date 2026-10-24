using System;
using System.Collections.Generic;
using System.IO;

namespace Enb.Protocol
{
    /// <summary>Server-owned item template information used by the inventory display.</summary>
    public sealed class ItemTemplateInfo
    {
        public readonly uint Id, Type;
        public readonly string Name;
        /// <summary>Store a bounded item name and its server item type.</summary>
        public ItemTemplateInfo(uint id, uint type, string name)
        { Id = id; Type = type; Name = name; }

        /// <returns>The native item template's ID, type and display name.</returns>
        public static ItemTemplateInfo Decode(byte[] body)
        {
            Wire.Require(body, 0, 17);
            uint id = Wire.U32(body, 0, true);
            uint type = Wire.U32(body, 12);
            int count = body[16], offset = 17;
            if (count > 38) throw new InvalidDataException("Item template has too many fields.");
            string name = "";
            for (int i = 0; i < count; i++)
            {
                uint field = Wire.U32(body, offset); offset += 4;
                if (field == 1 || field == 11 || field == 13)
                {
                    int length = Wire.U16(body, offset); offset += 2;
                    string value = Wire.Text(body, offset, length); offset += length;
                    if (field == 1) name = value;
                }
                else { Wire.Require(body, offset, 4); offset += 4; }
            }
            for (int block = 0; block < 2; block++)
            {
                int effects = checked((int)Wire.U32(body, offset, true)); offset += 4;
                if (effects > 64) throw new InvalidDataException("Item effect limit exceeded.");
                for (int i = 0; i < effects; i++)
                {
                    for (int part = 0; part < 3; part++)
                    { int length = Wire.U16(body, offset); offset += 2; Wire.Require(body, offset, length); offset += length; }
                    Wire.Require(body, offset, 4); offset += 4;
                    int values = checked((int)Wire.U32(body, offset, true)); offset += 4;
                    if (values > 16) throw new InvalidDataException("Item effect values exceed limit.");
                    Wire.Require(body, offset, values * 4 + 8); offset += values * 4 + 8;
                }
                if (effects > 0) { Wire.Require(body, offset, 16); offset += 16; }
            }
            Wire.Require(body, offset, 22); offset += 22;
            int nameLength = Wire.U16(body, offset); offset += 2;
            string fullName = Wire.Text(body, offset, nameLength);
            return new ItemTemplateInfo(id, type, fullName.Length > 0 ? fullName : name);
        }
    }

    /// <summary>A single mutable network-worker item record.</summary>
    public sealed class ItemState
    {
        public uint Template = uint.MaxValue, Quantity, WeaponState;
        public ulong Price;
        public float Quality, Structure, Range;
        public string Info = "";
        public bool Present => Template != uint.MaxValue && Template != uint.MaxValue - 1;
        /// <returns>A copy for a new server update.</returns>
        public ItemState Copy() => (ItemState)MemberwiseClone();
    }

    /// <summary>Worker-owned cargo, mount and resource values from authoritative AUX packets.</summary>
    public sealed class EquipmentState
    {
        public ItemState[] Cargo = new ItemState[40], Mounted = new ItemState[20], Ammo = new ItemState[20];
        public float Energy = -1, Shield = -1, Hull = -1, MaxEnergy = -1, MaxShield = -1, MaxHull = -1;
        public uint WeaponMounts, DeviceMounts, TargetId;
        public bool Firing;
    }

    /// <summary>Stable inventory row copied for the Unity thread.</summary>
    public sealed class ItemView
    {
        public readonly int Slot;
        public readonly uint Template, Quantity, Type, WeaponState;
        public readonly ulong Price;
        public readonly float Quality, Structure, Range;
        public readonly string Name, Info;
        /// <summary>Resolve a worker item against the known server catalog.</summary>
        public ItemView(int slot, ItemState item, IReadOnlyDictionary<uint, ItemTemplateInfo> catalog)
        {
            Slot = slot; Template = item.Template; Quantity = item.Quantity;
            Quality = item.Quality; Structure = item.Structure; Range = item.Range;
            WeaponState = item.WeaponState; Price = item.Price; Info = item.Info;
            if (catalog.TryGetValue(item.Template, out ItemTemplateInfo template))
            { Name = template.Name; Type = template.Type; }
            else { Name = "Item #" + item.Template; Type = 0; }
        }
    }
}
