# MediaPipe Tasks Vision assets

Populated by `npm run mocap:fetch` — runs once, checks in the fetched
files so production deploys are reproducible without CDN access.

Contents after the fetch:

| File                              | Purpose                               |
| --------------------------------- | ------------------------------------- |
| `vision_wasm_internal.{js,wasm}`  | SIMD build of the Tasks Vision runtime |
| `vision_wasm_nosimd_internal.*`   | Fallback for browsers without SIMD    |
| `face_landmarker.task`            | 468-point face mesh + 52 blendshapes  |
| `pose_landmarker_full.task`       | 33-keypoint full-body pose            |

Do not edit these files by hand — re-run the fetch script instead so
the pinned versions stay in lock-step with
`@mediapipe/tasks-vision` in `package.json`.
