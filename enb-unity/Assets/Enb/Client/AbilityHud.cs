using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Enb.Protocol;
using UnityEngine;

namespace Enb.Client
{
    /// <summary>Display server-advertised class abilities and activate them in space.</summary>
    public sealed class AbilityHud
    {
        private sealed class AbilityDefinition
        {
            public readonly int id, skill;
            public readonly string name, description, kind;
            public readonly int rank, range;
            public readonly float energy, charge, cooldown;

            /// <summary>Parse one generated display row from the server catalog.</summary>
            public AbilityDefinition(string[] fields)
            {
                id = int.Parse(fields[0], CultureInfo.InvariantCulture);
                skill = int.Parse(fields[1], CultureInfo.InvariantCulture);
                name = fields[2]; description = fields[3]; kind = fields[4];
                rank = int.Parse(fields[5], CultureInfo.InvariantCulture);
                energy = float.Parse(fields[6], CultureInfo.InvariantCulture);
                charge = float.Parse(fields[7], CultureInfo.InvariantCulture);
                cooldown = float.Parse(fields[8], CultureInfo.InvariantCulture);
                range = int.Parse(fields[9], CultureInfo.InvariantCulture);
            }
        }

        private readonly SpaceClient client;
        private readonly FlightHud flightHud;
        private readonly AbilityDefinition[] catalog;
        private Vector2 scroll;
        private bool open;
        public bool IsOpen => open;
        private float CanvasWidth => Screen.width / flightHud.Scale;
        private float CanvasHeight => Screen.height / flightHud.Scale;

        /// <summary>Load public display text; the live server still decides availability.</summary>
        public AbilityHud(SpaceClient client, FlightHud flightHud)
        {
            this.client = client; this.flightHud = flightHud;
            TextAsset asset = Resources.Load<TextAsset>("AbilityCatalog");
            var entries = new List<AbilityDefinition>();
            if (asset != null)
                foreach (string row in asset.text.Split('\n'))
                {
                    string[] fields = row.TrimEnd('\r').Split('\t');
                    if (fields.Length == 10) entries.Add(new AbilityDefinition(fields));
                }
            catalog = entries.ToArray();
        }

        /// <returns>bool: Whether pointer input belongs to the ability button or open panel.</returns>
        public bool ContainsPointer()
        {
            Vector2 point = new Vector2(Input.mousePosition.x / flightHud.Scale,
                (Screen.height - Input.mousePosition.y) / flightHud.Scale);
            return ButtonRect().Contains(point) || open && PanelRect().Contains(point);
        }

        /// <returns>Rect: Bounds of the ability panel's persistent toolbar button.</returns>
        private Rect ButtonRect() { return new Rect(CanvasWidth - 550, 25, 110, 28); }

        /// <returns>Rect: Bounds of the scrollable ability panel.</returns>
        private Rect PanelRect()
        {
            float height = Mathf.Min(600, CanvasHeight - 145);
            return new Rect((CanvasWidth - 520) * .5f, 82, 520, height);
        }

        /// <summary>Show only abilities that this character received in native RPG packets.</summary>
        public void Draw(Snapshot snapshot)
        {
            if (snapshot == null || snapshot.Station != null) { open = false; return; }
            if (GUI.Button(ButtonRect(), open ? "Close skills" : "Skills", flightHud.ButtonStyle))
                open = !open;
            if (!open) return;
            Rect panel = PanelRect(); flightHud.Panel(panel);
            GUI.Label(new Rect(panel.x + 18, panel.y + 15, 380, 28), "CLASS ABILITIES", flightHud.BodyStyle);
            if (GUI.Button(new Rect(panel.xMax - 48, panel.y + 12, 30, 28), "×", flightHud.ButtonStyle))
            { open = false; return; }
            GUI.Label(new Rect(panel.x + 18, panel.y + 42, panel.width - 36, 20),
                "Select a target first for combat or group abilities.", flightHud.SmallStyle);
            AbilityDefinition[] available = catalog.Where(item => item.id >= 0 && snapshot.AvailableAbilities.Contains((uint)item.id))
                .OrderBy(item => item.name).ToArray();
            GUILayout.BeginArea(new Rect(panel.x + 18, panel.y + 70, panel.width - 36, panel.height - 119));
            scroll = GUILayout.BeginScrollView(scroll);
            if (available.Length == 0)
                GUILayout.Label("Waiting for class abilities from the server…", flightHud.SmallStyle);
            foreach (AbilityDefinition ability in available)
            {
                snapshot.AbilityCooldownSeconds.TryGetValue((uint)ability.skill, out float recovery);
                bool charging = snapshot.CastRemainingSeconds > 0;
                string description = $"{ability.description}\nEnergy {ability.energy:0} · Charge {ability.charge:0.#} s · Cooldown {ability.cooldown:0.#} s · Range {ability.range} m";
                string label = recovery > 0 ? $"{ability.name}  ·  READY IN {recovery:0.#} S" :
                    charging && snapshot.CastingAbilityId == (uint)ability.id ?
                    $"{ability.name}  ·  CHARGING {snapshot.CastRemainingSeconds:0.#} S" :
                    $"{ability.name}  ·  RANK {ability.rank}  ·  {ability.energy:0} ENERGY";
                GUI.enabled = recovery <= 0 && !charging;
                if (GUILayout.Button(new GUIContent(label, description), flightHud.ButtonStyle, GUILayout.Height(34)))
                    client.ActivateAbility((uint)ability.id);
                GUI.enabled = true;
            }
            GUILayout.EndScrollView(); GUILayout.EndArea();
            string tooltip = GUI.tooltip;
            GUI.Label(new Rect(panel.x + 18, panel.yMax - 43, panel.width - 36, 35),
                string.IsNullOrEmpty(tooltip) ? client.Status : tooltip, flightHud.SmallStyle);
        }
    }
}
