import { Img, AbsoluteFill, useCurrentFrame, interpolate } from "remotion";
import React from "react";
import { pickAsset } from "../assetSelector";

// Scene 2 -- Deploy: 5-stage pipeline runs (20s @ 30fps = 600 frames).
// Maps to script.md "## Scene 2: Deploy -- 5-stage pipeline runs (20s)".
export const Scene2Deploy: React.FC = () => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 15], [0, 1], { extrapolateRight: "clamp" });
  const translateY = interpolate(frame, [0, 15], [20, 0], { extrapolateRight: "clamp" });

  return (
    <AbsoluteFill style={{ backgroundColor: "#0e1a2b", color: "#fff", fontFamily: "Inter, system-ui, sans-serif" }}>
      <Img
        src={pickAsset("scene2-deploy.png")}
        style={{ width: "100%", height: "auto", opacity }}
      />
      <div
        style={{
          position: "absolute",
          bottom: 80,
          left: 64,
          right: 64,
          fontSize: 36,
          opacity,
          transform: `translateY(${translateY}px)`,
        }}
      >
        Scene 2 &mdash; Deploy: 5-stage pipeline runs
      </div>
    </AbsoluteFill>
  );
};
