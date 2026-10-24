using System.Collections.Generic;
using System.Linq;
using Enb.Protocol;
using UnityEngine;
using CharacterRecord = Enb.Protocol.CharacterInfo;

namespace Enb.Client
{
    /// <summary>Own the rendered world and foreground input; networking has a separate owner.</summary>
    public sealed class SpaceClient : MonoBehaviour
    {
        public const float MetersToWorld = .01f;
        private ClientWorker worker;
        private CharacterPickerWorker picker;
        private CharacterRecord[] characters;
        private Snapshot snapshot;
        private MapObject[] renderedObjects = System.Array.Empty<MapObject>();
        private Camera view;
        private SpaceArt art;
        private RemoteShips remoteShips;
        private CombatEffects combatEffects;
        private FlightHud hud;
        private Transform ship, worldRoot, environment, hangar;
        private readonly Dictionary<uint, Transform> bodies = new Dictionary<uint, Transform>();
        private string host = "192.168.47.128", username = "toon", password = "pass", status = "Ready to connect", sector = "";
        private uint target;
        private bool chart, positioned;
        private bool chatOpen;
        private int chatOpenedFrame = -1;
        private int lastFrameRate = 120;
        private int sampledFrames, displayFps;
        private float sampledSeconds, slowestFrameMs, displayFrameMs, nextPerformanceLog;
        private float previousYaw, previousPitch, orbitYaw, orbitPitch, zoom = 2.8f, mapZoom = 1400;
        private Vector3 renderedPosition;
        private float authoritativePoseAt;
        public Snapshot CurrentSnapshot => snapshot;
        public string ConnectionError => worker?.Error;
        public string Status => status;
        public bool Connected => worker != null || picker != null;
        public bool ChoosingCharacter => picker != null && Characters != null;
        public bool PickingCharacter => picker != null;
        public CharacterRecord[] Characters => picker?.Characters ?? characters;
        public string LoginError => picker?.Error ?? worker?.Error;
        public bool Ready => worker != null && worker.Running && snapshot != null && snapshot.Ready;
        public bool Chart => chart;
        public MapObject Target => snapshot?.Objects.FirstOrDefault(item => item.Id == target);
        public int RenderedBodyCount => bodies.Count;
        public int Fps => displayFps;
        public float FrameMs => displayFrameMs;

        /// <summary>Create the space client when the bootstrap scene starts.</summary>
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Bootstrap()
        { if (FindFirstObjectByType<SpaceClient>() == null) new GameObject("42 and Beyond").AddComponent<SpaceClient>(); }

        /// <summary>Create lighting, reusable models, the camera and the flight HUD.</summary>
        private void Awake()
        {
            Application.runInBackground = true;
            QualitySettings.vSyncCount = 0;
            Application.targetFrameRate = 120;
            art = new SpaceArt(); hud = new FlightHud(this);
            worldRoot = new GameObject("Server world").transform; worldRoot.SetParent(transform, false);
            remoteShips = new RemoteShips(art, worldRoot);
            ship = art.Ship(transform); combatEffects = new CombatEffects(transform, ship, remoteShips); ship.localScale = Vector3.one * .25f;
            environment = art.Environment(transform);
            hangar = art.Hangar(transform); hangar.gameObject.SetActive(false);
            RenderSettings.skybox = art.Sky; RenderSettings.ambientLight = new Color(.18f, .23f, .32f);
            RenderSettings.ambientMode = UnityEngine.Rendering.AmbientMode.Flat;
            var sun = new GameObject("Sunlight", typeof(Light)); sun.transform.SetParent(transform, false);
            sun.transform.rotation = Quaternion.Euler(30, -35, 0);
            Light light = sun.GetComponent<Light>(); light.type = LightType.Directional; light.color = new Color(1, .83f, .65f); light.intensity = 1.7f;
            light.shadows = LightShadows.Soft; QualitySettings.shadowDistance = 150; QualitySettings.antiAliasing = 4;
            view = Camera.main;
            if (view == null) view = new GameObject("Flight camera", typeof(Camera)).GetComponent<Camera>();
            view.clearFlags = CameraClearFlags.Skybox; view.nearClipPlane = .015f; view.farClipPlane = 35000; view.fieldOfView = 60;
            view.transform.position = new Vector3(0, 1, -zoom); view.transform.LookAt(Vector3.zero);
        }

        /// <summary>Connect the owned local demo when invoked by the project launcher.</summary>
        private void Start()
        {
            string[] args = System.Environment.GetCommandLineArgs();
            if (System.Array.IndexOf(args, "--enb-local") >= 0)
                StartLocalSession("127.0.0.1", "local", "local");
            if (System.Array.IndexOf(args, "--enb-vm") >= 0)
                StartLocalSession("192.168.47.128", "toon", "pass");
        }

        /// <summary>Join the previous worker before replacing the connection.</summary>
        public void StartLocalSession(string address, string account, string secret)
        {
            Disconnect(); host = address; username = account; password = secret;
            try { LocalSession.LocalAddress(host); picker = new CharacterPickerWorker(host, username, password); status = "Checking account…"; }
            catch (System.ArgumentException error) { status = error.Message; }
        }

        /// <summary>Enter the selected server character after account lookup.</summary>
        public void ChooseCharacter(int slot)
        {
            CharacterRecord character = Characters?.FirstOrDefault(item => item.Slot == slot);
            if (!ChoosingCharacter || character == null) return;
            string name = character.Name;
            picker.Dispose(); picker = null;
            worker = new ClientWorker(host, username, password, slot); password = "";
            status = "Entering " + name + "…";
        }

        /// <summary>Return to the account form after a failed lookup or session.</summary>
        public void ReturnToLogin() { Disconnect(); }

        /// <returns>Unity-space coordinates in hundred-meter units.</returns>
        public static Vector3 Convert(Position position)
        { return new Vector3(position.Y, position.Z, position.X) * MetersToWorld; }

        /// <returns>A rotation preserving the server's forward and up axes.</returns>
        public static Quaternion ConvertRotation(float[] q)
        {
            var source = new Quaternion(q[0], q[1], q[2], q[3]);
            if (Quaternion.Dot(source, source) < .00001f) return Quaternion.identity;
            source = source.normalized; Vector3 forward = source * Vector3.right, up = source * Vector3.forward;
            return Quaternion.LookRotation(new Vector3(forward.y, forward.z, forward.x), new Vector3(up.y, up.z, up.x));
        }

        /// <returns>The current visual heading projected from native steering rates.</returns>
        private static Quaternion ProjectRotation(Snapshot state, float age)
        {
            Quaternion source = new Quaternion(state.Orientation[0], state.Orientation[1],
                state.Orientation[2], state.Orientation[3]).normalized;
            float yaw = state.AngularZ * 420f * age;
            float pitch = state.AngularY * 420f * age;
            source = (source * new Quaternion(0, 0, yaw, 1).normalized *
                new Quaternion(0, pitch, 0, 1).normalized).normalized;
            Vector3 forward = source * Vector3.right, up = source * Vector3.forward;
            return Quaternion.LookRotation(new Vector3(forward.y, forward.z, forward.x),
                new Vector3(up.y, up.z, up.x));
        }

        /// <summary>Consume snapshots and route keyboard and mouse actions.</summary>
        private void Update()
        {
            MeasureFrame();
            int frameRate = !Application.isFocused ? 60 : 120;
            if (frameRate != lastFrameRate)
            {
                Application.targetFrameRate = frameRate;
                lastFrameRate = frameRate;
            }
            if (picker?.Characters != null)
            {
                characters = picker.Characters;
                status = "Choose a character";
            }
            if (picker?.Error != null) status = picker.Error;
            if (worker?.Error != null) status = worker.Error;
            Snapshot next = worker?.Latest;
            if (next != null && !ReferenceEquals(next, snapshot))
            {
                bool poseChanged = next.HasPosition && (snapshot == null || !snapshot.HasPosition ||
                    next.Position.Distance(snapshot.Position) > .001f || next.Speed != snapshot.Speed ||
                    next.AngularY != snapshot.AngularY || next.AngularZ != snapshot.AngularZ ||
                    !next.Orientation.SequenceEqual(snapshot.Orientation));
                snapshot = next; if (worker.Error == null) status = next.Status;
                if (sector != next.Sector) { ClearWorld(); sector = next.Sector; target = 0; positioned = false; }
                ApplyWorld(next);
                remoteShips.Apply(next.Players.Concat(next.Mobs).ToArray());
                combatEffects.Apply(next);
                if (poseChanged) authoritativePoseAt = Time.unscaledTime;
                if (!positioned && next.HasPosition) { renderedPosition = Convert(next.Position); positioned = true; }
            }
            if (!Ready) return;
            if (Input.GetKeyDown(KeyCode.Return) && !chatOpen)
            {
                chatOpen = true;
                chatOpenedFrame = Time.frameCount;
            }
            if (Input.GetKeyDown(KeyCode.Escape)) chatOpen = false;
            if (chatOpen || hud.InputFocused)
            {
                if (previousYaw != 0 || previousPitch != 0) worker.Submit(new ClientCommand(CommandKind.Steer));
                previousYaw = previousPitch = 0;
                return;
            }
            if (snapshot.Station != null)
            {
                if (Input.GetKeyDown(KeyCode.E)) hud.ToggleEquipment();
                if (Input.GetKeyDown(KeyCode.L)) Launch();
                return;
            }
            if (Input.GetKeyDown(KeyCode.Tab)) SetChart(!chart);
            if (Input.GetKeyDown(KeyCode.E)) hud.ToggleEquipment();
            if (Input.GetKeyDown(KeyCode.X))
            {
                if (snapshot.WeaponsFiring) StopWeapons();
                else ToggleWeapons(hud.SelectedContactId);
            }
            if (Input.GetKeyDown(KeyCode.W)) Thrust(2);
            if (Input.GetKeyDown(KeyCode.S)) Thrust(3);
            if (Input.GetKeyDown(KeyCode.Space)) Thrust(4);
            if (Input.GetKeyDown(KeyCode.F)) WarpSelected();
            if (Input.GetKeyDown(KeyCode.G)) GateSelected();
            if (Input.GetKeyDown(KeyCode.D) &&
                (Input.GetKey(KeyCode.LeftShift) || Input.GetKey(KeyCode.RightShift))) DockSelected();
            if (Input.GetKeyDown(KeyCode.C)) { orbitYaw = 0; orbitPitch = 0; }
            float yaw = (Input.GetKey(KeyCode.A) ? 1 : 0) - (Input.GetKey(KeyCode.D) ? 1 : 0);
            float pitch = (Input.GetKey(KeyCode.UpArrow) ? 1 : 0) - (Input.GetKey(KeyCode.DownArrow) ? 1 : 0);
            if (yaw != previousYaw || pitch != previousPitch)
            { worker.Submit(new ClientCommand(CommandKind.Steer, yaw: yaw, pitch: pitch)); previousYaw = yaw; previousPitch = pitch; }
            if (!hud.ContainsPointer())
            {
                if (Input.GetMouseButton(1) && !chart)
                { orbitYaw += Input.GetAxis("Mouse X") * 3; orbitPitch = Mathf.Clamp(orbitPitch - Input.GetAxis("Mouse Y") * 3, -65, 65); }
                float wheel = Input.mouseScrollDelta.y;
                if (chart) mapZoom = Mathf.Clamp(mapZoom * Mathf.Exp(-wheel * .12f), 100, 4000);
                else zoom = Mathf.Clamp(zoom * Mathf.Exp(-wheel * .1f), 1.5f, 14);
                if (Input.GetMouseButtonDown(0)) SelectAtPointer();
            }
        }

        /// <summary>Publish one-second frame pacing measurements for the HUD.</summary>
        private void MeasureFrame()
        {
            float delta = Time.unscaledDeltaTime;
            if (delta <= 0 || delta > 1) return;
            sampledSeconds += delta; sampledFrames++;
            slowestFrameMs = Mathf.Max(slowestFrameMs, delta * 1000);
            if (sampledSeconds < 1) return;
            displayFps = Mathf.RoundToInt(sampledFrames / sampledSeconds);
            displayFrameMs = slowestFrameMs;
            sampledFrames = 0; sampledSeconds = slowestFrameMs = 0;
            if (Time.unscaledTime >= nextPerformanceLog)
            {
                Debug.Log($"42 and Beyond frame pacing: {displayFps} FPS, slowest {displayFrameMs:0.0} ms, focused {Application.isFocused}, contacts {remoteShips.Count}");
                nextPerformanceLog = Time.unscaledTime + 30;
            }
        }

        /// <summary>Create visible scenery using the server's positions, rotations and scale.</summary>
        private void ApplyWorld(Snapshot next)
        {
            if (renderedObjects.SequenceEqual(next.Objects)) return;
            renderedObjects = next.Objects;
            var ids = new HashSet<uint>();
            foreach (MapObject item in next.Objects)
            {
                ids.Add(item.Id);
                if (!bodies.TryGetValue(item.Id, out Transform body))
                { body = art.WorldObject(worldRoot, item); if (body != null) bodies[item.Id] = body; }
                if (body != null) { body.localPosition = Convert(item.Position); body.localRotation = ConvertRotation(item.Orientation); }
            }
            foreach (uint id in bodies.Keys.Where(id => !ids.Contains(id)).ToArray())
            { Destroy(bodies[id].gameObject); bodies.Remove(id); }
            if (target == 0 || !ids.Contains(target))
                target = next.Objects.Where(item => item.Navigable).OrderBy(item => item.Position.Distance(next.Position)).FirstOrDefault()?.Id ?? 0;
        }

        /// <summary>Smooth authoritative poses and position the camera without changing gameplay state.</summary>
        private void LateUpdate()
        {
            float delta = Mathf.Min(Time.unscaledDeltaTime, .1f);
            float blend = 1 - Mathf.Exp(-14 * delta);
            bool docked = snapshot?.Station != null;
            if (snapshot != null && !docked)
            {
                float age = Mathf.Clamp(Time.unscaledTime - authoritativePoseAt, 0, .25f);
                Quaternion projectedRotation = ProjectRotation(snapshot, age);
                ship.rotation = Quaternion.Slerp(ship.rotation, projectedRotation, blend);
                float ramp = snapshot.Acceleration > .001f
                    ? Mathf.Min(age, Mathf.Abs(snapshot.TargetSpeed - snapshot.Speed) / snapshot.Acceleration) : 0;
                float direction = Mathf.Sign(snapshot.TargetSpeed - snapshot.Speed);
                float currentSpeed = snapshot.Speed + direction * snapshot.Acceleration * ramp;
                float distance = snapshot.Speed * ramp +
                    .5f * direction * snapshot.Acceleration * ramp * ramp + currentSpeed * (age - ramp);
                Vector3 velocity = ship.forward * (currentSpeed * MetersToWorld);
                Vector3 predicted = Convert(snapshot.Position) +
                    projectedRotation * Vector3.forward * (distance * MetersToWorld);
                renderedPosition += velocity * delta;
                renderedPosition = Vector3.Distance(renderedPosition, predicted) > 50
                    ? predicted : Vector3.Lerp(renderedPosition, predicted, 1 - Mathf.Exp(-10 * delta));
            }
            worldRoot.position = -renderedPosition;
            remoteShips.Tick(Time.unscaledDeltaTime); combatEffects.Tick();
            hangar.gameObject.SetActive(docked);
            environment.gameObject.SetActive(!chart && !docked);
            if (docked)
            {
                view.orthographic = false; view.clearFlags = CameraClearFlags.SolidColor;
                view.backgroundColor = new Color(.025f, .055f, .075f);
                ship.position = new Vector3(0, 1.4f, 0); ship.rotation = Quaternion.identity;
                ship.localScale = Vector3.one * 1.3f;
                view.transform.position = new Vector3(4, 3.5f, -8);
                view.transform.LookAt(new Vector3(0, 1.2f, 0));
                return;
            }
            ship.localPosition = Vector3.zero;
            if (chart)
            {
                view.orthographic = true; view.orthographicSize = mapZoom;
                view.clearFlags = CameraClearFlags.SolidColor; view.backgroundColor = new Color(.008f, .018f, .032f);
                view.transform.position = new Vector3(0, 5000, 0); view.transform.rotation = Quaternion.Euler(90, 0, 0);
                ship.localScale = Vector3.one * mapZoom * .008f;
            }
            else
            {
                view.orthographic = false; view.clearFlags = CameraClearFlags.Skybox; ship.localScale = Vector3.one * .25f;
                Quaternion orbit = ship.rotation * Quaternion.Euler(orbitPitch, orbitYaw, 0);
                view.transform.position = orbit * new Vector3(0, zoom * .24f, -zoom);
                view.transform.LookAt(ship.position + ship.forward * .3f, orbit * Vector3.up);
            }
        }

        /// <summary>Select the closest projected navigation target under the pointer.</summary>
        private void SelectAtPointer()
        {
            if (snapshot == null) return; float best = 35; uint selected = 0;
            Vector2 pointer = new Vector2(Input.mousePosition.x, Screen.height - Input.mousePosition.y);
            if (hud.SelectContactAtPointer(snapshot, pointer) || hud.SelectWreckAtPointer(snapshot, pointer)) return;
            foreach (MapObject item in snapshot.Objects.Where(item => item.Navigable))
            {
                Vector3 point = Project(item.Position); if (point.z <= 0) continue;
                float distance = Vector2.Distance(new Vector2(point.x, point.y), new Vector2(Input.mousePosition.x, Screen.height - Input.mousePosition.y));
                if (distance < best) { best = distance; selected = item.Id; }
            }
            if (selected != 0) target = selected;
        }

        /// <returns>A projected position in GUI coordinates with signed camera depth.</returns>
        public Vector3 Project(Position position)
        { Vector3 point = view.WorldToScreenPoint(Convert(position) - renderedPosition); point.y = Screen.height - point.y; return point; }

        /// <returns>A remote ship's interpolated GUI position, or a negative depth when absent.</returns>
        public Vector3 ProjectPlayer(uint id)
        {
            if (!remoteShips.TryPosition(id, out Vector3 position)) return new Vector3(0, 0, -1);
            Vector3 point = view.WorldToScreenPoint(position);
            point.y = Screen.height - point.y;
            return point;
        }

        /// <summary>Select an existing navigable server object.</summary>
        public void Select(uint id)
        { if (snapshot != null && snapshot.Objects.Any(item => item.Id == id && item.Navigable)) target = id; }

        /// <summary>Request a server-authoritative warp to the selected target.</summary>
        public void WarpSelected() { if (Ready && Target != null) worker.Submit(new ClientCommand(CommandKind.Warp, target)); }

        /// <summary>Request travel through the selected gate.</summary>
        public void GateSelected() { if (Ready && Target?.Kind == 11) worker.Submit(new ClientCommand(CommandKind.Gate, target)); }

        /// <summary>Request docking at the selected nearby station.</summary>
        public void DockSelected()
        { if (Ready && Target?.Kind == 12 && Target.Position.Distance(snapshot.Position) <= 5000)
                worker.Submit(new ClientCommand(CommandKind.Dock, target)); }

        /// <summary>Leave the station after its server-owned launch transition.</summary>
        public void Launch()
        { if (Ready && snapshot.Station != null) worker.Submit(new ClientCommand(CommandKind.Launch)); }

        /// <summary>Ask the server to open a contact placed in this station.</summary>
        public void OpenNpc(uint id)
        { if (Ready && snapshot.Station != null && snapshot.Station.NpcIds.Contains(id))
                worker.Submit(new ClientCommand(CommandKind.OpenNpc, id)); }

        /// <summary>Send one server-offered dialogue branch or close command.</summary>
        public void ChooseNpc(byte node)
        { if (Ready && snapshot.Dialogue != null)
                worker.Submit(new ClientCommand(CommandKind.ChooseNpc, node)); }

        /// <summary>Buy one item from a server-listed station vendor slot.</summary>
        public void BuyVendor(int slot)
        { if (Ready && snapshot.Dialogue?.VendorOpen == true)
                worker.Submit(new ClientCommand(CommandKind.BuyVendor, slot: slot)); }

        /// <summary>Sell one item from a cargo slot through the active vendor.</summary>
        public void SellVendor(int slot)
        { if (Ready && snapshot.Dialogue?.VendorOpen == true)
                worker.Submit(new ClientCommand(CommandKind.SellVendor, slot: slot)); }

        /// <summary>Install a cargo item in the selected ship hardpoint.</summary>
        public void EquipCargo(int cargoSlot, int mountSlot)
        {
            if (Ready && cargoSlot >= 0 && cargoSlot < snapshot.Cargo.Length &&
                mountSlot >= 0 && mountSlot < snapshot.Mounted.Length)
                worker.Submit(new ClientCommand(CommandKind.EquipCargo, (uint)cargoSlot, slot: mountSlot));
        }

        /// <summary>Remove an installed item into an empty cargo slot.</summary>
        public void UnequipMount(int mountSlot)
        {
            if (Ready && mountSlot >= 0 && mountSlot < snapshot.Mounted.Length)
                worker.Submit(new ClientCommand(CommandKind.UnequipMount, (uint)mountSlot));
        }

        /// <summary>Request a server-authoritative formation change or follow action.</summary>
        public void Formation(uint action)
        { if (Ready) worker.Submit(new ClientCommand(CommandKind.Formation, action)); }

        /// <summary>Open a visible wreck through the server's loot protocol.</summary>
        public void OpenLoot(uint id)
        {
            if (Ready && snapshot.Objects.Any(item => item.Id == id && item.Kind == 25))
                worker.Submit(new ClientCommand(CommandKind.OpenLoot, id));
        }

        /// <summary>Tractor one server-owned wreck slot into cargo.</summary>
        public void TakeLoot(uint id, int slot)
        {
            if (Ready && snapshot.OpenLootId == id)
                worker.Submit(new ClientCommand(CommandKind.TakeLoot, id, slot: slot));
        }

        /// <summary>Set the authoritative target of a visible contact.</summary>
        public void SelectContact(uint id)
        {
            if (Ready && snapshot.Players.Concat(snapshot.Mobs).Any(actor => actor.Id == id))
                worker.Submit(new ClientCommand(CommandKind.SelectTarget, id));
        }

        /// <summary>Stop weapons without requiring a surviving target.</summary>
        public void StopWeapons()
        { if (Ready && snapshot.WeaponsFiring) worker.Submit(new ClientCommand(CommandKind.StopWeapons)); }

        /// <summary>Request server-selected combat against one visible MOB.</summary>
        public void ToggleWeapons(uint id)
        {
            if (Ready && snapshot.Mobs.Any(mob => mob.Id == id && mob.HasPosition))
                worker.Submit(new ClientCommand(CommandKind.ToggleWeapons, id));
        }

        /// <summary>Submit one explicit throttle or brake command.</summary>
        public void Thrust(byte command) { if (Ready && snapshot.Station == null) worker.Submit(new ClientCommand(CommandKind.Thrust, command)); }

        /// <summary>Send one chat line or local developer command to the server.</summary>
        public void SendChat(string text, bool group = false)
        { if (Ready) worker.Submit(new ClientCommand(CommandKind.Chat, target: group ? 1u : 4u, text: text)); }

        /// <summary>Whether the local chat entry is open.</summary>
        public bool ChatOpen => chatOpen;
        public int ChatOpenedFrame => chatOpenedFrame;
        public void CloseChatEntry() { chatOpen = false; }

        /// <summary>Switch between the flight camera and sector chart.</summary>
        public void SetChart(bool enabled) { chart = enabled; }

        /// <summary>Draw the HUD on the main thread.</summary>
        private void OnGUI()
        {
            Matrix4x4 previous = GUI.matrix;
            float scale = hud.Scale;
            GUI.matrix = Matrix4x4.Scale(new Vector3(scale, scale, 1));
            try { hud.Draw(ref host, ref username, ref password); }
            finally { GUI.matrix = previous; }
        }

        /// <summary>Join the worker before removing the connected world's resources.</summary>
        public void Disconnect()
        {
            worker?.Dispose(); worker = null; snapshot = null; sector = ""; target = 0; positioned = false; authoritativePoseAt = 0;
            picker?.Dispose(); picker = null; characters = null;
            previousYaw = previousPitch = 0; ClearWorld(); status = "Disconnected";
        }

        /// <summary>Release scene instances belonging to the departed sector.</summary>
        private void ClearWorld() { combatEffects?.Clear(); remoteShips?.Clear(); foreach (Transform body in bodies.Values) Destroy(body.gameObject); bodies.Clear(); renderedObjects = System.Array.Empty<MapObject>(); }

        /// <summary>Brake and neutralize steering when foreground control is lost.</summary>
        private void OnApplicationFocus(bool focused)
        {
            Application.targetFrameRate = focused ? 120 : 60;
            if (focused || worker == null || snapshot?.Station != null) return;
            worker.Submit(new ClientCommand(CommandKind.Thrust, 4)); worker.Submit(new ClientCommand(CommandKind.Steer));
            previousYaw = previousPitch = 0;
        }

        /// <summary>Stop networking before releasing shared rendering assets.</summary>
        private void OnDestroy() { worker?.Dispose(); worker = null; picker?.Dispose(); picker = null; combatEffects?.Dispose(); art?.Dispose(); hud?.Dispose(); }
    }
}
