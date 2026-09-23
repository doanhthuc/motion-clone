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
     "migration": {"running": false, "phase": null, "to_dc": null, "started_at": null,
                   "bytes_copied": null, "total_bytes": null},
     "kill_running": false, "last_kill": null,
     "failed_rental": {"gpu": "NVIDIA GeForce RTX 5090", "datacenter": "EU-RO-1",
                       "stock_out": true, "detail": "no stock"}}
    """#

    /// The rental failed on the 5090, then the .env card moved to another GPU:
    /// Retry rental rents `gpu`, not `failed_rental.gpu`.
    static let podFailedOtherGpu = #"""
    {"run_id": "tg-1000", "gpu": "NVIDIA RTX PRO 4500 Blackwell", "lease": null,
     "migration": null, "kill_running": false, "last_kill": null,
     "failed_rental": {"gpu": "NVIDIA GeForce RTX 5090", "datacenter": "EU-RO-1",
                       "stock_out": true, "detail": "no stock"}}
    """#

    static let podMigrating = #"""
    {"run_id": "tg-1000", "gpu": "NVIDIA GeForce RTX 5090", "lease": null,
     "migration": {"running": true, "phase": "copy", "to_dc": "EU-CZ-1", "started_at": 1790000000,
                   "bytes_copied": 1000, "total_bytes": 4000},
     "kill_running": false, "last_kill": null, "failed_rental": null}
    """#

    /// `GET /v1/gpu/stock` (bot.py `_gpu_stock_data`): five GPUs in catalog order.
    static let gpuStock = #"""
    {"selected": "NVIDIA GeForce RTX 5090", "home_datacenter": "EU-RO-1",
     "gpus": [
      {"gpu": "NVIDIA GeForce RTX 5090", "name": "RTX 5090", "usd_per_hr": 0.99,
       "home": {"stock": "Low"}, "sold_out_everywhere": false},
      {"gpu": "NVIDIA GeForce RTX 4090", "name": "RTX 4090", "usd_per_hr": 0.69,
       "home": null, "sold_out_everywhere": false},
      {"gpu": "NVIDIA RTX PRO 4500 Blackwell", "name": "RTX PRO 4500", "usd_per_hr": null,
       "home": null, "sold_out_everywhere": true},
      {"gpu": "NVIDIA L40S", "name": "L40S", "usd_per_hr": 0.86,
       "home": {"stock": "None"}, "sold_out_everywhere": false},
      {"gpu": "NVIDIA RTX PRO 6000 Blackwell Server Edition", "name": "RTX PRO 6000",
       "usd_per_hr": 2.09, "home": {"stock": "High"}, "sold_out_everywhere": false}
     ],
     "other_regions": [
      {"gpu": "NVIDIA GeForce RTX 5090", "name": "RTX 5090", "datacenter": "EU-CZ-1",
       "stock": "High", "usd_per_hr": 0.99},
      {"gpu": "NVIDIA GeForce RTX 4090", "name": "RTX 4090", "datacenter": "EU-CZ-1",
       "stock": "Medium", "usd_per_hr": 0.69},
      {"gpu": "NVIDIA GeForce RTX 4090", "name": "RTX 4090", "datacenter": "US-TX-3",
       "stock": "Low", "usd_per_hr": 0.69}
     ]}
    """#

    static let balance = #"""
    {"runpod": {"usd": 12.34, "usd_per_hr": 0.99, "runway_hours": 12.46, "low_runway": false},
     "errors": []}
    """#

    static let balanceRunpodDown = #"""
    {"runpod": null, "errors": ["couldn't reach runpodctl: timeout"]}
    """#

    static let balanceVast = #"""
    {"runpod": {"usd": 12.34, "usd_per_hr": 0.99, "runway_hours": 12.46, "low_runway": false},
     "vast": {"usd": 7.5}, "errors": []}
    """#

    static let balanceVastDown = #"""
    {"runpod": {"usd": 0.5, "usd_per_hr": 0.99, "runway_hours": 0.51, "low_runway": true},
     "vast": {"usd": null}, "errors": ["couldn't read the Vast credit: exit 1"]}
    """#

    static let migrateAsk = #"""
    {"to_dc": "EU-CZ-1", "home_datacenter": "EU-RO-1", "confirm_token": "tok-abc",
     "expires_in_sec": 600,
     "warning": "This copies your Network Volume to EU-CZ-1: ~2 temporary CPU pods for the duration, then deletes the current volume once the copy is verified byte-for-byte. Cannot be undone once the old volume is deleted."}
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

    static let tryonIdle = #"{"run_id":"tg-1000","run_token":"1790000000123.4","phase_a_running":false,"previews":[]}"#

    static let tryonRunning = #"""
    {"run_id":"tg-1000","run_token":"1790000000123.4","phase_a_running":true,
     "previews":[{"index":"0","run":"model__dress","status":"running","has_image":false},
                 {"index":"1","run":"model__blazer","status":"pending","has_image":false}]}
    """#

    static let tryonDone = #"""
    {"run_id":"tg-1000","run_token":"1790000000123.4","phase_a_running":false,
     "previews":[{"index":"0","run":"model__dress","status":"done","has_image":true},
                 {"index":"1","run":"model__blazer","status":"error","has_image":false}]}
    """#

    static let rentPanel = #"""
    {"runpod":{"gpu":"NVIDIA GeForce RTX 5090","datacenter":"EU-RO-1","stock":"High",
               "usd_per_hr":0.99,"sold_out":false},
     "vast":{"enabled":true,"usd_per_hr":0.62,"session_usd":1.05,"blockers":[],"can_spend":true},
     "run_id":"tg-1000","panel_token":"1790000000123.4.9","after_phase_a":true,
     "jobs":2,"estimate_min":84}
    """#

    static let rentPanelFresh = #"""
    {"runpod":{"gpu":"NVIDIA GeForce RTX 5090","datacenter":"EU-RO-1","stock":"Medium",
               "usd_per_hr":0.99,"sold_out":false},
     "vast":{"enabled":true,"usd_per_hr":0.62,"session_usd":1.05,"blockers":[],"can_spend":true},
     "run_id":"tg-1000","panel_token":"1790000000999.1.10","after_phase_a":false,
     "jobs":2,"estimate_min":84}
    """#

    static let rentPanelSoldOut = #"""
    {"runpod":{"gpu":"NVIDIA GeForce RTX 5090","datacenter":"EU-RO-1","stock":null,
               "usd_per_hr":null,"sold_out":true},
     "vast":{"enabled":false,"usd_per_hr":null,"session_usd":null,
             "blockers":["Vast is disabled — set VAST_ENABLED=1"],"can_spend":false},
     "run_id":"tg-1000","panel_token":"1790000000123.4.9","after_phase_a":false,
     "jobs":1,"estimate_min":42}
    """#

    static let keepRecord = #"""
    {"id":"a1b2c3","owner":"app","material_ids":{"character":"app/model.png","outfit":"app/dress.png"},
     "provider":"gemini","saved_at":1790000300.5}
    """#

    static func data(_ s: String) -> Data { Data(s.utf8) }
}
