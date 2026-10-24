using System.Collections.Generic;
using Enb.Protocol;
using UnityEngine;

namespace Enb.Client
{
    /// <summary>Own and interpolate visible remote ships beneath the floating world origin.</summary>
    public sealed class RemoteShips
    {
        private sealed class View
        {
            public readonly Transform Model;
            public ShipActor State;
            public Vector3 Destination;
            public Vector3 Velocity;
            public Quaternion Orientation;
            public float PoseAt;

            /// <summary>Place a new ship immediately at its first authoritative pose.</summary>
            public View(Transform model, ShipActor state)
            {
                Model = model;
                Apply(state);
                Model.localPosition = Destination;
                Model.localRotation = Orientation;
            }

            /// <summary>Retain the latest pose for foreground interpolation.</summary>
            public void Apply(ShipActor state)
            {
                Vector3 next = SpaceClient.Convert(state.Position);
                if (State != null && Vector3.Distance(next, Destination) > .0001f)
                {
                    float elapsed = Mathf.Max(.04f, Time.unscaledTime - PoseAt);
                    Vector3 estimate = (next - Destination) / elapsed;
                    Velocity = estimate.magnitude <= 50 ? estimate : Vector3.zero;
                    PoseAt = Time.unscaledTime;
                }
                else if (State == null) PoseAt = Time.unscaledTime;
                State = state;
                Destination = next;
                Orientation = SpaceClient.ConvertRotation(state.Orientation);
            }
        }

        private readonly Dictionary<uint, View> ships = new Dictionary<uint, View>();
        private readonly HashSet<uint> visible = new HashSet<uint>();
        private readonly List<uint> removed = new List<uint>();
        private readonly SpaceArt art;
        private readonly Transform parent;
        public int Count => ships.Count;

        /// <summary>Share the scene's mesh and material owner.</summary>
        public RemoteShips(SpaceArt art, Transform parent)
        { this.art = art; this.parent = parent; }

        /// <summary>Reconcile server visibility without recreating ships on movement packets.</summary>
        public void Apply(ShipActor[] players)
        {
            visible.Clear();
            foreach (ShipActor player in players)
            {
                if (!player.HasPosition) continue;
                visible.Add(player.Id);
                if (!ships.TryGetValue(player.Id, out View view))
                {
                    Transform model = player.IsPlayer ? art.Ship(parent) : art.Mob(parent);
                    model.localScale = Vector3.one * .25f * Mathf.Clamp(player.Scale, .05f, 100);
                    view = new View(model, player);
                    ships.Add(player.Id, view);
                }
                else if (!ReferenceEquals(view.State, player)) view.Apply(player);
                view.Model.name = player.Name;
            }
            removed.Clear();
            foreach (uint id in ships.Keys) if (!visible.Contains(id)) removed.Add(id);
            foreach (uint id in removed)
            {
                Object.Destroy(ships[id].Model.gameObject);
                ships.Remove(id);
            }
        }

        /// <summary>Smooth network poses and snap long teleports instead of flying across a sector.</summary>
        public void Tick(float deltaTime)
        {
            float blend = 1 - Mathf.Exp(-12 * deltaTime);
            foreach (View view in ships.Values)
            {
                Transform model = view.Model;
                float age = Time.unscaledTime - view.PoseAt;
                Vector3 predicted = view.Destination +
                    (age < .35f ? view.Velocity * Mathf.Min(age, .25f) : Vector3.zero);
                model.localPosition = Vector3.Distance(model.localPosition, predicted) > 300
                    ? predicted : Vector3.Lerp(model.localPosition, predicted, blend);
                model.localRotation = Quaternion.Slerp(model.localRotation, view.Orientation, blend);
            }
        }

        /// <returns>The rendered ship position when the actor has a visible model.</returns>
        public bool TryPosition(uint id, out Vector3 position)
        {
            if (ships.TryGetValue(id, out View view)) { position = view.Model.position; return true; }
            position = default; return false;
        }

        /// <summary>Remove departed-sector ships while retaining shared rendering assets.</summary>
        public void Clear()
        {
            foreach (View view in ships.Values) Object.Destroy(view.Model.gameObject);
            ships.Clear(); visible.Clear(); removed.Clear();
        }
    }
}
