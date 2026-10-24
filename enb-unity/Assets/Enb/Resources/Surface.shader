Shader "ENB/Surface"
{
    Properties {
        _Color ("Color", Color) = (0.5,0.5,0.5,1)
        _EmissionColor ("Emission", Color) = (0,0,0,1)
        _Metallic ("Metal", Range(0,1)) = 0
        _Glossiness ("Smoothness", Range(0,1)) = 0.3
        _Grain ("Rock grain", Range(0,1)) = 0
        _Panels ("Hull plating", Range(0,1)) = 0
    }
    SubShader {
        Tags { "RenderType"="Opaque" }
        CGPROGRAM
        #pragma surface surf Standard fullforwardshadows vertex:vert
        #pragma target 3.0
        fixed4 _Color, _EmissionColor;
        half _Metallic, _Glossiness, _Grain, _Panels;
        struct Input { float3 localPosition; };
        void vert(inout appdata_full v, out Input o) { UNITY_INITIALIZE_OUTPUT(Input,o); o.localPosition=v.vertex.xyz; }
        void surf(Input i, inout SurfaceOutputStandard o) {
            float n=sin(i.localPosition.x*37+sin(i.localPosition.y*29))*sin(i.localPosition.z*31+i.localPosition.y*11);
            float3 grid=abs(frac(i.localPosition*3.1+float3(.13,.37,.71))-.5);
            float seam=1-smoothstep(.015,.055,min(grid.x,min(grid.y,grid.z)));
            float panelTint=.96+.04*sin(dot(floor(i.localPosition*3.1),float3(2.1,4.3,1.7)));
            o.Albedo=_Color.rgb*(1+_Grain*n*.25)*(1-_Panels*seam*.3)*lerp(1,panelTint,_Panels);
            o.Metallic=_Metallic; o.Smoothness=_Glossiness; o.Emission=_EmissionColor.rgb; o.Alpha=1;
        }
        ENDCG
    }
    Fallback "Diffuse"
}
