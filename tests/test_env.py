import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from arcus_mm.env import load_env

class EnvFile(unittest.TestCase):
    def test_template_blanks_and_existing_environment(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{"ARCUS_ADDRESS":"existing"},clear=True):
            p=Path(d)/".env"
            p.write_text('ARCUS_ADDRESS=file-address\nARCUS_API_PRIVATE_KEY=\nARCUS_ACCOUNT_INDEX="2"\nIGNORED_NAME=value\n',encoding="utf-8-sig")
            load_env(p)
            self.assertEqual(os.environ["ARCUS_ADDRESS"],"existing")
            self.assertEqual(os.environ["ARCUS_ACCOUNT_INDEX"],"2")
            self.assertNotIn("ARCUS_API_PRIVATE_KEY",os.environ)
            self.assertNotIn("IGNORED_NAME",os.environ)
    def test_parse_error_does_not_echo_secret(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{},clear=True):
            p=Path(d)/".env";p.write_text('ARCUS_API_PRIVATE_KEY="private-value',encoding="utf-8")
            with self.assertRaises(ValueError) as ctx: load_env(p)
            self.assertNotIn("private-value",str(ctx.exception))
