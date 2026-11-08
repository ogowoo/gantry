# -*- coding: utf-8 -*-
"""Power Automate 工程生成：definition / 工程目录 / 旧版导入包 / 解决方案包 / 工具。"""
import asyncio
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gantry import power_automate as pa  # noqa: E402
from gantry.tools import ToolRegistry, ToolError  # noqa: E402

EXAMPLES = ROOT / "skills" / "power-automate" / "examples"


def _spec() -> dict:
    return {
        "name": "demo-flow",
        "display_name": "Demo Flow",
        "trigger": {"type": "manual"},
        "actions": [
            {"name": "read items", "connector": "sharepoint", "operation": "get_items",
             "parameters": {"dataset": "https://x.sharepoint.com/sites/s", "table": "t"}},
            {"name": "send mail", "connector": "outlook", "operation": "send_email",
             "parameters": {"emailMessage/To": "a@b.com", "emailMessage/Subject": "s",
                            "emailMessage/Body": "b"}},
        ],
    }


class DefinitionTests(unittest.TestCase):
    def test_manual_trigger_and_actions(self):
        d = pa.build_definition(_spec())
        self.assertEqual(d["triggers"]["manual"]["type"], "Request")
        self.assertEqual(d["triggers"]["manual"]["kind"], "Button")
        self.assertEqual(list(d["actions"]), ["read_items", "send_mail"])
        self.assertEqual(d["actions"]["read_items"]["runAfter"], {})
        self.assertEqual(d["actions"]["send_mail"]["runAfter"], {"read_items": ["Succeeded"]})
        host = d["actions"]["read_items"]["inputs"]["host"]
        self.assertEqual(host["operationId"], "GetItems")
        self.assertTrue(host["apiId"].startswith(pa.APIS_PREFIX))

    def test_recurrence_trigger(self):
        spec = _spec()
        spec["trigger"] = {"type": "recurrence", "frequency": "Day", "interval": 2,
                           "timeZone": "China Standard Time"}
        d = pa.build_definition(spec)
        trig = d["triggers"]["manual"]
        self.assertEqual(trig["type"], "Recurrence")
        self.assertEqual(trig["recurrence"]["interval"], 2)

    def test_control_actions(self):
        spec = {"name": "ctrl", "trigger": {"type": "manual"}, "actions": [
            {"name": "v", "type": "initialize_variable", "var_type": "string", "value": "x"},
            {"name": "wait", "type": "delay", "count": 3, "unit": "Minute"},
            {"name": "note", "type": "compose", "inputs": "hello"},
            {"name": "cond", "type": "condition",
             "actions": [{"name": "inner", "type": "compose", "inputs": "yes"}],
             "else": [{"name": "other", "type": "compose", "inputs": "no"}]},
        ]}
        d = pa.build_definition(spec)
        self.assertEqual(d["actions"]["v"]["type"], "InitializeVariable")
        self.assertEqual(d["actions"]["wait"]["type"], "Wait")
        self.assertEqual(d["actions"]["cond"]["type"], "If")
        self.assertIn("inner", d["actions"]["cond"]["actions"])
        self.assertIn("other", d["actions"]["cond"]["else"]["actions"])

    def test_unknown_connector_and_operation(self):
        with self.assertRaises(pa.PowerAutomateError):
            pa.build_definition({"name": "x", "trigger": {"type": "manual"},
                                 "actions": [{"connector": "nope", "operation": "x"}]})
        with self.assertRaises(pa.PowerAutomateError):
            pa.build_definition({"name": "x", "trigger": {"type": "manual"},
                                 "actions": [{"connector": "teams", "operation": "nope"}]})

    def test_empty_actions_rejected(self):
        with self.assertRaises(pa.PowerAutomateError):
            pa.build_definition({"name": "x", "trigger": {"type": "manual"}, "actions": []})


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="cbp_pa_"))

    def test_write_and_validate(self):
        project = pa.write_flow_project(self.ws, _spec())
        base = Path(project["dir"])
        for rel in ("flow/definition.json", "connections.json", "apis.json", "README.md"):
            self.assertTrue((base / rel).is_file(), rel)
        self.assertIn("shared_sharepointonline", json.loads((base / "connections.json").read_text(encoding="utf-8")))
        self.assertIn("shared_office365", json.loads((base / "apis.json").read_text(encoding="utf-8")))
        report = pa.validate_project(self.ws, "demo-flow")
        self.assertTrue(report["ok"], report)

    def test_examples_are_valid(self):
        for p in sorted(EXAMPLES.glob("*.json")):
            spec = json.loads(p.read_text(encoding="utf-8"))
            project = pa.write_flow_project(self.ws, spec)
            self.assertTrue(pa.validate_project(self.ws, project["name"])["ok"], p.name)


class LegacyPackageTests(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="cbp_pa_legacy_"))

    def test_zip_structure_and_envelope(self):
        pkg = pa.build_legacy_package(self.ws, _spec())
        with zipfile.ZipFile(pkg) as zf:
            names = set(zf.namelist())
            self.assertIn("manifest.json", names)
            self.assertIn("Microsoft.Flow/flows/manifest.json", names)
            self.assertTrue(any(n.endswith("/apisMap.json") for n in names))
            self.assertTrue(any(n.endswith("/connectionsMap.json") for n in names))
            definition_entry = next(n for n in names if n.endswith("/definition.json"))
            envelope = json.loads(zf.read(definition_entry))
            self.assertEqual(envelope["type"], "Microsoft.Flow/flows")
            self.assertIn("definition", envelope["properties"])
            actions = envelope["properties"]["definition"]["actions"]
            host = actions["read_items"]["inputs"]["host"]
            self.assertEqual(host["connectionReferenceName"], "shared_sharepointonline")
            self.assertNotIn("authentication", actions["read_items"]["inputs"])
            for n in names:
                if n.endswith(".json"):
                    json.loads(zf.read(n))  # 必须可解析


class SolutionPackageTests(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="cbp_pa_sol_"))

    def test_zip_structure_and_xml(self):
        pkg = pa.build_solution_package(self.ws, _spec())
        with zipfile.ZipFile(pkg) as zf:
            names = set(zf.namelist())
            self.assertIn("[Content_Types].xml", names)
            self.assertIn("solution.xml", names)
            self.assertIn("customizations.xml", names)
            wf = [n for n in names if n.startswith("Workflows/") and n.endswith(".json")]
            self.assertEqual(len(wf), 1)
            for xml_name in ("solution.xml", "customizations.xml", "[Content_Types].xml"):
                ElementTree.fromstring(zf.read(xml_name))
            envelope = json.loads(zf.read(wf[0]))
            self.assertEqual(envelope["type"], "Microsoft.Flow/flows")
            self.assertIn("definition", envelope["properties"])
            self.assertIn("connectionReferences", envelope["properties"])


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="cbp_pa_tool_"))
        self.reg = ToolRegistry(self.ws, {})

    def test_catalog(self):
        out = asyncio.run(self.reg._tools["power_automate_catalog"]())
        keys = {c["key"] for c in out["connectors"]}
        self.assertIn("teams", keys)
        self.assertIn("outlook", keys)
        self.assertIn("compose", {x["key"] for x in out["control_actions"]})

    def test_generate_and_validate(self):
        out = asyncio.run(self.reg._tools["power_automate_generate"](
            name="demo-flow", spec=_spec(), target="all"))
        self.assertTrue(Path(out["project_dir"]).is_dir())
        self.assertTrue(Path(out["packages"]["legacy"]).is_file())
        self.assertTrue(Path(out["packages"]["solution"]).is_file())
        self.assertTrue(out["validation"]["ok"], out["validation"])
        report = asyncio.run(self.reg._tools["power_automate_validate"](name="demo-flow"))
        self.assertTrue(report["ok"])

    def test_generate_accepts_json_string(self):
        out = asyncio.run(self.reg._tools["power_automate_generate"](
            name="json-flow", spec=json.dumps(_spec()), target="project"))
        self.assertTrue(Path(out["project_dir"]).is_dir())

    def test_generate_rejects_bad_spec(self):
        with self.assertRaises(ToolError):
            asyncio.run(self.reg._tools["power_automate_generate"](
                name="bad", spec={"trigger": {"type": "manual"}, "actions": []}))


if __name__ == "__main__":
    unittest.main()
