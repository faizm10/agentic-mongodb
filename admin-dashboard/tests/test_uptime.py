from opsrelay_dashboard.uptime import Target, UptimeMonitor, http_check


class FakeClock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def scripted(results):
    """Check function that returns the next scripted (status, latency, error)."""
    it = iter(results)
    return lambda url, timeout: next(it)


def test_summary_reports_status_since_uptime_and_latency():
    clock = FakeClock()
    checks = [(200, 20.0, None), (200, 40.0, None), (503, 5.0, "HTTP 503"), (200, 30.0, None), (200, 10.0, None)]
    monitor = UptimeMonitor([Target("shopping", "http://x/health")], check=scripted(checks), clock=clock)
    for _ in checks:
        monitor.check_once("shopping")
        clock.now += 60

    s = monitor.summary("shopping")

    assert s["status"] == "up"
    assert s["since"] == 1_000_000.0 + 3 * 60  # first up sample after the outage
    assert s["uptime_1h"] == 80.0 and s["uptime_24h"] == 80.0
    assert s["latency_p50_1h"] == 20.0 and s["latency_p95_1h"] == 40.0
    assert s["last_error"] == "HTTP 503"
    assert [r["up"] for r in s["recent"]] == [True, True, False, True, True]


def test_old_samples_age_out_of_the_hour_and_day_windows():
    clock = FakeClock()
    monitor = UptimeMonitor([Target("shopping", "http://x")], check=scripted([(None, None, "refused"), (200, 9.0, None)]), clock=clock)
    monitor.check_once("shopping")
    clock.now += 2 * 3600
    monitor.check_once("shopping")

    s = monitor.summary("shopping")

    assert s["uptime_1h"] == 100.0
    assert s["uptime_24h"] == 50.0

    clock.now += 25 * 3600
    monitor._record(monitor._samples["shopping"][-1].__class__("shopping", clock.now, True, 5.0, 200))
    assert monitor.summary("shopping")["checks_24h"] == 1


def test_unknown_before_first_check():
    monitor = UptimeMonitor([Target("messaging", "http://x")])

    assert monitor.summary("messaging")["status"] == "unknown"


def test_history_survives_restart_via_jsonl(tmp_path):
    log = tmp_path / "uptime.jsonl"
    clock = FakeClock()
    first = UptimeMonitor([Target("shopping", "http://x")], log_path=log, check=scripted([(500, 3.0, "HTTP 500")]), clock=clock)
    first.check_once("shopping")
    log.write_text(log.read_text() + "not json\n")

    second = UptimeMonitor([Target("shopping", "http://x")], log_path=log, clock=clock)

    assert second.summary("shopping")["status"] == "down"


def test_http_check_against_real_server(health_server):
    up_url, down_url = health_server

    assert http_check(up_url, 2)[0] == 200
    status, _, error = http_check(down_url, 2)
    assert status == 503 and error == "HTTP 503"
    status, latency, error = http_check("http://127.0.0.1:1/health", 1)
    assert status is None and latency is None and error
