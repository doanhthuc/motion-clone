"""RunPod Community: the rent body, its filters, the stock quote, the network probe, and the drain
wiring that treats a Community pod as a stateless, checked-after-rent host. No pod, no spend."""
import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runpod_community as rc
from batchlib.manifest import Manifest, Run
from batchlib_ext import net_probe
from batchlib_ext.watchdog import DESTROYABLE_NAMES
import drain

ROOT = Path(__file__).resolve().parents[2]


def _get(values: dict):
    return lambda k: values.get(k, "")


def _motion_manifest() -> Manifest:
    return Manifest(path=Path("/tmp/m.yaml"), runs=[Run(id="a", pipeline="motion-enhance",
                              inputs={"character": "/tmp/c.png", "driver": "/tmp/d.mp4"})])


class TestBody(unittest.TestCase):
    def _body(self, **env):
        return rc.build_body(gpu="NVIDIA GeForce RTX 5090", image="img:1", disk_gb=100,
                             filters=rc.Filters.from_env(_get(env)))

    def test_is_a_community_gpu_pod_the_watchdog_may_destroy(self):
        b = self._body()
        self.assertEqual((b["cloudType"], b["computeType"], b["gpuCount"]), ("COMMUNITY", "GPU", 1))
        self.assertIn(b["name"], DESTROYABLE_NAMES)
        self.assertNotIn("networkVolumeId", b)

    def test_needs_a_public_ssh_port_for_rsync(self):
        b = self._body()
        self.assertTrue(b["supportPublicIp"])
        self.assertEqual(b["ports"], ["22/tcp"])

    def test_defaults_are_the_vast_floors(self):
        b = self._body()
        self.assertEqual((b["minDownloadMbps"], b["minDiskBandwidthMBps"]), (1000.0, 3000.0))
        self.assertEqual((b["minVCPUPerGPU"], b["minRAMPerGPU"]), (8, 32))
        self.assertNotIn("countryCodes", b)

    def test_env_overrides_every_filter(self):
        b = self._body(RUNPOD_MIN_DOWNLOAD_MBPS="500", RUNPOD_MIN_DISK_MBPS="1500",
                       RUNPOD_MIN_VCPU="16", RUNPOD_MIN_RAM_GB="64", RUNPOD_COUNTRIES="ro, cz")
        self.assertEqual((b["minDownloadMbps"], b["minDiskBandwidthMBps"]), (500.0, 1500.0))
        self.assertEqual((b["minVCPUPerGPU"], b["minRAMPerGPU"]), (16, 64))
        self.assertEqual(b["countryCodes"], ["RO", "CZ"])

    def test_disk_floor_falls_back_to_the_vast_knob(self):
        self.assertEqual(self._body(MIN_DISK_BW="2500")["minDiskBandwidthMBps"], 2500.0)

    def test_cuda_floor_becomes_an_exact_list(self):
        self.assertEqual(self._body(MIN_CUDA_VERSION="13.2")["allowedCudaVersions"][0], "13.2")
        self.assertNotIn("12.9", self._body(MIN_CUDA_VERSION="13.0")["allowedCudaVersions"])

    def test_cuda_compares_numerically(self):
        self.assertEqual(rc.cuda_versions_from("12.10")[0], "13.0")

    def test_cuda_above_everything_known_is_refused(self):
        with self.assertRaises(ValueError):
            rc.cuda_versions_from("99.0")

    def test_cli_body_reads_the_environment(self):
        env = {**os.environ, "GPU": "G", "IMAGE": "I", "DISK": "77",
               "RUNPOD_MIN_VCPU": "12", "MIN_CUDA_VERSION": "13.0"}
        out = subprocess.run([sys.executable, str(ROOT / "scripts/runpod_community.py"), "body"],
                             env=env, capture_output=True, text=True, check=True).stdout
        b = json.loads(out)
        self.assertEqual((b["gpuTypeIds"], b["imageName"], b["containerDiskInGb"]), (["G"], "I", 77))
        self.assertEqual(b["minVCPUPerGPU"], 12)


class TestResponseParsing(unittest.TestCase):
    def test_pod_id_and_cost(self):
        raw = json.dumps({"id": "abc123", "costPerHr": 0.69})
        self.assertEqual(rc._field(raw, "id"), "abc123")
        self.assertEqual(rc._field(raw, "costPerHr"), "0.69")

    def test_an_error_response_has_no_id(self):
        for raw in ('[{"error": "no instances available"}]', "not json", "{}"):
            self.assertEqual(rc._field(raw, "id"), "", raw)


class TestStock(unittest.TestCase):
    def test_query_carries_the_rent_filters(self):
        q = rc.stock_query("NVIDIA GeForce RTX 5090", rc.Filters(countries=("RO",)), 100)
        for part in ("secureCloud: false", "supportPublicIp: true", "minDownload: 1000",
                     "minVcpuCount: 8", "minMemoryInGb: 32", "minDisk: 100",
                     'countryCode: "RO"', '"NVIDIA GeForce RTX 5090"'):
            self.assertIn(part, q)

    def test_in_stock(self):
        s = rc.parse_stock({"data": {"gpuTypes": [{"communityPrice": 0.69, "lowestPrice": {
            "stockStatus": "Low", "uninterruptablePrice": 0.71}}]}})
        self.assertEqual((s.status, s.usd_per_hr, s.sold_out), ("Low", 0.71, False))

    def test_null_stock_is_sold_out_but_keeps_the_list_price(self):
        # What RunPod answered for the 5090 on 2026-10-03.
        s = rc.parse_stock({"data": {"gpuTypes": [{"communityPrice": 0.69, "lowestPrice": {
            "stockStatus": None, "uninterruptablePrice": None}}]}})
        self.assertTrue(s.sold_out)
        self.assertEqual(s.usd_per_hr, 0.69)

    def test_unknown_gpu(self):
        self.assertTrue(rc.parse_stock({"data": {"gpuTypes": []}}).sold_out)

    def test_cloud_and_community(self):
        self.assertEqual(rc.cloud(_get({})), "SECURE")
        self.assertTrue(rc.is_community(_get({"GPU_PROVIDER": "runpod", "RUNPOD_CLOUD": "community"})))
        self.assertFalse(rc.is_community(_get({"GPU_PROVIDER": "vast", "RUNPOD_CLOUD": "COMMUNITY"})))


class TestNetProbe(unittest.TestCase):
    def test_fast_host_is_ok(self):
        # 4 streams x 750 MB in 15 s = 200 MB/s, a Vast-like host.
        v = net_probe.judge("bytes 750000000\n" * 4)
        self.assertEqual(v.state, "ok")
        self.assertAlmostEqual(v.mbps, 200.0)

    def test_slow_host_is_slow(self):
        self.assertEqual(net_probe.judge("bytes 100000000\n" * 4).state, "slow")

    def test_nothing_downloaded_is_unknown_not_slow(self):
        # Fails open: an HF outage is not the host's fault and must not destroy a paid pod.
        for out in ("", "bytes 0\n" * 4, "curl: (6) Could not resolve host\n"):
            self.assertEqual(net_probe.judge(out).state, "unknown", out)

    def test_floor_is_configurable(self):
        self.assertEqual(net_probe.judge("bytes 750000000\n" * 4, floor=250).state, "slow")

    def test_streams_read_different_ranges(self):
        script = net_probe.probe_script("https://x/y")
        self.assertEqual(script.count("curl "), net_probe.STREAMS)
        self.assertIn(f"-r {net_probe.RANGE_STEP}-", script)

    def test_ssh_failure_is_unknown(self):
        with mock.patch("batchlib_ext.net_probe.subprocess.run", side_effect=OSError("no ssh")):
            self.assertEqual(net_probe.run_probe("h", "22").state, "unknown")


class TestDrainCommunity(unittest.TestCase):
    def _env(self, **extra):
        return mock.patch.dict(os.environ, {"GPU_PROVIDER": "runpod", "RUNPOD_CLOUD": "COMMUNITY",
                                            **extra})

    def test_community_is_stateless_secure_is_not(self):
        with self._env():
            self.assertTrue(drain.runpod_community())
            self.assertTrue(drain.stateless_box())
        with self._env(RUNPOD_CLOUD="SECURE"):
            self.assertFalse(drain.stateless_box())

    def test_wait_and_bootstrap_preloads_models_after_the_network_check(self):
        calls = []
        os.environ.pop("VAST_MODEL_IDS", None)
        with self._env(), \
             mock.patch.object(drain.subprocess, "run", side_effect=lambda *a, **k: calls.append(("wait", k["env"]["TIMEOUT"]))), \
             mock.patch.object(drain, "check_network_speed", side_effect=lambda: calls.append(("net",))), \
             mock.patch.object(drain, "sh", side_effect=lambda *a: calls.append(a)):
            drain.wait_and_bootstrap(_motion_manifest())
            self.assertIn("wan-animate-14b", os.environ.get("VAST_MODEL_IDS", ""))
        os.environ.pop("VAST_MODEL_IDS", None)
        self.assertEqual(calls, [("wait", str(drain.COMMUNITY_WAIT_TIMEOUT_MIN)), ("net",),
                                 ("bash", "scripts/pod-bootstrap.sh")])

    def test_no_ssh_in_time_is_a_bad_host(self):
        err = subprocess.CalledProcessError(1, ["bash"])
        with self._env(), mock.patch.object(drain.subprocess, "run", side_effect=err), \
             mock.patch.object(drain, "sh") as sh:
            with self.assertRaises(drain.BadHost):
                drain.wait_and_bootstrap(_motion_manifest())
        sh.assert_not_called()

    def test_slow_network_is_a_bad_host(self):
        slow = net_probe.Verdict("slow", 20.0, "20 MB/s")
        with self._env(), mock.patch.object(drain, "env_get", return_value=""), \
             mock.patch.object(drain.net_probe, "run_probe", return_value=slow):
            with self.assertRaises(drain.BadHost):
                drain.check_network_speed()

    def test_throttled_community_gpu_raises_without_touching_the_vast_board(self):
        from batchlib_ext.gpu_probe import judge
        with self._env(), mock.patch.object(drain, "env_get", return_value="x"), \
             mock.patch.object(drain, "run_probe", return_value=judge("210, 3090, 55\n" * 3)), \
             mock.patch.object(drain, "blacklist_machine") as bl:
            with self.assertRaises(drain.SlowGpu):
                drain.check_gpu_speed("pod1")
        bl.assert_not_called()

    def test_a_community_stock_out_is_a_plain_failure(self):
        # The stock-out screen offers Secure-only remedies (switch at the volume's datacenter,
        # migrate the volume), none of which apply to a Community pod.
        from batchlib_ext.provision_failure import provision_failure_path, read_provision_failure
        path = Path(tempfile.mkdtemp()) / "tg-1.yaml"
        with self._env(), mock.patch.object(drain.subprocess, "run") as run, \
             mock.patch.object(drain, "env_get", return_value=""):
            run.return_value = mock.Mock(returncode=1, stderr=f"x {drain._STOCK_OUT_MARKER} y")
            with self.assertRaises(subprocess.CalledProcessError):
                drain.provision(ceiling_min=60, manifest_path=path, manifest=_motion_manifest())
        failure = read_provision_failure(provision_failure_path(path))
        self.assertFalse(failure.stock_out)
        self.assertEqual(failure.datacenter, "Community")

    def test_main_replaces_a_pod_that_failed_before_bootstrap(self):
        d = Path(tempfile.mkdtemp())
        f = d / "m.yaml"
        f.write_text("runs:\n  - id: a\n    pipeline: motion-enhance\n"
                     "    inputs: {character: /tmp/c.png, driver: /tmp/d.mp4}\n")
        n = {"provision": 0, "teardown": 0, "chain": 0}
        waits = iter([drain.BadHost("slow download"), None])

        def wait(_m):
            exc = next(waits)
            if exc:
                raise exc

        def bump(k):
            return lambda *a, **kw: n.__setitem__(k, n[k] + 1) or (f"pod{n[k]}" if k == "provision" else None)

        with mock.patch.object(sys, "argv", ["drain.py", "--file", str(f), "--yes"]), \
             mock.patch.object(drain, "provision", side_effect=bump("provision")), \
             mock.patch.object(drain, "write_lease"), \
             mock.patch.object(drain, "wait_and_bootstrap", side_effect=wait), \
             mock.patch.object(drain, "check_gpu_speed"), \
             mock.patch.object(drain, "batch_run",
                               side_effect=lambda *a: drain.EXIT_NEEDS_POD if "--no-start" in a else 0), \
             mock.patch.object(drain, "teardown", side_effect=bump("teardown")), \
             mock.patch.object(drain, "chain_or_teardown", side_effect=bump("chain")):
            rc_ = drain.main()
        self.assertEqual((rc_, n["provision"], n["teardown"], n["chain"]), (0, 2, 1, 1))


class TestQuote(unittest.TestCase):
    def test_runpod_quote_follows_the_cloud(self):
        from control import runs
        with mock.patch.object(runs.runpod_community, "cloud", return_value="COMMUNITY"):
            self.assertEqual(runs.quoted_usd_per_hr("runpod"), rc.DEFAULT_USD_PER_HR)
        with mock.patch.object(runs.runpod_community, "cloud", return_value="SECURE"):
            self.assertEqual(runs.quoted_usd_per_hr("runpod"), runs.RUNPOD_FLAT_USD_PER_HR)
        self.assertIsNone(runs.quoted_usd_per_hr("vast"))


if __name__ == "__main__":
    unittest.main()
