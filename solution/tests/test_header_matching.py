from __future__ import annotations

import pytest
from solution.schema import normalize_header, WorkflowSchema


class TestHeaderMatching:
    def test_normalize_header(self):
        assert normalize_header("Mobile No") == "mobile no"
        assert normalize_header("  Insured_Name  ") == "insured name"
        assert normalize_header("policy__number") == "policy number"
        assert normalize_header("Order_ID") == "order id"
        assert normalize_header("preferred_language") == "preferred language"

    def test_workflow_schema_aliases_mapping(self):
        schema_dict = {
            "workflow_id": "test_wf",
            "fields": [
                {
                    "name": "customer_name",
                    "type": "string",
                    "required": True,
                    "aliases": ["name", "customer name", "insured name"]
                },
                {
                    "name": "phone",
                    "type": "phone",
                    "required": True,
                    "aliases": ["mobile", "mobile no", "contact"]
                }
            ]
        }
        wf = WorkflowSchema.from_dict(schema_dict)
        assert wf.header_to_field["name"] == "customer_name"
        assert wf.header_to_field["customer name"] == "customer_name"
        assert wf.header_to_field["insured name"] == "customer_name"
        assert wf.header_to_field["mobile no"] == "phone"
        assert wf.header_to_field["contact"] == "phone"
        assert "unknown_col" not in wf.header_to_field
