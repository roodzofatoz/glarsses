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
        return float4(0.0, 0.0, 0.0, 1.0);
    if (xri_debug > 0.5 && (any(suv < 0.004) || any(suv > 0.996)))
        return float4(1.0, 1.0, 0.0, 1.0);
    return tex2D(XRI_BackBuffer, suv);
}

technique Glarsses
{
    pass
    {
        VertexShader = XRI_FullscreenVS;
        PixelShader = PS_Glarsses;
    }
}
