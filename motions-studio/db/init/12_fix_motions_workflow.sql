-- #region ALD 24/06/2026 - Repair /motions as the standard multi-outfit motion workflow.

-- /motions is the multi-variant workflow: 1 driver video + 3 outfit refs → clean/refine each
-- image via Try-on → Motion Transfer on 0-5s / 5-10s / 10-15s driver slices
-- → smooth crossfade concat.
WITH target_definition AS (
  SELECT $workflow$
{
  "nodes": [
    {
      "id": "motions-driver",
      "type": "input",
      "position": { "x": 80, "y": 80 },
      "data": { "config": { "contentType": "video", "source": "session", "field": "driver_video", "label": "Video motion gốc", "staticData": "", "staticMime": "", "staticName": "", "_gen": "multi-outfit" } }
    },
    {
      "id": "motions-ref-1",
      "type": "input",
      "position": { "x": 80, "y": 280 },
      "data": { "config": { "contentType": "image", "source": "session", "field": "outfit_1", "label": "Outfit 1", "staticData": "", "staticMime": "", "staticName": "", "_gen": "multi-outfit", "_seg": 1 } }
    },
    {
      "id": "motions-tryon-1",
      "type": "tryon",
      "position": { "x": 400, "y": 280 },
      "data": { "config": { "provider": "qwen", "garmentType": "upper", "autoAnalyze": true, "brightness": 0, "outputRes": "", "cleanOnly": true, "productCount": 1, "prompt": "Clean and refine this model/outfit image before motion transfer. Keep the same person, outfit, face, pose, and full-body framing. Remove artifacts, improve lighting, keep a realistic fashion look.", "_gen": "multi-outfit", "_seg": 1 } }
    },
    {
      "id": "motions-motion-1",
      "type": "motion",
      "position": { "x": 720, "y": 280 },
      "data": { "config": { "preset": "5s-720p", "mode": "transfer", "aspectRatio": "9:16", "quality": "480p", "refImageSource": "prev", "motionVideoSource": "prev", "cfg": 6, "shift": 8, "scheduler": "unipc", "audioPassthrough": true, "fps60": false, "loraRelight": 0.3, "clipStrength": 1.2, "faceStrength": 0.6, "faceSource": "driver", "poseStrength": 0.85, "skipFirstFrames": 4, "motionSpeedup": 0, "driverStartSec": 0, "driverDurSec": 5, "_gen": "multi-outfit", "_seg": 1 } }
    },
    {
      "id": "motions-ref-2",
      "type": "input",
      "position": { "x": 80, "y": 480 },
      "data": { "config": { "contentType": "image", "source": "session", "field": "outfit_2", "label": "Outfit 2", "staticData": "", "staticMime": "", "staticName": "", "_gen": "multi-outfit", "_seg": 2 } }
    },
    {
      "id": "motions-tryon-2",
      "type": "tryon",
      "position": { "x": 400, "y": 480 },
      "data": { "config": { "provider": "qwen", "garmentType": "upper", "autoAnalyze": true, "brightness": 0, "outputRes": "", "cleanOnly": true, "productCount": 1, "prompt": "Clean and refine this model/outfit image before motion transfer. Keep the same person, outfit, face, pose, and full-body framing. Remove artifacts, improve lighting, keep a realistic fashion look.", "_gen": "multi-outfit", "_seg": 2 } }
    },
    {
      "id": "motions-motion-2",
      "type": "motion",
      "position": { "x": 720, "y": 480 },
      "data": { "config": { "preset": "5s-720p", "mode": "transfer", "aspectRatio": "9:16", "quality": "480p", "refImageSource": "prev", "motionVideoSource": "prev", "cfg": 6, "shift": 8, "scheduler": "unipc", "audioPassthrough": true, "fps60": false, "loraRelight": 0.3, "clipStrength": 1.2, "faceStrength": 0.6, "faceSource": "driver", "poseStrength": 0.85, "skipFirstFrames": 4, "motionSpeedup": 0, "driverStartSec": 5, "driverDurSec": 5, "_gen": "multi-outfit", "_seg": 2 } }
    },
    {
      "id": "motions-ref-3",
      "type": "input",
      "position": { "x": 80, "y": 680 },
      "data": { "config": { "contentType": "image", "source": "session", "field": "outfit_3", "label": "Outfit 3", "staticData": "", "staticMime": "", "staticName": "", "_gen": "multi-outfit", "_seg": 3 } }
    },
    {
      "id": "motions-tryon-3",
      "type": "tryon",
      "position": { "x": 400, "y": 680 },
      "data": { "config": { "provider": "qwen", "garmentType": "upper", "autoAnalyze": true, "brightness": 0, "outputRes": "", "cleanOnly": true, "productCount": 1, "prompt": "Clean and refine this model/outfit image before motion transfer. Keep the same person, outfit, face, pose, and full-body framing. Remove artifacts, improve lighting, keep a realistic fashion look.", "_gen": "multi-outfit", "_seg": 3 } }
    },
    {
      "id": "motions-motion-3",
      "type": "motion",
      "position": { "x": 720, "y": 680 },
      "data": { "config": { "preset": "5s-720p", "mode": "transfer", "aspectRatio": "9:16", "quality": "480p", "refImageSource": "prev", "motionVideoSource": "prev", "cfg": 6, "shift": 8, "scheduler": "unipc", "audioPassthrough": true, "fps60": false, "loraRelight": 0.3, "clipStrength": 1.2, "faceStrength": 0.6, "faceSource": "driver", "poseStrength": 0.85, "skipFirstFrames": 4, "motionSpeedup": 0, "driverStartSec": 10, "driverDurSec": 5, "_gen": "multi-outfit", "_seg": 3 } }
    },
    {
      "id": "motions-concat",
      "type": "concat",
      "position": { "x": 1040, "y": 480 },
      "data": { "config": { "clipCount": 3, "transition": "fade", "transitionDuration": 0.85, "fps": 16, "_gen": "multi-outfit" } }
    },
    {
      "id": "motions-output",
      "type": "output",
      "position": { "x": 1360, "y": 480 },
      "data": { "config": { "format": "video", "_gen": "multi-outfit" } }
    }
  ],
  "edges": [
    { "id": "motions-e-ref-1", "source": "motions-ref-1", "target": "motions-tryon-1", "targetHandle": "model" },
    { "id": "motions-e-clean-1", "source": "motions-tryon-1", "target": "motions-motion-1", "targetHandle": "image" },
    { "id": "motions-e-driver-1", "source": "motions-driver", "target": "motions-motion-1", "targetHandle": "motion" },
    { "id": "motions-e-ref-2", "source": "motions-ref-2", "target": "motions-tryon-2", "targetHandle": "model" },
    { "id": "motions-e-clean-2", "source": "motions-tryon-2", "target": "motions-motion-2", "targetHandle": "image" },
    { "id": "motions-e-driver-2", "source": "motions-driver", "target": "motions-motion-2", "targetHandle": "motion" },
    { "id": "motions-e-ref-3", "source": "motions-ref-3", "target": "motions-tryon-3", "targetHandle": "model" },
    { "id": "motions-e-clean-3", "source": "motions-tryon-3", "target": "motions-motion-3", "targetHandle": "image" },
    { "id": "motions-e-driver-3", "source": "motions-driver", "target": "motions-motion-3", "targetHandle": "motion" },
    { "id": "motions-e-concat-1", "source": "motions-motion-1", "target": "motions-concat", "targetHandle": "clip1" },
    { "id": "motions-e-concat-2", "source": "motions-motion-2", "target": "motions-concat", "targetHandle": "clip2" },
    { "id": "motions-e-concat-3", "source": "motions-motion-3", "target": "motions-concat", "targetHandle": "clip3" },
    { "id": "motions-e-output", "source": "motions-concat", "target": "motions-output" }
  ]
}
$workflow$::jsonb AS definition
)
UPDATE workflows target
SET name = COALESCE(NULLIF(target.name, ''), 'motions'),
    description = 'Multi-outfit Motion: 3 ảnh outfit được clean qua Try-on, render Motion từng đoạn 5s rồi ghép mượt thành video 15s.',
    definition = target_definition.definition,
    is_active = true,
    updated_at = now()
FROM target_definition
WHERE target.slug = 'motions'
  AND (
    target.definition IS DISTINCT FROM target_definition.definition
    OR target.description IS DISTINCT FROM 'Multi-outfit Motion: 3 ảnh outfit được clean qua Try-on, render Motion từng đoạn 5s rồi ghép mượt thành video 15s.'
    OR target.is_active IS DISTINCT FROM true
  );

-- #endregion
