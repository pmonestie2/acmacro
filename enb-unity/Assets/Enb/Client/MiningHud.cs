using System;
using System.Linq;
using Enb.Protocol;
using UnityEngine;

namespace Enb.Client
{
    /// <summary>Show mineable resources and native prospecting controls over the flight view.</summary>
    public sealed class MiningHud
    {
        private readonly SpaceClient client;
        private readonly FlightHud flightHud;
        private uint selectedId;
        private MapObject[] nearby = Array.Empty<MapObject>();
        private Vector2 oreScroll;
        private float refreshedAt;
        private string sector = "";
        private float CanvasWidth => Screen.width / flightHud.Scale;
        private float CanvasHeight => Screen.height / flightHud.Scale;

        /// <summary>Share the foreground controller without owning network state.</summary>
        public MiningHud(SpaceClient client, FlightHud flightHud)
        { this.client = client; this.flightHud = flightHud; }

        /// <summary>Discard a resource selection after sector travel or logout.</summary>
        public void Clear() { selectedId = 0; nearby = Array.Empty<MapObject>(); sector = ""; }

        /// <returns>bool: Whether the pointer falls on the selected resource panel.</returns>
        public bool ContainsPointer()
        {
            if (selectedId == 0) return false;
            Vector2 point = new Vector2(Input.mousePosition.x / flightHud.Scale,
                (Screen.height - Input.mousePosition.y) / flightHud.Scale);
            Snapshot snapshot = client.CurrentSnapshot;
            return PanelRect(snapshot != null && snapshot.ResourceWindowOpen && snapshot.ResourceTargetId == selectedId).Contains(point);
        }

        /// <returns>bool: Whether a visible resource was selected under the pointer.</returns>
        public bool SelectAtPointer(Snapshot snapshot, Vector2 pointer)
        {
            if (snapshot == null) return false;
            float closest = 36; uint found = 0;
            foreach (MapObject resource in snapshot.Objects.Where(item => item.Kind == 38))
            {
                Vector3 projected = client.Project(resource.Position);
                if (projected.z <= 0) continue;
                float distance = Vector2.Distance(new Vector2(projected.x, projected.y), pointer);
                if (distance < closest) { closest = distance; found = resource.Id; }
            }
            if (found == 0) return false;
            selectedId = found; oreScroll = Vector2.zero;
            flightHud.ClearSelection();
            return true;
        }

        /// <returns>The centered bounds of the selected resource panel.</returns>
        private Rect PanelRect(bool open)
        {
            float height = open ? 286 : 160;
            return new Rect((CanvasWidth - 370) * .5f, CanvasHeight - height - 122, 370, height);
        }

        /// <summary>Draw limited world labels and the selected asteroid's server-owned ore stock.</summary>
        public void Draw(Snapshot snapshot)
        {
            if (snapshot == null || snapshot.Station != null) return;
            if (sector != snapshot.Sector || Time.unscaledTime >= refreshedAt)
            {
                nearby = snapshot.Objects.Where(item => item.Kind == 38)
                    .OrderBy(item => item.Position.Distance(snapshot.Position)).Take(12).ToArray();
                sector = snapshot.Sector; refreshedAt = Time.unscaledTime + .25f;
            }
            if (selectedId != 0 && !snapshot.Objects.Any(item => item.Id == selectedId && item.Kind == 38))
                selectedId = 0;
            if (Event.current.type == EventType.Repaint) DrawLabels(snapshot);
            if (selectedId == 0) return;
            MapObject resource = snapshot.Objects.First(item => item.Id == selectedId);
            bool open = snapshot.ResourceWindowOpen && snapshot.ResourceTargetId == selectedId;
            Rect panel = PanelRect(open);
            flightHud.Panel(panel);
            Fill(new Rect(panel.x + 12, panel.y + 13, 5, 5), new Color(.25f, .95f, .85f));
            GUI.Label(new Rect(panel.x + 25, panel.y + 7, panel.width - 37, 18), "RESOURCE SCAN", flightHud.SmallStyle);
            GUI.Label(new Rect(panel.x + 12, panel.y + 27, panel.width - 24, 22),
                resource.Name.ToUpperInvariant(), flightHud.BodyStyle);
            float distance = resource.Position.Distance(snapshot.Position);
            ResourceView stock = snapshot.Resources.FirstOrDefault(item => item.Id == selectedId);
            string detail = $"LEVEL {stock?.Level ?? 0}   ·   {distance / 1000:0.00} KM";
            if (stock != null) detail += $"   ·   {stock.Fraction * 100:0}% REMAINING";
            GUI.Label(new Rect(panel.x + 12, panel.y + 53, panel.width - 24, 20), detail, flightHud.SmallStyle);
            GUI.Label(new Rect(panel.x + 12, panel.yMax - 25, panel.width - 24, 18), client.Status, flightHud.SmallStyle);
            if (!open)
            {
                if (GUI.Button(new Rect(panel.x + 12, panel.y + 79, panel.width - 24, 32),
                    "OPEN PROSPECTING WINDOW", flightHud.ButtonStyle)) client.OpenResource(selectedId);
                GUI.Label(new Rect(panel.x + 12, panel.y + 115, panel.width - 24, 18),
                    "Stop nearby. The server checks skill and range.", flightHud.SmallStyle);
                return;
            }
            if (GUI.Button(new Rect(panel.x + panel.width - 88, panel.y + 7, 76, 28), "CLOSE", flightHud.ButtonStyle))
                client.CloseResource();
            GUILayout.BeginArea(new Rect(panel.x + 12, panel.y + 79, panel.width - 24, panel.height - 116));
            oreScroll = GUILayout.BeginScrollView(oreScroll);
            if (stock == null) GUILayout.Label("Reading resource inventory…", flightHud.SmallStyle);
            else
            {
                ItemView[] items = stock.Items.Where(item => item != null).ToArray();
                if (items.Length == 0) GUILayout.Label("Resource depleted", flightHud.SmallStyle);
                foreach (ItemView item in items)
                    if (GUILayout.Button($"PROSPECT  {item.Name}  ×{item.Quantity}", flightHud.ButtonStyle, GUILayout.Height(34)))
                        client.ProspectResource(item.Slot);
            }
            GUILayout.EndScrollView(); GUILayout.EndArea();
        }

        /// <summary>Mark a bounded set of nearby mineable objects without covering ship contacts.</summary>
        private void DrawLabels(Snapshot snapshot)
        {
            foreach (MapObject resource in nearby)
            {
                Vector3 point = client.Project(resource.Position);
                point.x /= flightHud.Scale; point.y /= flightHud.Scale;
                if (point.z <= 0 || point.x < 320 || point.x > CanvasWidth - 335 ||
                    point.y < 100 || point.y > CanvasHeight - 160) continue;
                Color signal = selectedId == resource.Id ? new Color(1, .77f, .35f) :
                    new Color(.38f, .87f, .82f);
                Fill(new Rect(point.x - 5, point.y - 5, 10, 2), signal);
                Fill(new Rect(point.x - 5, point.y - 5, 2, 10), signal);
                Fill(new Rect(point.x + 9, point.y - 12, 172, 42), new Color(.005f, .018f, .032f, .86f));
                Fill(new Rect(point.x + 9, point.y - 12, 3, 42), signal);
                GUI.Label(new Rect(point.x + 18, point.y - 10, 160, 20), resource.Name, flightHud.BodyStyle);
                GUI.Label(new Rect(point.x + 18, point.y + 9, 160, 18),
                    "ORE   ·   " + (resource.Position.Distance(snapshot.Position) / 1000).ToString("0.0") + " km",
                    flightHud.SmallStyle);
            }
        }

        /// <summary>Draw a tinted mining marker without changing later GUI colors.</summary>
        private static void Fill(Rect rect, Color color)
        {
            Color previous = GUI.color;
            GUI.color = color; GUI.DrawTexture(rect, Texture2D.whiteTexture); GUI.color = previous;
        }
    }
}
