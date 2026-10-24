Shader "ENB/Planet"
{
    SubShader {
        Tags { "RenderType"="Opaque" }
        Pass {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"
            struct Input { float4 vertex:POSITION; float3 normal:NORMAL; };
            struct Output { float4 vertex:SV_POSITION; float3 normal:TEXCOORD0; float3 local:TEXCOORD1; float3 view:TEXCOORD2; };
            Output vert(Input v) { Output o; o.vertex=UnityObjectToClipPos(v.vertex); o.normal=UnityObjectToWorldNormal(v.normal); o.local=v.vertex.xyz; o.view=WorldSpaceViewDir(v.vertex); return o; }
            fixed4 frag(Output i):SV_Target {
                float3 n=normalize(i.normal);
                float band=sin(i.local.y*90+sin(i.local.x*22)*.3)*.5+.5;
                float3 color=lerp(float3(.3,.22,.17),float3(.7,.56,.35),band);
                float light=saturate(dot(n,normalize(float3(-.6,.3,-.8))));
                float rim=pow(1-saturate(dot(n,normalize(i.view))),4);
                return float4(color*(.025+light)+rim*float3(.16,.22,.3)*light,1);
            }
            ENDCG
        }
    }
}
