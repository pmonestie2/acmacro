using System.Collections.Generic;
using Enb.Protocol;
using UnityEngine;

namespace Enb.Client
{
    /// <summary>Show brief procedural weapon streaks from server shot events.</summary>
    public sealed class CombatEffects
    {
        private sealed class Beam
        {
            public readonly uint Source, Target;
            public readonly LineRenderer Line;
            public readonly float Started, Lifetime;
            /// <summary>Store one beam and its expiry.</summary>
            public Beam(uint source, uint target, LineRenderer line, float lifetime)
            { Source = source; Target = target; Line = line; Started = Time.unscaledTime; Lifetime = lifetime; }
        }
        private readonly List<Beam> beams = new List<Beam>();
        private readonly Transform parent, player;
        private readonly RemoteShips ships;
        private readonly Material material;
        private long lastSequence;
        private uint playerId;

        /// <summary>Share visible ship positions while owning a single beam material.</summary>
        public CombatEffects(Transform parent, Transform player, RemoteShips ships)
        {
            this.parent = parent; this.player = player; this.ships = ships;
            Shader shader = Shader.Find("Sprites/Default") ?? Shader.Find("Hidden/Internal-Colored");
            material = new Material(shader);
        }

        /// <summary>Spawn only newly received server effects.</summary>
        public void Apply(Snapshot snapshot)
        {
            playerId = snapshot.PlayerId;
            foreach (ShotEvent shot in snapshot.Shots)
            {
                if (shot.Sequence <= lastSequence) continue;
                lastSequence = shot.Sequence;
                if (!TryPoint(shot.Source, out _) || !TryPoint(shot.Target, out _)) continue;
                var model = new GameObject("Weapon effect", typeof(LineRenderer));
                model.transform.SetParent(parent, false);
                var line = model.GetComponent<LineRenderer>();
                line.sharedMaterial = material; line.positionCount = 2;
                line.useWorldSpace = true; line.numCapVertices = 4;
                line.widthMultiplier = .028f;
                Color color = shot.Source == playerId ? new Color(.3f, .9f, 1f) : new Color(1f, .43f, .2f);
                line.startColor = color; line.endColor = color;
                beams.Add(new Beam(shot.Source, shot.Target, line, Mathf.Clamp(shot.Duration / 1000f, .18f, .75f)));
                if (beams.Count > 32) Remove(0);
            }
        }

        /// <returns>The rendered world position of a visible actor.</returns>
        private bool TryPoint(uint id, out Vector3 position)
        {
            if (id == playerId) { position = player.position; return true; }
            return ships.TryPosition(id, out position);
        }

        /// <summary>Track moving endpoints and fade expired effects.</summary>
        public void Tick()
        {
            for (int i = beams.Count - 1; i >= 0; i--)
            {
                Beam beam = beams[i]; float age = Time.unscaledTime - beam.Started;
                if (age >= beam.Lifetime || !TryPoint(beam.Source, out Vector3 from) || !TryPoint(beam.Target, out Vector3 to))
                { Remove(i); continue; }
                beam.Line.SetPosition(0, from); beam.Line.SetPosition(1, to);
                float fade = 1 - age / beam.Lifetime;
                beam.Line.widthMultiplier = .028f * fade;
            }
        }

        /// <summary>Destroy one expired effect.</summary>
        private void Remove(int index)
        { Object.Destroy(beams[index].Line.gameObject); beams.RemoveAt(index); }

        /// <summary>Clear effects on travel or logout.</summary>
        public void Clear()
        { for (int i = beams.Count - 1; i >= 0; i--) Remove(i); lastSequence = 0; playerId = 0; }

        /// <summary>Release the shared line material after all effects are gone.</summary>
        public void Dispose() { Clear(); Object.Destroy(material); }
    }
}
