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
// intro animation, timed by gamescope's own clock so it is smooth on every frame:
// x = duration in ms (0 = no animation), y = armed (1 while the screen has just appeared)
uniform float2 xri_intro < source = "xri_intro"; defaultValue = float2(0.0, 0.0); >;
// cubic-bezier(x1, y1, x2, y2) timing curve for the intro, as in CSS animations
uniform float4 xri_bezier < source = "xri_bezier"; defaultValue = float4(0.34, 1.56, 0.64, 1.0); >;
// milliseconds since gamescope loaded this effect (the driver loads it fresh each time
// the glARsses screen appears, so 0 = the moment it appeared)
uniform float xri_timer < source = "timer"; >;

// y of a cubic Bezier timing curve at progress x (Newton iterations solve x(t) = x)
float CubicBezier(float x, float4 c)
{
    float t = x;
    [unroll] for (int i = 0; i < 6; i++)
    {
        float u = 1.0 - t;
        float bx = 3.0 * u * u * t * c.x + 3.0 * u * t * t * c.z + t * t * t;
        float dx = 3.0 * u * u * c.x + 6.0 * u * t * (c.z - c.x) + 3.0 * t * t * (1.0 - c.z);
        t = saturate(t - (bx - x) / max(dx, 1e-4));
    }
    float u = 1.0 - t;
    return 3.0 * u * u * t * c.y + 3.0 * u * t * t * c.w + t * t * t;
}

// glow (intensity, size) for this frame: full glow when the screen appears, then along the
// Bezier curve to the user's setting
float2 AmbientNow()
{
    if (xri_intro.x <= 0.0 || xri_intro.y < 0.5)
        return xri_ambient;
    float e = CubicBezier(saturate(xri_timer / xri_intro.x), xri_bezier);
    float2 v = lerp(float2(1.0, 1.0), xri_ambient, e);
    return float2(saturate(v.x), max(v.y, 0.05));
}

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

float4 Ambient(float2 suv, float screen_aspect, float2 pos, float2 amb)
{
    float2 edge = saturate(suv);
    float t = saturate(length((suv - edge) * float2(screen_aspect, 1.0)) / max(amb.y, 0.01));
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
    float3 col = lerp(local, overall, 0.7 * t) * amb.x * fall;
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
        float2 amb = AmbientNow();
        if (amb.x > 0.0)
            return Ambient(suv, half_extent.x / half_extent.y, pos.xy, amb);
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
