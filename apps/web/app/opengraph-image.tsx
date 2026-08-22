import { ImageResponse } from "next/og";

export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function OGImage() {
  return new ImageResponse(
    (
      <div
        style={{
          background: "linear-gradient(135deg, #0f172a 0%, #1e293b 100%)",
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "flex-start",
          justifyContent: "flex-end",
          padding: "80px",
          fontFamily: "sans-serif",
        }}
      >
        <div
          style={{
            color: "#64748b",
            fontSize: 20,
            marginBottom: 20,
            letterSpacing: "0.3px",
          }}
        >
          Building in public · github.com/gaurav-gandhi-2411
        </div>
        <div
          style={{
            color: "white",
            fontSize: 54,
            fontWeight: 700,
            lineHeight: 1.1,
            letterSpacing: "-1.5px",
            maxWidth: 900,
          }}
        >
          DealHunter
        </div>
        <div
          style={{
            color: "#94a3b8",
            fontSize: 26,
            marginTop: 28,
            lineHeight: 1.4,
          }}
        >
          The reasoning layer for travel platforms.
        </div>
      </div>
    ),
    { ...size },
  );
}
