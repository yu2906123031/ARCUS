import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from arcus_mm.api import PublicAPI
from arcus_mm.config import Config
from arcus_mm.engine import Engine
from arcus_mm.diagnostics import exception_locations

class Diagnostics(unittest.IsolatedAsyncioTestCase):
    def test_locations_never_include_values_or_exception_text(self):
        secret="DO_NOT_LOG_SECRET"
        try:raise TypeError(secret)
        except TypeError as exc:locations=exception_locations(exc)
        encoded=json.dumps(locations)
        self.assertNotIn(secret,encoded);self.assertTrue(locations)
        self.assertEqual(set(locations[-1]),{"file","function","line"})

    async def test_cleanup_error_does_not_mask_original_failure(self):
        events=[];log=lambda event,**kw:events.append(dict(event=event,**kw))
        api=PublicAPI(Config(),log);e=Engine(api.c,api,log)
        e.setup=AsyncMock();e.venue=SimpleNamespace()
        async def idle():await asyncio.Event().wait()
        api.stream=idle;e.watchdog=idle
        e.cycle=AsyncMock(side_effect=TypeError("private original detail"))
        e.cancel_all=AsyncMock(side_effect=ValueError("private cleanup detail"))
        with self.assertRaisesRegex(TypeError,"private original detail"):await e.run()
        self.assertEqual([r["event"] for r in events],["execution_error","cleanup_error"])
        self.assertEqual(events[1]["primary_error_type"],"TypeError")
        self.assertNotIn("private",json.dumps(events))

    async def test_cleanup_failure_without_original_error_is_propagated(self):
        api=PublicAPI(Config(),lambda *a,**k:None);e=Engine(api.c,api,api.log)
        e.setup=AsyncMock();e.venue=SimpleNamespace()
        async def idle():await asyncio.Event().wait()
        api.stream=idle;e.watchdog=idle
        async def stop():e.trip("operator stop");e.stop.set()
        e.cycle=AsyncMock(side_effect=stop)
        e.cancel_all=AsyncMock(side_effect=ValueError("cleanup"))
        with self.assertRaisesRegex(ValueError,"cleanup"):await e.run()

    def test_logger_metadata_cannot_be_overridden_or_crash_on_collision(self):
        import tempfile,io
        from contextlib import redirect_stdout
        from arcus_mm.models import Log
        with tempfile.TemporaryDirectory() as directory:
            log=Log(directory,"live")
            with redirect_stdout(io.StringIO()):log("example",mode="payload",time_ns=0)
            row=json.loads(log.path.read_text(encoding="utf-8"))
        self.assertEqual(row["mode"],"live");self.assertGreater(row["time_ns"],0)
        self.assertEqual(row["event"],"example")
