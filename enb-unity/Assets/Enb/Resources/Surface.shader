Shader "ENB/Surface"
{
    Properties {
        _Color ("Color", Color) = (0.5,0.5,0.5,1)
        _EmissionColor ("Emission", Color) = (0,0,0,1)
        _Metallic ("Metal", Range(0,1)) = 0
        _Glossiness ("Smoothness", Range(0,1)) = 0.3
        _Grain ("Rock grain", Range(0,1)) = 0
        _Panels ("Hull plating", Range(0,1)) = 0
        _DetailTex ("Surface detail", 2D) = "white" {}
        _TextureStrength ("Texture strength", Range(0,1)) = 0
    }
    SubShader {
        Tags { "RenderType"="Opaque" }
        CGPROGRAM
        #pragma surface surf Standard fullforwardshadows vertex:vert
        #pragma target 3.0
        fixed4 _Color, _EmissionColor;
        sampler2D _DetailTex;
        half _Metallic, _Glossiness, _Grain, _Panels, _TextureStrength;
        struct Input { float3 localPosition; float3 localNormal; };
        void vert(inout appdata_full v, out Input o) {
            UNITY_INITIALIZE_OUTPUT(Input,o); o.localPosition=v.vertex.xyz; o.localNormal=v.normal;
        }
        void surf(Input i, inout SurfaceOutputStandard o) {
            float n=sin(i.localPosition.x*37+sin(i.localPosition.y*29))*sin(i.localPosition.z*31+i.localPosition.y*11);
            float3 grid=abs(frac(i.localPosition*3.1+float3(.13,.37,.71))-.5);
            float seam=1-smoothstep(.015,.055,min(grid.x,min(grid.y,grid.z)));
            float panelTint=.96+.04*sin(dot(floor(i.localPosition*3.1),float3(2.1,4.3,1.7)));
            float3 blend=pow(abs(normalize(i.localNormal)),4);
            blend/=max(.001,blend.x+blend.y+blend.z);
            float3 detail=tex2D(_DetailTex,i.localPosition.yz*.8).rgb*blend.x+
                tex2D(_DetailTex,i.localPosition.xz*.8).rgb*blend.y+
                tex2D(_DetailTex,i.localPosition.xy*.8).rgb*blend.z;
            o.Albedo=_Color.rgb*(1+_Grain*n*.25)*(1-_Panels*seam*.3)*lerp(1,panelTint,_Panels)*
                lerp(1,saturate(detail*3.5),_TextureStrength);
            o.Metallic=_Metallic; o.Smoothness=_Glossiness; o.Emission=_EmissionColor.rgb; o.Alpha=1;
        }
        ENDCG
    }
    Fallback "Diffuse"
}
