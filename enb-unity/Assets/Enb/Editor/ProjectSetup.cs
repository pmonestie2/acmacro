using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine.SceneManagement;

namespace Enb.Editor
{
    [InitializeOnLoad]
    public static class ProjectSetup
    {
        /// <summary>Create the initial playable scene after the first import.</summary>
        static ProjectSetup() { EditorApplication.delayCall += EnsureScene; }

        /// <summary>Generate only missing bootstrap assets without replacing existing scenes.</summary>
        [MenuItem("ENB/Create 3D bootstrap scene")]
        public static void EnsureScene()
        {
            const string path = "Assets/Enb/Space.unity";
            if (System.IO.File.Exists(path) || EditorApplication.isPlayingOrWillChangePlaymode) return;
            Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
            EditorSceneManager.SaveScene(scene, path);
            EditorSceneManager.CloseScene(scene, true);
            EditorBuildSettings.scenes = new[] { new EditorBuildSettingsScene(path, true) };
        }
    }
}
