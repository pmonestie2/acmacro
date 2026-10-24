Shader "ENB/GateField"
{
    SubShader {
        Tags { "Queue"="Transparent" "RenderType"="Transparent" }
        Blend SrcAlpha One Cull Off ZWrite Off
        Pass {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"
            struct Input { float4 vertex:POSITION; float2 uv:TEXCOORD0; };
            struct Output { float4 vertex:SV_POSITION; float2 uv:TEXCOORD0; };
            Output vert(Input v) { Output o; o.vertex=UnityObjectToClipPos(v.vertex); o.uv=v.uv; return o; }
            fixed4 frag(Output i):SV_Target {
                float r=length(i.uv-.5)*2;
                float ripple=sin(r*60-_Time.y*2)*.08+.12;
                float rim=pow(saturate(r),6);
                return float4(.1,.55,1,(r<1)*(ripple+rim*.5)*.6);
            }
            ENDCG
        }
    }
}
