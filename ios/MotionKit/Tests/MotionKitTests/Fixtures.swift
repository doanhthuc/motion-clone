import Foundation

enum Fixtures {
    static let runs = #"""
    {"runs": [
      {"id": "tg-1000", "batch": "2026-09-21-0900", "status": "running", "updated_at": 1790000000.5,
       "jobs_total": 3, "jobs_done": 1},
      {"id": "old-run", "batch": null, "status": "phase_a", "updated_at": 1789000000,
       "jobs_total": 0, "jobs_done": 0},
      {"id": "weird", "batch": "b", "status": "something_new", "updated_at": 1, "jobs_total": 1, "jobs_done": 0}
    ]}
    """#

    static let runDetail = #"""
    {"id": "tg-1000", "batch": "2026-09-21-0900", "status": "running", "updated_at": 1790000000.5,
     "jobs_total": 2, "jobs_done": 1,
     "jobs": [
       {"id": "model-side__ao-dai", "status": "done",
        "stages": [{"name": "tryon", "status": "done", "elapsed_sec": 6.4},
                   {"name": "motion", "status": "done", "elapsed_sec": 2040},
                   {"name": "enhance", "status": "done", "elapsed_sec": 480}]},
       {"id": "model-side__blazer", "status": "running",
        "stages": [{"name": "tryon", "status": "done", "elapsed_sec": 7.1},
                   {"name": "motion", "status": "running", "elapsed_sec": null},
                   {"name": "enhance", "status": "None", "elapsed_sec": null}]}
     ],
     "lease": {"provider": "runpod", "provisioned_at": 1789999000, "abs_max_min": 180,
               "quoted_usd_per_hr": 0.99},
     "outputs": ["model-side__ao-dai.mp4"]}
    """#

    static let runDetailVast = #"""
    {"id": "tg-1000", "batch": null, "status": "stopped", "updated_at": 1, "jobs_total": 0, "jobs_done": 0,
     "jobs": [], "lease": {"provider": "vast", "provisioned_at": 5, "abs_max_min": null,
     "quoted_usd_per_hr": null}, "outputs": []}
    """#

    static let podLive = #"""
    {"run_id": "tg-1000", "gpu": "NVIDIA GeForce RTX 5090",
     "lease": {"provider": "runpod", "provisioned_at": 1789999000, "abs_max_min": 180,
               "quoted_usd_per_hr": 0.99, "run_id": "tg-1000"},
     "migration": null, "kill_running": false,
     "last_kill": {"at": 1789990000, "ok": false, "code": "destroy_unverified",
                   "message": "could not verify the pod is gone"},
     "failed_rental": null}
    """#

    static let podIdle = #"""
    {"run_id": "tg-1000", "gpu": "NVIDIA GeForce RTX 5090", "lease": null,
     "migration": {"state": "whatever"}, "kill_running": false, "last_kill": null,
     "failed_rental": {"gpu": "NVIDIA GeForce RTX 5090", "datacenter": "EU-RO-1",
                       "stock_out": true, "detail": "no stock"}}
    """#

    static let outputs = #"""
    {"outputs": [
      {"batch": "2026-09-21-0900", "updated_at": 1790000000,
       "files": [{"name": "model-side__ao-dai.mp4", "bytes": 18000000},
                 {"name": "model-side__ao-dai.png", "bytes": 900000}]}
    ]}
    """#

    static let materials = #"""
    {"materials": [
      {"id": "app/áo dài.png", "owner": "app", "name": "áo dài.png", "bytes": 901,
       "updated_at": 1790000100.5, "kind": "image"},
      {"id": "42/driver.mp4", "owner": "42", "name": "driver.mp4", "bytes": 33554449,
       "updated_at": 1790000000, "kind": "future_kind"}
    ]}
    """#

    static let uploadOpen =
        #"{"upload_id":"abc123","chunk_size":33554432,"chunks_total":2}"#

    static let uploadStatus =
        #"{"upload_id":"abc123","file_name":"driver.mp4","size":33554449,"chunk_size":33554432,"chunks_total":2,"received":[0]}"#

    static let uploadComplete = #"""
    {"material":{"id":"app/driver.mp4","owner":"app","name":"driver.mp4","bytes":33554449,
     "updated_at":1790000200,"kind":"video"},
     "probe":{"kind":"video","width":1080,"height":1920,"duration_s":12.5,
     "bitrate_kbps":4200,"size_bytes":33554449,"warning":"Video is larger than recommended."}}
    """#

    static let pipelines = #"""
    {"pipelines":[
      {"id":"motion-enhance","stages":["motion","enhance"],
       "required":["character","driver"],"optional":[],
       "roles":{"character":"image","driver":"video"},"providers":[]},
      {"id":"tryon-motion-enhance","stages":["tryon","motion","enhance"],
       "required":["character","driver","outfit"],"optional":["mask"],
       "roles":{"character":"image","driver":"video","outfit":"image","mask":"future_kind"},
       "providers":[{"id":"gemini","label":"Gemini"},{"id":"qwen-max","label":"Qwen Max"}]}
    ]}
    """#

    static let draft = #"""
    {"owner":"app","pipeline":"tryon-motion-enhance","provider":"gemini","generation":4,
     "slots":{"character":{"material_id":"app/model.png","name":"model.png","exists":true,
       "probe":{"kind":"image","width":1024,"height":1536,"duration_s":null,
       "bitrate_kbps":null,"size_bytes":900,"warning":""},"warning":""}},
     "required":["character","driver","outfit"],"optional":["mask"],
     "missing":["driver","outfit"],"validated":null,
     "batch":[{"digest":"abc123def0","run_id":"model__dress","pipeline":"tryon-motion-enhance",
       "provider":"gemini","slots":{"character":"app/model.png","outfit":"app/dress.png","driver":null}}],
     "jobs":1,"estimate_min":null}
    """#

    static let validatedDraft = #"""
    {"valid":true,"stale":false,"draft":{"owner":"app","pipeline":"motion-enhance",
     "provider":"gemini","generation":8,
     "slots":{"character":{"material_id":"app/model.png","name":"model.png","exists":true,
       "probe":{"kind":"image","width":1024,"height":1536,"duration_s":null,
       "bitrate_kbps":null,"size_bytes":900,"warning":""},"warning":""},
       "driver":{"material_id":"app/driver.mp4","name":"driver.mp4","exists":true,
       "probe":{"kind":"video","width":1080,"height":1920,"duration_s":12.5,
       "bitrate_kbps":4200,"size_bytes":33554449,"warning":""},"warning":""}},
     "required":["character","driver"],"optional":[],"missing":[],
     "validated":true,"batch":[],"jobs":1,"estimate_min":48}}
    """#

    static let errorConflict = #"{"error": {"code": "stale_panel", "message": "the panel changed"}}"#

    static let cloudflareHTML = "<!DOCTYPE html><html><head><title>Access denied | Error 1010</title></head></html>"

    static func data(_ s: String) -> Data { Data(s.utf8) }
}
