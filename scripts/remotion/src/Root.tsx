import "./index.css";
import { Composition } from "remotion";
import React from "react";
import { Walkthrough } from "./Walkthrough";

// Phase 15 / DEMO-03 / CONTEXT D-07.
// Composition id="Walkthrough" -- the CI render command and
// scripts/build-walkthrough.sh both target this id explicitly:
//   npm run render -- Walkthrough out/walkthrough.mp4 ...
//
// Frame budget: 5 scenes, 30fps; Scene 2 is 20s (600 frames) and
// the other four are 15s each (450 frames). Total: 2400 frames =
// 80 seconds. See src/Walkthrough.tsx for the per-scene Sequence
// layout.
export const Root: React.FC = () => {
  return (
    <>
      <Composition
        id="Walkthrough"
        component={Walkthrough}
        durationInFrames={2400}
        fps={30}
        width={1920}
        height={1080}
      />
    </>
  );
};
