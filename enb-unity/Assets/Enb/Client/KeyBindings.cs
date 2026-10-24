using System;
using UnityEngine;

namespace Enb.Client
{
    /// <summary>Keyboard actions that can be rebound from the in-game controls panel.</summary>
    public enum ControlAction
    {
        OpenChat, Forward, Reverse, TurnLeft, TurnRight, PitchUp, PitchDown, Brake,
        Warp, Fire, NextHostile, Follow, Gate, Dock, Launch, Chart, Equipment, ResetCamera,
        AbilityOne, AbilityTwo, AbilityThree, AbilityFour, AbilityFive, AbilitySix
    }

    /// <summary>Persist one unique keyboard key for each flight action.</summary>
    public sealed class KeyBindings
    {
        private const string PreferencePrefix = "42andBeyond.key.";
        private static readonly KeyCode[] Defaults =
        {
            KeyCode.Return, KeyCode.W, KeyCode.S, KeyCode.A, KeyCode.D,
            KeyCode.UpArrow, KeyCode.DownArrow, KeyCode.Space, KeyCode.Q, KeyCode.F,
            KeyCode.T, KeyCode.R, KeyCode.G, KeyCode.O, KeyCode.L,
            KeyCode.Tab, KeyCode.E, KeyCode.C,
            KeyCode.Alpha1, KeyCode.Alpha2, KeyCode.Alpha3,
            KeyCode.Alpha4, KeyCode.Alpha5, KeyCode.Alpha6
        };
        public static readonly ControlAction[] Actions = (ControlAction[])Enum.GetValues(typeof(ControlAction));
        private readonly KeyCode[] keys = (KeyCode[])Defaults.Clone();

        /// <summary>Load valid saved keys while keeping every action reachable.</summary>
        public KeyBindings()
        {
            foreach (ControlAction action in Actions)
            {
                int index = (int)action;
                int stored = PlayerPrefs.GetInt(PreferencePrefix + action, (int)Defaults[index]);
                KeyCode key = (KeyCode)stored;
                if (!Enum.IsDefined(typeof(KeyCode), key) || key == KeyCode.None || key == KeyCode.Escape)
                    continue;
                int duplicate = Array.IndexOf(keys, key);
                if (duplicate >= 0 && duplicate != index) keys[duplicate] = keys[index];
                keys[index] = key;
            }
        }

        /// <returns>KeyCode: The assigned key for a flight action.</returns>
        public KeyCode Get(ControlAction action) { return keys[(int)action]; }

        /// <returns>bool: Whether the assigned key was pressed this frame.</returns>
        public bool Pressed(ControlAction action) { return Input.GetKeyDown(Get(action)); }

        /// <returns>bool: Whether the assigned key is being held.</returns>
        public bool Held(ControlAction action) { return Input.GetKey(Get(action)); }

        /// <returns>string: A compact label for the assigned key.</returns>
        public string Display(ControlAction action)
        {
            KeyCode key = Get(action);
            if (key == KeyCode.Return) return "Enter";
            if (key == KeyCode.Space) return "Space";
            if (key == KeyCode.UpArrow) return "↑";
            if (key == KeyCode.DownArrow) return "↓";
            return key.ToString();
        }

        /// <returns>string: The readable action name shown in the controls panel.</returns>
        public static string Label(ControlAction action)
        {
            switch (action)
            {
                case ControlAction.OpenChat: return "Open chat";
                case ControlAction.Forward: return "Forward thrust";
                case ControlAction.Reverse: return "Reverse thrust";
                case ControlAction.TurnLeft: return "Turn left";
                case ControlAction.TurnRight: return "Turn right";
                case ControlAction.PitchUp: return "Pitch up";
                case ControlAction.PitchDown: return "Pitch down";
                case ControlAction.Brake: return "Brake / cancel follow";
                case ControlAction.Warp: return "Warp to destination";
                case ControlAction.Fire: return "Fire / stop weapons";
                case ControlAction.NextHostile: return "Next hostile target";
                case ControlAction.Follow: return "Follow target";
                case ControlAction.Gate: return "Activate gate";
                case ControlAction.Dock: return "Dock at station";
                case ControlAction.Launch: return "Launch from station";
                case ControlAction.Chart: return "Sector chart";
                case ControlAction.Equipment: return "Equipment panel";
                case ControlAction.ResetCamera: return "Reset camera";
                case ControlAction.AbilityOne: return "Ability slot 1";
                case ControlAction.AbilityTwo: return "Ability slot 2";
                case ControlAction.AbilityThree: return "Ability slot 3";
                case ControlAction.AbilityFour: return "Ability slot 4";
                case ControlAction.AbilityFive: return "Ability slot 5";
                case ControlAction.AbilitySix: return "Ability slot 6";
                default: throw new ArgumentOutOfRangeException(nameof(action));
            }
        }

        /// <summary>Assign a key, swapping with its previous action to avoid conflicts.</summary>
        public void Bind(ControlAction action, KeyCode key)
        {
            if (!Enum.IsDefined(typeof(KeyCode), key) || key == KeyCode.None || key == KeyCode.Escape)
                throw new ArgumentOutOfRangeException(nameof(key));
            int index = (int)action;
            int duplicate = Array.IndexOf(keys, key);
            if (duplicate >= 0 && duplicate != index)
            {
                keys[duplicate] = keys[index];
                PlayerPrefs.SetInt(PreferencePrefix + Actions[duplicate], (int)keys[duplicate]);
            }
            keys[index] = key;
            PlayerPrefs.SetInt(PreferencePrefix + action, (int)key);
            PlayerPrefs.Save();
        }

        /// <summary>Restore and save the default keyboard layout.</summary>
        public void Reset()
        {
            Array.Copy(Defaults, keys, keys.Length);
            foreach (ControlAction action in Actions)
                PlayerPrefs.SetInt(PreferencePrefix + action, (int)keys[(int)action]);
            PlayerPrefs.Save();
        }
    }
}
