import { Sequence, AbsoluteFill } from "remotion";
import React from "react";
import { Scene1WorkItem } from "./scenes/Scene1WorkItem";
import { Scene2Deploy } from "./scenes/Scene2Deploy";
import { Scene3TestGates } from "./scenes/Scene3TestGates";
import { Scene4AuditRecord } from "./scenes/Scene4AuditRecord";
import { Scene5Rollback } from "./scenes/Scene5Rollback";

// Phase 15 / DEMO-03 / CONTEXT D-07 -- 5-scene composition.
//
// Frame budget per scene at 30fps:
//   Scene 1 (WorkItem)    = 450 (15s)
//   Scene 2 (Deploy)      = 600 (20s)
//   Scene 3 (TestGates)   = 450 (15s)
//   Scene 4 (AuditRecord) = 450 (15s)
//   Scene 5 (Rollback)    = 450 (15s)
//                  Total  = 2400 frames (80 seconds)
//
// Root.tsx registers the composition with durationInFrames=2400
// matching this layout. If you change a scene's duration here,
// update Root.tsx accordingly or Remotion will trim/pad the render.
const SCENE_DURATIONS = [450, 600, 450, 450, 450] as const;

const SCENES: ReadonlyArray<React.FC> = [
  Scene1WorkItem,
  Scene2Deploy,
  Scene3TestGates,
  Scene4AuditRecord,
  Scene5Rollback,
];

export const Walkthrough: React.FC = () => {
  let cursor = 0;
  const segments: Array<{ from: number; durationInFrames: number; Comp: React.FC; key: string }> = [];
  for (let idx = 0; idx < SCENES.length; idx++) {
    segments.push({
      from: cursor,
      durationInFrames: SCENE_DURATIONS[idx],
      Comp: SCENES[idx],
      key: `scene-${idx + 1}`,
    });
    cursor += SCENE_DURATIONS[idx];
  }

  return (
    <AbsoluteFill style={{ backgroundColor: "#0e1a2b" }}>
      {segments.map(({ from, durationInFrames, Comp, key }) => (
        <Sequence key={key} from={from} durationInFrames={durationInFrames}>
          <Comp />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
