// glARsses - virtual screen effect for gamescope (independent implementation).
// Draws the game as a flat or curved screen placed in space. Head pose comes from the
// glARsses driver through gamescope's runtime uniforms.
// Self-contained: no ReShade.fxh needed (so it works without Breezy/XR Gaming installed).
texture XRI_BackBufferTex : COLOR;
sampler XRI_BackBuffer { Texture = XRI_BackBufferTex; };

void XRI_FullscreenVS(in uint id : SV_VertexID, out float4 position : SV_Position, out float2 texcoord : TEXCOORD)
{
    texcoord.x = (id == 2) ? 2.0 : 0.0;
    texcoord.y = (id == 1) ? 2.0 : 0.0;
    position = float4(texcoord * float2(2.0, -2.0) + float2(-1.0, 1.0), 0.0, 1.0);
}

// head->anchor rotation quaternion (w, x, y, z); head frame: x right, y up, z backward
uniform float4 xri_pose < source = "xri_pose"; defaultValue = float4(1.0, 0.0, 0.0, 0.0); >;
// x = tan(half horizontal FOV of the glasses), y = tan(half vertical FOV)
uniform float2 xri_fov < source = "xri_fov"; defaultValue = float2(0.370, 0.208); >;
// x = screen scale (1 = same size as without the effect), y = enabled (0 = pass-through)
uniform float2 xri_screen < source = "xri_screen"; defaultValue = float2(1.0, 0.0); >;
// x = output (glasses) aspect ratio, y = curved (1) or flat (0)
uniform float2 xri_output < source = "xri_output"; defaultValue = float2(1.7778, 0.0); >;
// 1 = draw an outline around the virtual screen (setup / debugging)
uniform float xri_debug < source = "xri_debug"; defaultValue = 0.0; >;
// ambient light around the screen: x = intensity (0 = off), y = size (in screen heights)
uniform float2 xri_ambient < source = "xri_ambient"; defaultValue = float2(0.0, 0.35); >;

// Soft glow outside the screen (like an Ambilight TV). The picture is first shrunk to a tiny
// 32x18 image (each texel averages 64 samples) and blurred again, so the glow only carries
// broad colour regions: no streaks, beams or ghost shapes. Far from the screen it blends into
// the picture's overall colour. On see-through glasses black stays transparent, so the glow is
// the only light added around the screen.
texture XRI_SmallTex { Width = 32; Height = 18; Format = RGBA8; };
sampler XRI_Small { Texture = XRI_SmallTex; AddressU = CLAMP; AddressV = CLAMP; MinFilter = LINEAR; MagFilter = LINEAR; };
texture XRI_GlowTex { Width = 32; Height = 18; Format = RGBA8; };
sampler XRI_Glow { Texture = XRI_GlowTex; AddressU = CLAMP; AddressV = CLAMP; MinFilter = LINEAR; MagFilter = LINEAR; };

float4 PS_GlowDown(float4 pos : SV_Position, float2 uv : TEXCOORD) : SV_Target
{
    float2 texel = float2(1.0 / 32.0, 1.0 / 18.0);
    float3 c = 0.0;
    [unroll] for (int i = 0; i < 8; i++)
        [unroll] for (int j = 0; j < 8; j++)
            c += tex2D(XRI_BackBuffer, uv + ((float2(i, j) + 0.5) / 8.0 - 0.5) * texel).rgb;
    return float4(c / 64.0, 1.0);
}

float4 PS_GlowBlur(float4 pos : SV_Position, float2 uv : TEXCOORD) : SV_Target
{
    // 5x5 binomial blur on the tiny image (about 2 texels wide)
    float2 texel = float2(1.0 / 32.0, 1.0 / 18.0);
    static const float w[5] = { 1.0, 4.0, 6.0, 4.0, 1.0 };
    float3 c = 0.0;
    [unroll] for (int i = 0; i < 5; i++)
        [unroll] for (int j = 0; j < 5; j++)
            c += w[i] * w[j] * tex2D(XRI_Small, uv + float2(i - 2, j - 2) * texel).rgb;
    return float4(c / 256.0, 1.0);
}

float4 Ambient(float2 suv, float screen_aspect, float2 pos)
{
    float2 edge = saturate(suv);
    float t = saturate(length((suv - edge) * float2(screen_aspect, 1.0)) / max(xri_ambient.y, 0.01));
    float fall = 1.0 - t;
    fall = fall * fall * (3.0 - 2.0 * fall);      // smooth start and end, no visible ring
    if (fall <= 0.0)
        return float4(0.0, 0.0, 0.0, 1.0);
    // nearby colour, taken a little inside the picture so the outermost row doesn't dominate
    float3 local = tex2D(XRI_Glow, lerp(edge, 0.5, 0.12)).rgb;
    float3 overall = 0.0;
    [unroll] for (int x = 0; x < 3; x++)
        [unroll] for (int y = 0; y < 3; y++)
            overall += tex2D(XRI_Glow, float2(0.2 + 0.3 * x, 0.2 + 0.3 * y)).rgb;
    overall /= 9.0;
    float3 col = lerp(local, overall, 0.7 * t) * xri_ambient.x * fall;
    // tiny dither so the dark end of the gradient doesn't band
    col += (frac(sin(dot(pos, float2(12.9898, 78.233))) * 43758.5453) - 0.5) / 255.0;
    return float4(max(col, 0.0), 1.0);
}

float3 qrot(float4 q, float3 v)
{
    float3 u = q.yzw;
    return v + 2.0 * cross(u, cross(u, v) + q.x * v);
}

float4 PS_Glarsses(float4 pos : SV_Position, float2 uv : TEXCOORD) : SV_Target
{
    if (xri_screen.y < 0.5)
        return tex2D(XRI_BackBuffer, uv);

    // gamescope letterboxes/pillarboxes this buffer into the glasses' output; work out which
    // part of the field of view this buffer covers so angles stay correct.
    float app_aspect = BUFFER_WIDTH * 1.0 / BUFFER_HEIGHT;
    float2 cover = app_aspect < xri_output.x ? float2(app_aspect / xri_output.x, 1.0)
                                             : float2(1.0, xri_output.x / app_aspect);
    float2 tan_half = xri_fov * cover;

    // ray through this pixel, in head frame
    float3 ray = float3((uv.x * 2.0 - 1.0) * tan_half.x, (1.0 - uv.y * 2.0) * tan_half.y, -1.0);
    float3 dir = qrot(xri_pose, ray);

    float2 half_extent = tan_half * xri_screen.x;
    float2 p;
    if (xri_output.y > 0.5)
    {
        // curved: cylinder of radius 1 around the viewer, same arc width as the flat screen
        float horiz = length(dir.xz);
        p = float2(atan2(dir.x, -dir.z), dir.y / max(horiz, 1e-4));
        half_extent.x = atan(half_extent.x);
    }
    else
    {
        if (dir.z > -1e-4)
            return float4(0.0, 0.0, 0.0, 1.0);
        p = dir.xy * (-1.0 / dir.z);
    }

    float2 suv = float2(0.5 + p.x / (2.0 * half_extent.x), 0.5 - p.y / (2.0 * half_extent.y));
    if (any(suv < 0.0) || any(suv > 1.0))
    {
        if (xri_ambient.x > 0.0)
            return Ambient(suv, half_extent.x / half_extent.y, pos.xy);
        return float4(0.0, 0.0, 0.0, 1.0);
    }
    if (xri_debug > 0.5 && (any(suv < 0.004) || any(suv > 0.996)))
        return float4(1.0, 1.0, 0.0, 1.0);
    return tex2D(XRI_BackBuffer, suv);
}

technique Glarsses
{
    pass GlowDown
    {
        VertexShader = XRI_FullscreenVS;
        PixelShader = PS_GlowDown;
        RenderTarget = XRI_SmallTex;
    }
    pass GlowBlur
    {
        VertexShader = XRI_FullscreenVS;
        PixelShader = PS_GlowBlur;
        RenderTarget = XRI_GlowTex;
    }
    pass
    {
        VertexShader = XRI_FullscreenVS;
        PixelShader = PS_Glarsses;
    }
}
