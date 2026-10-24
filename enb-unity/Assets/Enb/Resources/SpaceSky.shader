Shader "ENB/SpaceSky"
{
    SubShader {
        Tags { "Queue"="Background" "RenderType"="Background" "PreviewType"="Skybox" }
        Cull Off ZWrite Off
        Pass {
            CGPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "UnityCG.cginc"
            struct Input { float4 vertex:POSITION; };
            struct Output { float4 vertex:SV_POSITION; float3 direction:TEXCOORD0; };
            Output vert(Input v) { Output o; o.vertex=UnityObjectToClipPos(v.vertex); o.direction=v.vertex.xyz; return o; }
            float hash(float3 p) { return frac(sin(dot(p,float3(127.1,311.7,74.7)))*43758.5453); }
            float noise(float3 p) {
                float3 a=floor(p),f=frac(p); f=f*f*(3-2*f);
                return lerp(lerp(lerp(hash(a),hash(a+float3(1,0,0)),f.x),lerp(hash(a+float3(0,1,0)),hash(a+float3(1,1,0)),f.x),f.y),lerp(lerp(hash(a+float3(0,0,1)),hash(a+float3(1,0,1)),f.x),lerp(hash(a+float3(0,1,1)),hash(a+float3(1,1,1)),f.x),f.y),f.z);
            }
            fixed4 frag(Output i):SV_Target {
                float3 d=normalize(i.direction);
                float band=exp(-abs(dot(d,normalize(float3(.25,1,.45))))*5);
                float cloud=noise(d*4)*.55+noise(d*11)*.3+noise(d*27)*.15;
                float3 col=float3(.003,.007,.018)+band*cloud*cloud*float3(.045,.085,.16);
                col+=pow(noise(d*7+13),4)*band*float3(.09,.025,.11);
                float2 uv=float2(atan2(d.z,d.x)/6.2831853+.5,acos(clamp(d.y,-1,1))/3.1415926);
                float2 grid=uv*float2(1600,800),cell=floor(grid),local=frac(grid)-.5;
                float h=hash(float3(cell,1));
                float star=step(.994,h)*(1-smoothstep(.03,.21,length(local)));
                col+=star*lerp(float3(.4,.65,1),float3(1,.86,.68),hash(float3(cell,2)))*1.4;
                return float4(col,1);
            }
            ENDCG
        }
    }
}
