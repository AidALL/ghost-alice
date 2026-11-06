"""Decision contracts for Core completion selection."""

from copy import deepcopy
import unittest

from completion_route import select_completion_route


def context():
    units = [dict(work_unit_id=u, contract_revision=1, kind=k, admitted=True,
                  owner=dict(platform="claude", session_id="session"))
             for u, k in (("fix", "execution"), ("explain", "nonexecution"), ("backlog", "execution"))]
    criteria = [dict(work_unit_id="fix", contract_revision=1, criterion_id="AC1", id="AC1",
                     summary="Repair the original failure", source="user-said", admitted=True)]
    surface = dict(response_mode="normal", boundary_contract="n/a", reason="approved")
    route = dict(schema_version="response-route.v1", status="ready", platform="claude", session_id="session",
                 input_event_id="input-new", current_input_event_id="input-new", boundary_ref=None,
                 routing_surface=surface, work_units=units,
                 selected_work_unit_ids=["fix", "explain"], selected_criteria=criteria)
    return dict(platform="claude", session_id="session", input_event_id="input-new", response_route=route)


class CompletionRouteTests(unittest.TestCase):
    def test_minimal_surface_binds_the_current_route(self):
        ctx = context()
        ctx["response_route"]["routing_surface"] = dict(response_mode="normal", boundary_contract="n/a")
        self.assertEqual(select_completion_route(ctx)["route_binding"], "current")

    def test_removed_or_malformed_surface_fields_cannot_authorize_completion(self):
        for field, value in (("verification_complexity", "level-2"), ("focus_layer", "micro"), ("reason", " ")):
            with self.subTest(field=field):
                ctx = context(); ctx["response_route"]["routing_surface"][field] = value
                self.assertEqual(select_completion_route(ctx)["route_binding"], "invalid")

    def test_new_input_waits_for_route(self):
        for missing in (False, True):
            ctx = context()
            ctx['input_event_id'] = 'next-input'
            if missing:
                ctx['response_route'] = None
            else:
                ctx['response_route'].update(status='stale', current_input_event_id='next-input')
            with self.subTest(missing=missing):
                self.assertEqual(select_completion_route(ctx)['route_binding'], 'missing' if missing else 'stale')
        ctx = context()
        ctx['response_route']['session_id'] = 'foreign-session'
        self.assertEqual(select_completion_route(ctx)['route_binding'], 'stale')

    def test_missing_or_stale_binding_is_not_current(self):
        for binding in (None, "stale", "wrong-session", "wrong-input"):
            ctx = context()
            if binding is None: ctx["response_route"] = None
            elif binding == "stale": ctx["response_route"]["status"] = "stale"
            elif binding == "wrong-session": ctx["response_route"]["session_id"] = "other"
            else: ctx["response_route"]["input_event_id"] = "old-input"
            with self.subTest(binding=binding):
                self.assertNotEqual(select_completion_route(ctx)["route_binding"], "current")

    def test_pure_selector_does_not_mutate_any_input(self):
        ctx = context()
        before = deepcopy(ctx)
        select_completion_route(ctx)
        self.assertEqual(ctx, before)


if __name__ == "__main__":
    unittest.main()
