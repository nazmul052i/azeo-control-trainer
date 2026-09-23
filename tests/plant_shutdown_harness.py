"""Real APVC plant/controller, with only wall-clock scheduling made deterministic."""
from contextlib import contextmanager
import logging
import time

from test_apvc_startup_commissioning import (
    PROJECT, _project_document, _stop_exchange_thread, load_strategy,
    StrategyRuntime, RuntimeContext, compile_strategy, DataBridge,
    SharedDataStore, LocalVirtualIoDriver,
)
from test_virtual_controller_qualification import _deterministic_config
from azeo_control_trainer.core.hmi.binding.source import LiveGraphSource


@contextmanager
def plant_controller():
    prior = logging.root.manager.disable
    logging.disable(logging.WARNING)
    store = SharedDataStore()
    driver = LocalVirtualIoDriver(store, _deterministic_config(), PROJECT)
    runtimes, graphs, periods = [], {}, []
    try:
        assert driver.start(), driver.last_error
        _stop_exchange_thread(driver)
        document = _project_document()
        area = document["areas"][0]
        for relative in [*area["strategies"], *area.get("sfc_modules", [])]:
            graph, _ = load_strategy(PROJECT / relative)
            runtime = StrategyRuntime()
            runtime.load(compile_strategy(graph), DataBridge(store))
            runtime.set_context(RuntimeContext(store=store, plugin_id=document["project_id"]))
            assert runtime.go_online(), relative
            graphs[graph.name] = graph
            runtimes.append(runtime)
            periods.append(max(1, round(graph.scan_ms / 100)))
        tick = 0

        def advance(count=1):
            nonlocal tick
            for _ in range(count):
                driver.scan_once()
                for period, runtime in zip(periods, runtimes):
                    if tick % period == 0:
                        runtime.execute_scan(period * .1)
                driver.scan_once()
                driver._session.product.step(1)
                tick += 1

        advance(10)
        yield driver._session.product, graphs, runtimes, LiveGraphSource(lambda: graphs), advance
    finally:
        for runtime in runtimes:
            runtime.go_offline()
        driver.stop()
        logging.disable(prior)


def run_guidance(definition, run, source, product, advance, *, on_prompt, on_event=lambda event: None):
    from azeo_control_trainer.core.pa_designer.connectors.tag_value import TagValue
    seen = []

    def observe():
        samples = {}
        for tag, path in definition.bindings.items():
            result = source.read(path)
            assert result.quality.name in {"GOOD", "UNCERTAIN"}, (tag, path, result)
            samples[tag] = TagValue(tag, result.value, result.quality.name.title())
        run.observe(product.engine.stats.sim_time, samples)

    def advance_observing(count=1):
        # A real station continues publishing observations while a procedure
        # is paused.  Interleave them here as well: a larger controller can
        # legitimately make a long deterministic advance exceed the runtime's
        # ten-second station-heartbeat limit if the harness goes silent.
        for _ in range(count):
            advance()
            observe()

    observe()
    run.start(definition, actor="qualification operator")
    deadline = time.monotonic() + 600
    while run.active and time.monotonic() < deadline:
        advance()
        observe()
        for event in run.drain():
            on_event(event)
            if event["kind"] == "prompt":
                seen.append(event["step"]["id"])
                on_prompt(event, advance_observing)
                run.answer(event["prompt_id"], "Monitored hot inventory handover." if event["prompt_kind"] == "comment" else True)
        # The real runner is asynchronous; allow it to observe each scan.
        time.sleep(.003)
    assert not run.active, "Native plant qualification exceeded its wall-clock budget"
    return seen
