"""Typed agent records: decisions Cassie answers and reports she reads on the X4."""
import tempfile
import unittest
from pathlib import Path

from x4_store import Store


def decision(**over):
    body = {"id": "pyrrha.menu-order", "kind": "decision", "agent": "pyrrha",
            "title": "Ship the menu before House?",
            "summary": "Agents first keeps one interaction grammar before mutations.",
            "sections": [{"heading": "Why", "body": "Menus define the grammar."},
                         {"heading": "Risk", "body": "House waits a little longer."}],
            "recommendation": "approve"}
    body.update(over)
    return body


def report(**over):
    body = {"id": "vesper.weekly", "kind": "report", "agent": "vesper",
            "title": "Weekly lab report", "summary": "Three runs finished.",
            "sections": [{"heading": "Findings", "body": "All three converged."}]}
    body.update(over)
    return body


class Records(unittest.TestCase):
    def setUp(self):
        import x4_records
        self.mod = x4_records
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(str(Path(self.tmp.name) / "db"))
        self.records = x4_records.Records(self.store)

    def put(self, body, now=100):
        return self.records.put(self.mod.validate_record(body), now=now)

    def test_decision_defaults_to_approve_reject_defer_and_opens(self):
        r = self.put(decision())
        self.assertEqual(r["revision"], 1)
        self.assertEqual(r["status"], "open")
        self.assertEqual(r["record"]["options"], ["approve", "reject", "defer"])

    def test_identical_put_keeps_revision_changed_body_bumps_and_reopens(self):
        self.put(decision())
        self.assertEqual(self.put(decision(), now=110)["revision"], 1)
        self.records.answer("pyrrha.menu-order", 1, "approve", now=120, source="x4-01")
        changed = self.put(decision(summary="New evidence arrived."), now=130)
        self.assertEqual(changed["revision"], 2)
        self.assertEqual(changed["status"], "open")
        self.assertIsNone(changed["answer"])

    def test_answer_applies_only_to_displayed_revision(self):
        self.put(decision())
        self.put(decision(summary="Revised after Cassie looked."), now=110)
        result = self.records.answer("pyrrha.menu-order", 1, "approve", now=120, source="x4-01")
        self.assertEqual(result["outcome"], "stale")
        self.assertEqual(result["current_revision"], 2)
        self.assertEqual(self.records.get("pyrrha.menu-order")["status"], "open")

    def test_answer_records_choice_and_does_not_overwrite(self):
        self.put(decision())
        first = self.records.answer("pyrrha.menu-order", 1, "defer", now=120, source="x4-01")
        self.assertEqual(first["outcome"], "answered")
        again = self.records.answer("pyrrha.menu-order", 1, "approve", now=130, source="x4-01")
        self.assertEqual(again["outcome"], "already_answered")
        got = self.records.get("pyrrha.menu-order")
        self.assertEqual((got["status"], got["answer"], got["answered_revision"]), ("answered", "defer", 1))

    def test_unknown_option_and_report_answers_are_refused(self):
        self.put(decision())
        self.put(report())
        self.assertEqual(self.records.answer("pyrrha.menu-order", 1, "maybe", now=1, source="x")["outcome"], "invalid")
        self.assertEqual(self.records.answer("vesper.weekly", 1, "approve", now=1, source="x")["outcome"], "invalid")
        self.assertEqual(self.records.answer("missing", 1, "approve", now=1, source="x")["outcome"], "missing")

    def test_report_ack_marks_read_for_that_revision(self):
        self.put(report())
        self.assertEqual(self.records.ack("vesper.weekly", 1, now=5)["outcome"], "read")
        self.assertEqual(self.records.get("vesper.weekly")["status"], "read")
        self.put(report(summary="A fourth run finished."), now=6)
        self.assertEqual(self.records.get("vesper.weekly")["status"], "unread")

    def test_agent_summary_counts_open_decisions_and_unread_reports(self):
        self.put(decision())
        self.put(decision(id="pyrrha.second", title="Second decision"))
        self.put(report())
        self.put(report(id="pyrrha.memo", agent="pyrrha"))
        summary = {row["agent"]: row for row in self.records.agents(now=100)}
        self.assertEqual((summary["pyrrha"]["decisions"], summary["pyrrha"]["reports"]), (2, 1))
        self.assertEqual((summary["vesper"]["decisions"], summary["vesper"]["reports"]), (0, 1))
        self.assertEqual([row["agent"] for row in self.records.agents(now=100)], ["pyrrha", "vesper"])

    def test_list_orders_open_decisions_before_reports_and_skips_expired(self):
        self.put(report(id="pyrrha.memo", agent="pyrrha"), now=1)
        self.put(decision(), now=2)
        self.put(decision(id="pyrrha.old", expires_at="2000-01-01T00:00:00Z"), now=3)
        ids = [r["id"] for r in self.records.list(agent="pyrrha", now=1_790_000_000)]
        self.assertEqual(ids, ["pyrrha.menu-order", "pyrrha.memo"])

    def test_remove(self):
        self.put(report())
        self.assertTrue(self.records.remove("vesper.weekly"))
        self.assertIsNone(self.records.get("vesper.weekly"))


class Validation(unittest.TestCase):
    def setUp(self):
        import x4_records
        self.v = x4_records.validate_record

    def test_rejects_bad_shapes(self):
        cases = [decision(kind="card"), decision(agent="Not Valid"), decision(title=""),
                 decision(title="x" * 81), decision(summary="y" * 601),
                 decision(sections=[{"heading": "h", "body": "b"}] * 13),
                 decision(sections=[{"heading": "h" * 41, "body": "b"}]),
                 decision(sections=[{"heading": "h", "body": "b" * 1201}]),
                 decision(options=["approve"]), decision(options=["a", "b", "c", "d", "e"]),
                 decision(recommendation="maybe"), report(options=["approve", "reject"]),
                 decision(extra=True), decision(expires_at="tomorrow"),
                 decision(notify="everyone")]
        for body in cases:
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.v(body)

    def test_custom_options_and_text_repair(self):
        out = self.v(decision(options=["merge", "hold"], recommendation="hold",
                              summary="Line one\\nLine two"))
        self.assertEqual(out["options"], ["merge", "hold"])
        self.assertEqual(out["summary"], "Line one\nLine two")
        self.assertEqual(out["notify"], "none")


if __name__ == "__main__":
    unittest.main()
