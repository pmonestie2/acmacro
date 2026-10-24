using System.Linq;
using Enb.Protocol;
using UnityEngine;

namespace Enb.Client
{
    /// <summary>Present the server's rescue choices when the local ship is incapacitated.</summary>
    public sealed class RecoveryHud
    {
        private readonly SpaceClient client;
        private readonly FlightHud flightHud;
        private float CanvasWidth => Screen.width / flightHud.Scale;
        private float CanvasHeight => Screen.height / flightHud.Scale;

        /// <summary>Share the foreground controller and its visual styles.</summary>
        public RecoveryHud(SpaceClient client, FlightHud flightHud)
        { this.client = client; this.flightHud = flightHud; }

        /// <summary>Draw server-offered tow and distress actions over the flight view.</summary>
        public void Draw(Snapshot snapshot)
        {
            if (snapshot == null || snapshot.Station != null || snapshot.MaxHull <= 0 || snapshot.Hull > 0) return;
            Rect panel = new Rect((CanvasWidth - 500) * .5f, (CanvasHeight - 320) * .5f, 500, 320);
            flightHud.Panel(panel);
            GUI.Label(new Rect(panel.x + 20, panel.y + 15, 450, 27),
                "SHIP INCAPACITATED", flightHud.BodyStyle);
            DialogueState dialogue = snapshot.Dialogue;
            GUI.Label(new Rect(panel.x + 20, panel.y + 47, 455, 39),
                dialogue?.Text ?? "Your ship cannot fly. Contact rescue control or wait for another pilot.",
                flightHud.SmallStyle);
            if (dialogue == null)
            {
                if (GUI.Button(new Rect(panel.x + 20, panel.y + 99, 460, 39),
                    "REQUEST RESCUE OPTIONS", flightHud.ButtonStyle)) client.RequestRescue();
            }
            else
            {
                int index = 0;
                foreach (DialogueChoice choice in dialogue.Choices.Take(3))
                {
                    if (GUI.Button(new Rect(panel.x + 20, panel.y + 95 + index * 43, 460, 37),
                        choice.Text, flightHud.ButtonStyle)) client.ChooseRescue(choice.Node);
                    index++;
                }
            }
            if (snapshot.AvailableAbilities.Contains(65u))
            {
                bool charging = snapshot.CastingAbilityId == 65u && snapshot.CastRemainingSeconds > 0;
                GUI.enabled = !charging;
                string label = charging ? $"JUMPSTART CHARGING  ·  {snapshot.CastRemainingSeconds:0.#} S" :
                    "SELF JUMPSTART  ·  USE REACTOR ENERGY";
                if (GUI.Button(new Rect(panel.x + 20, panel.yMax - 77, 460, 37),
                    label, flightHud.ButtonStyle)) client.SelfJumpstart();
                GUI.enabled = true;
            }
            GUI.Label(new Rect(panel.x + 20, panel.yMax - 29, 460, 20), client.Status,
                flightHud.SmallStyle);
        }
    }
}
