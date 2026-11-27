import unittest

from agent.calculator import RATIOS
from agent.schemas import (calc_schema, calculator_tool, direct_schema, extraction_schema, items_schema,
                           validate)


def walk_objects(schema):
    """Yield every object schema, including inside anyOf branches and properties."""
    if not isinstance(schema, dict):
        return
    if schema.get("type") == "object":
        yield schema
    for option in schema.get("anyOf", []):
        yield from walk_objects(option)
    for sub in schema.get("properties", {}).values():
        yield from walk_objects(sub)
    if "items" in schema and isinstance(schema["items"], dict) and "type" in schema["items"]:
        yield from walk_objects(schema["items"])


class SchemaTests(unittest.TestCase):
    def test_items_keys_follow_calculator_registry(self):
        for ratio, (_, inputs) in RATIOS.items():
            schema = items_schema(ratio)
            self.assertEqual(schema["required"], list(inputs))
            self.assertEqual(list(schema["properties"]), list(inputs))

    def test_every_object_is_closed_and_fully_required(self):
        for ratio in RATIOS:
            for schema in (extraction_schema(ratio), direct_schema(ratio), calc_schema(),
                           calculator_tool(ratio)["input_schema"]):
                for obj in walk_objects(schema):
                    self.assertIs(obj.get("additionalProperties"), False)
                    self.assertEqual(sorted(obj["required"]), sorted(obj["properties"]))

    def test_tool_is_strict(self):
        self.assertTrue(calculator_tool("roe")["strict"])

    def test_validator_accepts_and_rejects(self):
        schema = extraction_schema("current_ratio")
        item = {"value_reported": 1.0, "scale": "millions", "period_end": "2025-12-31",
                "source_statement": "balance_sheet", "source_label": "x", "status": "extracted"}
        good = {"items": {"current_assets": dict(item), "current_liabilities": dict(item)}}
        self.assertEqual(validate(good, schema), [])
        missing = {"items": {"current_assets": dict(item)}}
        self.assertTrue(validate(missing, schema))
        extra = {"items": {**good["items"], "inventory": dict(item)}}
        self.assertTrue(validate(extra, schema))
        bad_scale = {"items": {"current_assets": {**item, "scale": "percent"}, "current_liabilities": item}}
        self.assertTrue(validate(bad_scale, schema))
        nullable = {"items": {"current_assets": {**item, "value_reported": None, "scale": None,
                                                 "status": "not_found"}, "current_liabilities": item}}
        self.assertEqual(validate(nullable, schema), [])
        self.assertTrue(validate({"items": {**good["items"]}, "x": 1}, schema))
        self.assertTrue(validate(True, {"type": "number"}))  # bool is not a number


if __name__ == "__main__":
    unittest.main()
