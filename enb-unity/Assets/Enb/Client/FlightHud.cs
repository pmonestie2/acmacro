using System;
using System.Collections.Generic;
using System.Linq;
using Enb.Protocol;
using UnityEngine;
using CharacterRecord = Enb.Protocol.CharacterInfo;

namespace Enb.Client
{
    /// <summary>Flight instruments and navigation controls for the foreground client.</summary>
    public sealed class FlightHud : IDisposable
    {
        private readonly SpaceClient client;
        public float Scale => Mathf.Max(.01f, Mathf.Min(1f, Screen.width / 1280f, Screen.height / 760f));
        private float CanvasWidth => Screen.width / Scale;
        private float CanvasHeight => Screen.height / Scale;
        private readonly Color cyan = new Color(.3f, .85f, 1), white = new Color(.86f, .93f, 1), muted = new Color(.46f, .62f, .72f), gold = new Color(1, .72f, .3f);
        private GUIStyle heading, body, small, button, selected, damageStyle;
        private Texture2D buttonTexture, selectedTexture, radarTexture;
        private Vector2 scroll;
        private bool navigation = true, equipmentOpen;
        private int selectedCargo = -1, selectedMount = -1;
        private Vector2 cargoScroll, mountScroll;
        public bool EquipmentOpen => equipmentOpen;
        public uint SelectedContactId => contactId;
        /// <returns>Whether a visible ship was selected at an actual screen point.</returns>
        public bool SelectContactAtPointer(Snapshot snapshot, Vector2 pointer)
        {
            float closest = 34; uint selectedContact = 0;
            foreach (ShipActor actor in snapshot.Players.Concat(snapshot.Mobs).Where(item => item.HasPosition))
            {
                Vector3 point = client.ProjectPlayer(actor.Id);
                if (point.z <= 0) continue;
                float distance = Vector2.Distance(new Vector2(point.x, point.y), pointer);
                if (distance < closest) { closest = distance; selectedContact = actor.Id; }
            }
            if (selectedContact == 0) return false;
            contactId = selectedContact; client.SelectContact(selectedContact);
            return true;
        }
        /// <returns>Whether a visible wreck was selected at an actual screen point.</returns>
        public bool SelectWreckAtPointer(Snapshot snapshot, Vector2 pointer)
        {
            float closest = 32; uint selectedWreck = 0;
            foreach (MapObject item in snapshot.Objects.Where(item => item.Kind == 25))
            {
                Vector3 point = client.Project(item.Position);
                if (point.z <= 0) continue;
                float distance = Vector2.Distance(new Vector2(point.x, point.y), pointer);
                if (distance < closest) { closest = distance; selectedWreck = item.Id; }
            }
            if (selectedWreck == 0) return false;
            wreckId = selectedWreck; return true;
        }
        /// <summary>Open or close the ship equipment overlay.</summary>
        public void ToggleEquipment() { equipmentOpen = !equipmentOpen; }
        private string chatText = "";
        private string inviteName = "";
        public bool InputFocused { get; private set; }
        private bool groupChat;
        private uint contactId, wreckId;
        private Vector2 contactsScroll;
        private sealed class FloatingDamage
        {
            public readonly uint Target;
            public readonly float Amount, Started;
            /// <summary>Retain one short lived combat label.</summary>
            public FloatingDamage(uint target, float amount)
            { Target = target; Amount = amount; Started = Time.unscaledTime; }
        }
        private readonly List<FloatingDamage> damageLabels = new List<FloatingDamage>();
        private long lastDamage;
        private MapObject[] destinations = Array.Empty<MapObject>();
        private float refreshAt;
        private string sector = "";

        /// <summary>Bind the HUD to its concrete foreground controller.</summary>
        public FlightHud(SpaceClient client) { this.client = client; }

        /// <returns>Whether the pointer is over foreground controls.</returns>
        public bool ContainsPointer()
        {
            Vector2 p = new Vector2(Input.mousePosition.x / Scale, (Screen.height - Input.mousePosition.y) / Scale);
            return !client.Connected || (equipmentOpen && EquipmentRect().Contains(p)) || new Rect(24, 100, 280, Mathf.Clamp(CanvasHeight - 360, 200, 440)).Contains(p) || (p.x < 354 && p.y > CanvasHeight - 235) || new Rect(CanvasWidth - 320, 20, 300, CanvasHeight - 40).Contains(p) || p.y > CanvasHeight - 115 || p.y < 85;
        }

        /// <returns>A one-pixel owned background texture.</returns>
        private static Texture2D Texture(Color color)
        { var texture = new Texture2D(1, 1); texture.SetPixel(0, 0, color); texture.Apply(); return texture; }

        /// <summary>Create styles inside a GUI event where the skin is available.</summary>
        private void EnsureStyles()
        {
            if (heading != null) return;
            heading = new GUIStyle(GUI.skin.label) { fontSize = 22, fontStyle = FontStyle.Bold }; heading.normal.textColor = white;
            body = new GUIStyle(GUI.skin.label) { fontSize = 14 }; body.normal.textColor = white;
            small = new GUIStyle(GUI.skin.label) { fontSize = 11 }; small.normal.textColor = muted;
            damageStyle = new GUIStyle(GUI.skin.label) { fontSize = 18, fontStyle = FontStyle.Bold, alignment = TextAnchor.MiddleCenter };
            damageStyle.normal.textColor = gold;
            buttonTexture = Texture(new Color(.035f, .075f, .105f, 1)); selectedTexture = Texture(new Color(.08f, .24f, .3f, 1));
            radarTexture = RadarTexture();
            button = new GUIStyle(GUI.skin.button) { fontSize = 13, alignment = TextAnchor.MiddleLeft, padding = new RectOffset(10, 8, 4, 4) };
            button.normal.background = buttonTexture; button.normal.textColor = white;
            button.hover.background = selectedTexture; button.hover.textColor = cyan;
            selected = new GUIStyle(button); selected.normal.background = selectedTexture; selected.normal.textColor = cyan;
        }

        /// <returns>A shared transparent scanner dial with concentric range rings.</returns>
        private static Texture2D RadarTexture()
        {
            const int size = 128; var pixels = new Color32[size * size];
            for (int y = 0; y < size; y++) for (int x = 0; x < size; x++)
            {
                float dx = x - 63.5f, dy = y - 63.5f;
                float radius = Mathf.Sqrt(dx * dx + dy * dy);
                float rings = Mathf.Min(Mathf.Abs(radius - 20), Mathf.Abs(radius - 41), Mathf.Abs(radius - 62));
                byte alpha = (byte)(rings < .7f ? 95 : Mathf.Abs(dx) < .55f || Mathf.Abs(dy) < .55f ? 40 : radius < 62 ? 9 : 0);
                pixels[y * size + x] = new Color32(55, 190, 210, alpha);
            }
            var texture = new Texture2D(size, size, TextureFormat.RGBA32, false)
            { filterMode = FilterMode.Bilinear, wrapMode = TextureWrapMode.Clamp };
            texture.SetPixels32(pixels); texture.Apply(); return texture;
        }

        /// <summary>Draw a tinted rectangle without retaining GUI color changes.</summary>
        private static void Fill(Rect rect, Color color)
        { Color previous = GUI.color; GUI.color = color; GUI.DrawTexture(rect, Texture2D.whiteTexture); GUI.color = previous; }

        /// <summary>Draw a subtle panel and accent line.</summary>
        private void Panel(Rect rect)
        {
            Fill(rect, new Color(.007f, .018f, .032f, .96f));
            Fill(new Rect(rect.x, rect.y, rect.width, 2), new Color(.14f, .65f, .76f, .9f));
            Fill(new Rect(rect.x, rect.y + 2, rect.width, 8), new Color(.07f, .29f, .38f, .28f));
            Fill(new Rect(rect.x, rect.y, 2, 15), cyan);
            Fill(new Rect(rect.x + rect.width - 2, rect.y + rect.height - 15, 2, 15), cyan);
            Fill(new Rect(rect.x + rect.width - 15, rect.y + rect.height - 2, 15, 2), cyan);
        }

        /// <summary>Draw a shield, energy or hull gauge over a dim track.</summary>
        private void Meter(Rect rect, string label, float value, Color color)
        {
            Fill(rect, new Color(.07f, .13f, .17f, 1));
            Fill(new Rect(rect.x, rect.y, rect.width * Mathf.Clamp01(value), rect.height), color);
            GUI.Label(new Rect(rect.x + 5, rect.y - 4, rect.width - 10, 18), label, small);
        }

        /// <returns>The centered equipment overlay bounds.</returns>
        private Rect EquipmentRect()
        { return new Rect((CanvasWidth - 760) * .5f, Mathf.Max(95, (CanvasHeight - 610) * .5f), 760, Mathf.Min(610, CanvasHeight - 130)); }

        /// <summary>Draw connection controls, flight instruments and navigation.</summary>
        public void Draw(ref string host, ref string username, ref string password)
        {
            EnsureStyles(); if (!client.Connected) { InputFocused = false; Connection(ref host, ref username, ref password); return; }
            if (client.ChoosingCharacter) { InputFocused = false; CharacterSelection(); return; }
            if (client.CurrentSnapshot == null)
            {
                InputFocused = false;
                var waiting = new Rect(CanvasWidth / 2 - 220, CanvasHeight / 2 - 120, 440, 240);
                Panel(waiting); GUI.Label(new Rect(waiting.x + 24, waiting.y + 24, 390, 36), "42 AND BEYOND", heading);
                GUI.Label(new Rect(waiting.x + 24, waiting.y + 74, 390, 48), client.LoginError ?? client.Status, body);
                GUI.Label(new Rect(waiting.x + 24, waiting.y + 128, 390, 40), client.PickingCharacter ? "Reading characters from the server…" : "Entering space…", small);
                if (GUI.Button(new Rect(waiting.x + 24, waiting.y + 182, 150, 34), "Back to login", button)) client.ReturnToLogin();
                return;
            }
            Snapshot snapshot = client.CurrentSnapshot;
            Panel(new Rect(24, 20, 390, 65)); GUI.Label(new Rect(38, 29, 360, 29), snapshot?.Sector.ToUpperInvariant() ?? "CONNECTING", heading);
            GUI.Label(new Rect(38, 58, 360, 20), "42 AND BEYOND   /   LOCAL UNIVERSE", small);
            Panel(new Rect(24, CanvasHeight - 112, 330, 88)); GUI.Label(new Rect(38, CanvasHeight - 105, 300, 20), "IMPULSE / WARP VELOCITY", small);
            GUI.Label(new Rect(38, CanvasHeight - 83, 300, 30), $"{Math.Abs(snapshot?.Speed ?? 0):N0}  m/s", heading);
            GUI.Label(new Rect(38, CanvasHeight - 52, 300, 20), client.Status, small);
            GUI.Label(new Rect(375, CanvasHeight - 46, CanvasWidth - 720, 40), "W / S thrust   ·   A / D turn   ·   ↑ ↓ pitch   ·   SPACE brake\nRMB orbit   ·   Wheel zoom   ·   C reset camera   ·   TAB chart", small);
            if (GUI.Button(new Rect(CanvasWidth - 160, 25, 136, 28), client.Chart ? "TAB  Flight view" : "TAB  Sector chart", button)) client.SetChart(!client.Chart);
            if (GUI.Button(new Rect(CanvasWidth - 300, 25, 130, 28), "Disconnect", button)) { client.Disconnect(); return; }
            if (snapshot == null) return;
            if (Time.unscaledTime >= refreshAt || sector != snapshot.Sector)
            {
                if (sector != snapshot.Sector) { lastDamage = 0; damageLabels.Clear(); }
                destinations = snapshot.Objects.Where(item => item.Navigable).OrderBy(item => item.Position.Distance(snapshot.Position)).ToArray();
                refreshAt = Time.unscaledTime + .25f; sector = snapshot.Sector;
            }
            TrackDamage(snapshot);
            if (Event.current.type == EventType.Repaint) { WorldLabels(snapshot); PlayerLabels(snapshot); WreckLabels(snapshot); DamageLabels(snapshot); }
            Navigation(snapshot);
            Radar(snapshot);
            Social(snapshot);
            if (equipmentOpen) Equipment(snapshot);
            InputFocused = GUI.GetNameOfFocusedControl() == "Group invite";
            ChatEntry();
            if (!client.Chart)
            {
                float x = CanvasWidth * .5f, y = CanvasHeight * .5f;
                Fill(new Rect(x - 12, y, 8, 1), cyan); Fill(new Rect(x + 4, y, 8, 1), cyan); Fill(new Rect(x, y - 12, 1, 8), cyan); Fill(new Rect(x, y + 4, 1, 8), cyan);
            }
        }

        /// <summary>Let the player choose one of the account's server characters.</summary>
        private void CharacterSelection()
        {
            CharacterRecord[] characters = client.Characters;
            var rect = new Rect(CanvasWidth / 2 - 220, CanvasHeight / 2 - 170, 440, 340);
            Panel(rect); GUI.Label(new Rect(rect.x + 24, rect.y + 20, 390, 34), "CHOOSE YOUR TOON", heading);
            GUI.Label(new Rect(rect.x + 24, rect.y + 58, 390, 24), "Characters on this account", small);
            for (int i = 0; i < characters.Length; i++)
            {
                CharacterRecord character = characters[i];
                if (GUI.Button(new Rect(rect.x + 24, rect.y + 96 + i * 48, 392, 40), character.Name, button))
                    client.ChooseCharacter(character.Slot);
            }
            if (GUI.Button(new Rect(rect.x + 24, rect.y + 286, 150, 32), "Back to login", button)) client.ReturnToLogin();
        }

        /// <summary>Show a compact command line for server chat and /d_ commands.</summary>
        private void ChatEntry()
        {
            if (!client.ChatOpen) return;
            groupChat = GUI.Toggle(new Rect(CanvasWidth * .5f - 260, CanvasHeight - 118, 180, 24), groupChat, "Group chat (else sector)");
            float x = CanvasWidth * .5f - 260, y = CanvasHeight - 94;
            Panel(new Rect(x, y, 520, 52));
            GUI.SetNextControlName("ENB chat command");
            chatText = GUI.TextField(new Rect(x + 10, y + 10, 415, 32), chatText, 180);
            if (Time.frameCount == client.ChatOpenedFrame + 1) GUI.FocusControl("ENB chat command");
            if (Time.frameCount > client.ChatOpenedFrame && Event.current.type == EventType.KeyDown && Event.current.keyCode == KeyCode.Return)
            {
                if (!string.IsNullOrWhiteSpace(chatText)) client.SendChat(chatText, groupChat); chatText = ""; client.CloseChatEntry(); Event.current.Use();
            }
            if (GUI.Button(new Rect(x + 432, y + 10, 78, 32), "Send", button))
            { if (!string.IsNullOrWhiteSpace(chatText)) client.SendChat(chatText, groupChat); chatText = ""; }
        }

        /// <summary>Draw the account form without saving credentials.</summary>
        private void Connection(ref string host, ref string username, ref string password)
        {
            var rect = new Rect(CanvasWidth / 2 - 220, CanvasHeight / 2 - 170, 440, 340); Panel(rect);
            GUILayout.BeginArea(new Rect(rect.x + 24, rect.y + 20, 392, 300));
            GUILayout.Label("42 AND BEYOND", heading); GUILayout.Label("LOCAL UNIVERSE", small); GUILayout.Space(24);
            GUILayout.Label("Server IPv4", small); host = GUILayout.TextField(host);
            GUILayout.Label("Account", small); username = GUILayout.TextField(username);
            GUILayout.Label("Password", small); password = GUILayout.PasswordField(password, '*'); GUILayout.Space(16);
            if (GUILayout.Button("ENTER SPACE", button, GUILayout.Height(36))) client.StartLocalSession(host, username, password);
            GUILayout.Label(client.Status, small); GUILayout.EndArea();
        }

        /// <summary>Draw distance-sorted destinations and selected-target actions.</summary>
        private void Navigation(Snapshot snapshot)
        {
            float x = CanvasWidth - 320;
            if (GUI.Button(new Rect(x, 68, 296, 30), navigation ? "NAVIGATION   −" : "NAVIGATION   +", button)) navigation = !navigation;
            if (!navigation) return;
            float height = Mathf.Clamp(CanvasHeight - 510, 200, 370); Panel(new Rect(x, 100, 296, height));
            GUILayout.BeginArea(new Rect(x + 8, 108, 280, height - 16)); scroll = GUILayout.BeginScrollView(scroll);
            MapObject selectedTarget = client.Target;
            foreach (MapObject item in destinations)
            {
                string kind = item.Kind == 11 ? "GATE" : item.Kind == 12 ? "PORT" : "NAV";
                string text = $"{item.Name}\n{kind}   {item.Position.Distance(snapshot.Position) / 1000:0.0} km";
                if (GUILayout.Button(text, selectedTarget?.Id == item.Id ? selected : button, GUILayout.Height(43))) client.Select(item.Id);
            }
            GUILayout.EndScrollView(); GUILayout.EndArea(); float top = height + 112; MapObject target = client.Target;
            Panel(new Rect(x, top, 296, 145)); GUI.Label(new Rect(x + 12, top + 9, 270, 24), target?.Name ?? "Select a destination", body);
            GUI.Label(new Rect(x + 12, top + 34, 270, 22), target == null ? "Click a marker or choose a destination" : $"RANGE   {target.Position.Distance(snapshot.Position) / 1000:0.00} km", small);
            GUI.enabled = client.Ready && target != null;
            if (GUI.Button(new Rect(x + 12, top + 63, 272, 32), "F    WARP TO TARGET", button)) client.WarpSelected();
            GUI.enabled = client.Ready && target?.Kind == 11;
            if (GUI.Button(new Rect(x + 12, top + 102, 272, 30), "G    ACTIVATE GATE", button)) client.GateSelected(); GUI.enabled = true;
        }

        /// <summary>Draw a world-relative scanner with nearby objects and ships.</summary>
        private void Radar(Snapshot snapshot)
        {
            float x = CanvasWidth - 320, y = CanvasHeight - 210;
            Panel(new Rect(x, y, 296, 185));
            GUI.Label(new Rect(x + 12, y + 8, 270, 18), "TACTICAL RADAR   /   20 KM", small);
            var center = new Vector2(x + 148, y + 104);
            GUI.DrawTexture(new Rect(center.x - 70, center.y - 70, 140, 140), radarTexture);
            float sweep = Time.unscaledTime * .65f;
            Matrix4x4 previous = GUI.matrix;
            GUIUtility.RotateAroundPivot(-sweep * Mathf.Rad2Deg, center);
            Fill(new Rect(center.x - 1, center.y - 67, 2, 67), new Color(.25f, .85f, .9f, .27f));
            GUI.matrix = previous;
            foreach (MapObject item in destinations.Take(12))
                RadarDot(center, snapshot.Position, item.Position, item.Id == client.Target?.Id ? gold : muted, 3);
            foreach (ShipActor actor in snapshot.Players.Concat(snapshot.Mobs).Where(a => a.HasPosition).Take(64))
                RadarDot(center, snapshot.Position, actor.Position,
                    actor.Id == contactId ? white : actor.IsPlayer ? cyan : actor.Hostile ? new Color(1, .32f, .23f) : gold, 5);
            Fill(new Rect(center.x - 3, center.y - 3, 6, 6), white);
            GUI.Label(new Rect(x + 206, y + 153, 70, 18), "N  ↑", small);
        }

        /// <summary>Project one contact into the radar's twenty-kilometer radius.</summary>
        private void RadarDot(Vector2 center, Position own, Position contact, Color color, float size)
        {
            float dx = contact.Y - own.Y, dy = contact.X - own.X;
            float distance = Mathf.Sqrt(dx * dx + dy * dy);
            if (distance > 20000) return;
            Fill(new Rect(center.x + dx * 67 / 20000 - size * .5f,
                center.y - dy * 67 / 20000 - size * .5f, size, size), color);
        }

        /// <summary>Project limited markers without enlarging physical models.</summary>
        private void WorldLabels(Snapshot snapshot)
        {
            var occupied = new List<Rect>();
            MapObject selectedTarget = client.Target;
            foreach (MapObject item in destinations.OrderByDescending(item => selectedTarget?.Id == item.Id))
            {
                if (occupied.Count >= 9) break; Vector3 p = client.Project(item.Position); p.x /= Scale; p.y /= Scale;
                if (p.z <= 0 || p.x < 30 || p.x > CanvasWidth - 335 || p.y < 100 || p.y > CanvasHeight - 145) continue;
                bool active = selectedTarget?.Id == item.Id; Color color = active ? gold : cyan; float radius = active ? 11 : 5;
                Fill(new Rect(p.x - radius, p.y - radius, 5, 1), color); Fill(new Rect(p.x - radius, p.y - radius, 1, 5), color);
                Fill(new Rect(p.x + radius - 5, p.y + radius, 5, 1), color); Fill(new Rect(p.x + radius, p.y + radius - 5, 1, 5), color);
                var rect = new Rect(Mathf.Clamp(p.x - 85, 24, Mathf.Max(24, CanvasWidth - 510)), p.y + radius + 5, 175, 38);
                for (int attempt = 0; attempt < 3 && occupied.Any(other => other.Overlaps(rect)); attempt++) rect.y += 38;
                if (rect.yMax > CanvasHeight - 120 || occupied.Any(other => other.Overlaps(rect))) continue;
                occupied.Add(rect); GUI.color = color; GUI.Label(rect, $"{item.Name}\n{item.Position.Distance(snapshot.Position) / 1000:0.0} km", small); GUI.color = Color.white;
            }
        }

        /// <summary>Identify nearby player ships at their interpolated screen positions.</summary>
        private void PlayerLabels(Snapshot snapshot)
        {
            int shown = 0;
            foreach (ShipActor player in snapshot.Players.Concat(snapshot.Mobs))
            {
                Vector3 point = client.ProjectPlayer(player.Id); point.x /= Scale; point.y /= Scale;
                if (point.z <= 0 || point.x < 30 || point.x > CanvasWidth - 335 || point.y < 100 || point.y > CanvasHeight - 145) continue;
                if (++shown > 32) break;
                Color color = player.IsPlayer ? cyan : player.Hostile ? new Color(1, .35f, .2f) : gold;
                bool active = contactId == player.Id || snapshot.SelectedTargetId == player.Id;
                float radius = active ? 13 : 4;
                Fill(new Rect(point.x - radius, point.y - radius, active ? 10 : 8, 2), color);
                Fill(new Rect(point.x - radius, point.y - radius, 2, active ? 10 : 8), color);
                if (active)
                {
                    Fill(new Rect(point.x + radius - 10, point.y + radius, 10, 2), color);
                    Fill(new Rect(point.x + radius, point.y + radius - 10, 2, 10), color);
                    if (player.Shield >= 0) Meter(new Rect(point.x + 10, point.y + 34, 120, 6), "", player.Shield, cyan);
                    if (player.MaxHull > 0) Meter(new Rect(point.x + 10, point.y + 43, 120, 6), "", player.Hull / player.MaxHull, gold);
                }
                GUI.Label(new Rect(point.x + 10, point.y - 8, 220, 44),
                    player.Name + (player.IsPlayer ? "  /  PLAYER\n" : player.Hostile ? "  /  HOSTILE\n" : "  /  NPC\n") + (player.Position.Distance(snapshot.Position) / 1000).ToString("0.00") + " km", body);
            }
        }

        /// <summary>Identify nearby wrecks independently of navigation points.</summary>
        private void WreckLabels(Snapshot snapshot)
        {
            foreach (MapObject item in snapshot.Objects.Where(item => item.Kind == 25).Take(12))
            {
                Vector3 point = client.Project(item.Position); point.x /= Scale; point.y /= Scale;
                if (point.z <= 0 || point.x < 30 || point.x > CanvasWidth - 335 || point.y < 90 || point.y > CanvasHeight - 140) continue;
                bool active = wreckId == item.Id;
                Fill(new Rect(point.x - 5, point.y - 5, active ? 10 : 6, 2), gold);
                GUI.color = gold;
                GUI.Label(new Rect(point.x + 11, point.y - 9, 180, 35),
                    item.Name + "  /  WRECK\n" + (item.Position.Distance(snapshot.Position) / 1000).ToString("0.0") + " km", small);
                GUI.color = Color.white;
            }
        }

        /// <summary>Keep each server damage report as one fading display event.</summary>
        private void TrackDamage(Snapshot snapshot)
        {
            foreach (DamageEvent damage in snapshot.Damages)
            {
                if (damage.Sequence <= lastDamage) continue;
                lastDamage = damage.Sequence;
                damageLabels.Add(new FloatingDamage(damage.Target, damage.Amount));
                if (damageLabels.Count > 20) damageLabels.RemoveAt(0);
            }
            damageLabels.RemoveAll(label => Time.unscaledTime - label.Started > 1.3f);
        }

        /// <summary>Project transient hit numbers above the impacted ship.</summary>
        private void DamageLabels(Snapshot snapshot)
        {
            foreach (FloatingDamage label in damageLabels)
            {
                Vector3 point = label.Target == snapshot.PlayerId
                    ? new Vector3(CanvasWidth * .5f, CanvasHeight * .5f, 1)
                    : client.ProjectPlayer(label.Target);
                point.x /= label.Target == snapshot.PlayerId ? 1 : Scale;
                point.y /= label.Target == snapshot.PlayerId ? 1 : Scale;
                if (point.z <= 0 || point.x < 280 || point.x > CanvasWidth - 320) continue;
                float age = Time.unscaledTime - label.Started;
                Color previous = GUI.color; GUI.color = new Color(1, 1, 1, 1 - age / 1.3f);
                GUI.Label(new Rect(point.x - 40, point.y - 45 - age * 34, 80, 30), label.Amount.ToString("0"), damageStyle);
                GUI.color = previous;
            }
        }

        /// <summary>Offer authoritative group controls and a selectable nearby-contact roster.</summary>
        private void Social(Snapshot snapshot)
        {
            float height = Mathf.Clamp(CanvasHeight - 360, 200, 440);
            Panel(new Rect(24, 100, 280, height));
            GUILayout.BeginArea(new Rect(34, 108, 260, height - 16));
            contactsScroll = GUILayout.BeginScrollView(contactsScroll);
            GUILayout.Label(snapshot.Group.Joined ? "GROUP" + (snapshot.Group.IsLeader ? " · LEADER" : "") : "GROUP · SOLO", body);
            GUILayout.Label("Invite any online toon by name", small);
            GUI.SetNextControlName("Group invite");
            inviteName = GUILayout.TextField(inviteName, 20);
            GUILayout.BeginHorizontal();
            if (GUILayout.Button("Invite", button, GUILayout.Height(28))) InviteByName();
            if (GUILayout.Button("Who is online", button, GUILayout.Height(28))) client.SendChat("/who");
            GUILayout.EndHorizontal();
            if (Event.current.type == EventType.KeyDown && Event.current.keyCode == KeyCode.Return &&
                GUI.GetNameOfFocusedControl() == "Group invite")
            { InviteByName(); Event.current.Use(); }
            if (!string.IsNullOrEmpty(snapshot.Invitation))
            {
                GUILayout.Label(snapshot.Invitation, body);
                GUILayout.BeginHorizontal();
                if (GUILayout.Button("Accept", button)) client.SendChat("/accept");
                if (GUILayout.Button("Decline", button)) client.SendChat("/decline");
                GUILayout.EndHorizontal();
            }
            foreach (GroupMember member in snapshot.Group.Members)
            {
                GUILayout.BeginHorizontal(); GUILayout.Label(member.Name + (member.Formed ? "  ·  IN FORMATION" : ""), body);
                if (snapshot.Group.IsLeader && GUILayout.Button("Kick", button, GUILayout.Width(60))) client.SendChat("/kick " + member.Name);
                GUILayout.EndHorizontal();
            }
            if (snapshot.Group.Joined)
            {
                if (snapshot.Group.IsLeader && snapshot.SelectedTargetId != 0 && snapshot.SelectedTargetId != uint.MaxValue &&
                    GUILayout.Button("Share selected target", button)) client.Formation(12);
                GUILayout.Space(8);
                GUILayout.Label("FORMATION  " + (snapshot.Group.Formation.Length > 0 ? snapshot.Group.Formation.ToUpperInvariant() : "NONE"), small);
                if (snapshot.Group.IsLeader)
                {
                    GUILayout.BeginHorizontal();
                    foreach (var choice in new[] { (4u, "Slot Back"), (5u, "Block"), (6u, "Pipe") })
                        if (GUILayout.Button(choice.Item2, snapshot.Group.Formation == choice.Item2 ? selected : button))
                            client.Formation(choice.Item1);
                    GUILayout.EndHorizontal();
                    if (snapshot.Group.Formation.Length > 0 && GUILayout.Button("Release formation", button)) client.Formation(9);
                }
                else if (snapshot.Group.Formation.Length > 0)
                {
                    if (GUILayout.Button(snapshot.Group.Formed ? "Leave formation" : "Form up with leader", button))
                        client.Formation(snapshot.Group.Formed ? 8u : 7u);
                    if (!snapshot.Group.Formed) GUILayout.Label("Join within 5 km of your leader", small);
                }
                GUILayout.BeginHorizontal();
                if (GUILayout.Button("Leave", button)) client.SendChat("/leave");
                if (snapshot.Group.IsLeader && GUILayout.Button("Disband", button)) client.SendChat("/disband");
                GUILayout.EndHorizontal();
            }
            GUILayout.Space(12); GUILayout.Label("CONTACTS", body);
            foreach (ShipActor actor in snapshot.Players.Concat(snapshot.Mobs).Where(a => a.HasPosition)
                .OrderBy(a => a.Position.Distance(snapshot.Position)))
            {
                string kind = actor.IsPlayer ? "PLAYER" : actor.Hostile ? "HOSTILE" : "NPC";
                if (GUILayout.Button($"{actor.Name} · {kind}\n{actor.Position.Distance(snapshot.Position) / 1000:0.0} km",
                    contactId == actor.Id ? selected : button))
                { contactId = actor.Id; client.SelectContact(actor.Id); }
            }
            ShipActor contact = snapshot.Players.Concat(snapshot.Mobs).FirstOrDefault(a => a.Id == contactId);
            if (contact != null)
            {
                GUILayout.Space(8); GUILayout.Label(contact.Name, body);
                if (contact.Shield >= 0) GUILayout.Label($"Shield  {contact.Shield * 100:0}%", small);
                if (contact.MaxHull > 0) GUILayout.Label($"Hull  {contact.Hull:0} / {contact.MaxHull:0}", small);
                if (contact.IsPlayer && GUILayout.Button("Invite to group", button)) client.SendChat("/invite " + contact.Name);
                if (!contact.IsPlayer && GUILayout.Button(snapshot.WeaponsFiring ? "Stop weapons  ·  X" : "Fire weapons  ·  X", button))
                { if (snapshot.WeaponsFiring) client.StopWeapons(); else client.ToggleWeapons(contact.Id); }
            }
            if (snapshot.WeaponsFiring && GUILayout.Button("STOP ALL WEAPONS  ·  X", button)) client.StopWeapons();
            MapObject[] wrecks = snapshot.Objects.Where(item => item.Kind == 25)
                .OrderBy(item => item.Position.Distance(snapshot.Position)).Take(12).ToArray();
            if (wrecks.Length > 0) GUILayout.Label("WRECKS", body);
            foreach (MapObject wreck in wrecks)
                if (GUILayout.Button(wreck.Name + "  ·  " + (wreck.Position.Distance(snapshot.Position) / 1000).ToString("0.0") + " km",
                    wreckId == wreck.Id ? selected : button)) wreckId = wreck.Id;
            MapObject selectedWreck = wrecks.FirstOrDefault(item => item.Id == wreckId);
            if (selectedWreck != null)
            {
                bool nearby = selectedWreck.Position.Distance(snapshot.Position) < 2000;
                GUI.enabled = nearby;
                if (GUILayout.Button(snapshot.OpenLootId == wreckId ? "Wreck open" : "Open wreck", button)) client.OpenLoot(wreckId);
                GUI.enabled = true;
                if (!nearby) GUILayout.Label("Move within 2 km to loot", small);
                CorpseLoot contents = snapshot.Loot.FirstOrDefault(item => item.Id == wreckId);
                if (snapshot.OpenLootId == wreckId && contents != null)
                {
                    ItemView[] stock = contents.Items.Where(item => item != null).ToArray();
                    if (stock.Length == 0) GUILayout.Label("No items remain", small);
                    foreach (ItemView item in stock)
                        if (GUILayout.Button("TRACTOR  " + item.Name + "  ×" + item.Quantity, button))
                            client.TakeLoot(wreckId, item.Slot);
                }
            }
            GUILayout.EndScrollView(); GUILayout.EndArea();
            float top = CanvasHeight - 235;
            Panel(new Rect(24, top, 330, 115));
            GUI.Label(new Rect(34, top + 6, 310, 105), string.Join("\n", snapshot.Messages.Reverse().Take(5).Reverse()), small);
        }

        /// <summary>Draw server-reported cargo and hardpoints with explicit equip controls.</summary>
        private void Equipment(Snapshot snapshot)
        {
            Rect rect = EquipmentRect(); Panel(rect);
            GUI.Label(new Rect(rect.x + 20, rect.y + 16, 650, 32), "SHIP LOADOUT", heading);
            if (GUI.Button(new Rect(rect.xMax - 47, rect.y + 15, 28, 28), "×", button)) equipmentOpen = false;
            GUI.Label(new Rect(rect.x + 20, rect.y + 50, 700, 24),
                $"{snapshot.WeaponMounts} weapon hardpoints  ·  {snapshot.DeviceMounts} device slots  ·  Server equipment", small);
            float columnHeight = rect.height - 165;
            GUILayout.BeginArea(new Rect(rect.x + 20, rect.y + 83, 344, columnHeight));
            GUILayout.Label("CARGO HOLD", body);
            cargoScroll = GUILayout.BeginScrollView(cargoScroll);
            foreach (ItemView item in snapshot.Cargo)
            {
                if (item == null) continue;
                string title = $"{item.Slot:00}  {item.Name}" + (item.Quantity > 1 ? $"  ×{item.Quantity}" : "");
                if (GUILayout.Button(title, selectedCargo == item.Slot ? selected : button, GUILayout.Height(28))) selectedCargo = item.Slot;
            }
            GUILayout.EndScrollView(); GUILayout.EndArea();
            GUILayout.BeginArea(new Rect(rect.x + 393, rect.y + 83, 344, columnHeight));
            GUILayout.Label("INSTALLED HARDPOINTS", body);
            mountScroll = GUILayout.BeginScrollView(mountScroll);
            for (int slot = 0; slot < snapshot.Mounted.Length; slot++)
            {
                bool available = slot < 3 || slot >= 3 && slot < 3 + snapshot.WeaponMounts ||
                    slot >= 9 && slot < 9 + snapshot.DeviceMounts;
                if (!available) continue;
                ItemView item = snapshot.Mounted[slot];
                string title = $"{slot:00}  " + (item?.Name ?? (slot < 3 ? new[] { "Reactor", "Engine", "Shield" }[slot] : slot < 9 ? "Weapon" : "Device") + " · EMPTY");
                if (GUILayout.Button(title, selectedMount == slot ? selected : button, GUILayout.Height(28))) selectedMount = slot;
            }
            GUILayout.EndScrollView(); GUILayout.EndArea();
            float foot = rect.yMax - 70;
            ItemView cargo = selectedCargo >= 0 && selectedCargo < snapshot.Cargo.Length ? snapshot.Cargo[selectedCargo] : null;
            ItemView mounted = selectedMount >= 0 && selectedMount < snapshot.Mounted.Length ? snapshot.Mounted[selectedMount] : null;
            GUI.Label(new Rect(rect.x + 20, foot - 31, 710, 24),
                cargo != null ? $"Selected: {cargo.Name} · quality {cargo.Quality:0.00} · structure {cargo.Structure:0}%" :
                mounted != null ? $"Selected: {mounted.Name} · quality {mounted.Quality:0.00} · range {mounted.Range:0} m" :
                "Choose cargo and a hardpoint to equip, or choose installed gear to remove.", small);
            GUI.enabled = cargo != null && selectedMount >= 0;
            if (GUI.Button(new Rect(rect.x + 20, foot, 330, 34), cargo?.Type == 10 ? "LOAD AMMUNITION  →" : "INSTALL SELECTED CARGO  →", button))
                client.SendChat($"/equip {selectedCargo} {selectedMount}");
            GUI.enabled = mounted != null;
            if (GUI.Button(new Rect(rect.x + 393, foot, 344, 34), "REMOVE FROM HARDPOINT", button))
                client.SendChat($"/unequip {selectedMount}");
            GUI.enabled = true;
        }

        /// <summary>Submit a named invitation to the server, including players outside scan range.</summary>
        private void InviteByName()
        {
            string name = inviteName.Trim();
            if (name.Length == 0) return;
            client.SendChat("/invite " + name);
            inviteName = "";
            GUI.FocusControl("");
        }

        /// <summary>Release generated button backgrounds.</summary>
        public void Dispose() { if (buttonTexture != null) UnityEngine.Object.Destroy(buttonTexture); if (selectedTexture != null) UnityEngine.Object.Destroy(selectedTexture); if (radarTexture != null) UnityEngine.Object.Destroy(radarTexture); }
    }
}
