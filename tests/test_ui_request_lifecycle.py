import queue
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.ui import ReaderUI


class FakePopup:
    def __init__(self, exists=True, raises=False, has_update=True):
        self.exists = exists
        self.raises = raises
        self.updated = []
        self.destroyed = False
        if not has_update:
            self.update_meaning = None

    def winfo_exists(self):
        if self.raises:
            raise tk.TclError("widget has already been destroyed")
        return self.exists

    def update_meaning(self, text, is_error=False):
        self.updated.append((text, is_error))

    def destroy(self):
        self.destroyed = True
        self.exists = False


class FakeRoot:
    def __init__(self):
        self.scheduled = []

    def after(self, delay, callback):
        self.scheduled.append((delay, callback))
        return len(self.scheduled)


class FakeControl:
    def __init__(self):
        self.options = []

    def config(self, **kwargs):
        self.options.append(kwargs)


class ImmediateThread:
    def __init__(self, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


class PopupRequestLifecycleTests(unittest.TestCase):
    def setUp(self):
        # Exercise asynchronous lifecycle guards without starting a Tk display.
        self.ui = ReaderUI.__new__(ReaderUI)
        self.ui._selection_request = 7
        self.ui._meaning_results = queue.Queue()
        self.ui.root = FakeRoot()
        self.popup = FakePopup()
        self.ui.active_popup = self.popup

    def test_current_popup_and_request_are_accepted(self):
        self.assertTrue(self.ui._is_current_popup(self.popup, 7))

    def test_old_selection_request_is_rejected(self):
        self.assertFalse(self.ui._is_current_popup(self.popup, 6))

    def test_replaced_popup_is_rejected(self):
        self.ui.active_popup = FakePopup()
        self.assertFalse(self.ui._is_current_popup(self.popup, 7))

    def test_destroyed_popup_is_rejected(self):
        self.popup.exists = False
        self.assertFalse(self.ui._is_current_popup(self.popup, 7))

    def test_tcl_widget_error_is_rejected(self):
        self.popup.raises = True
        self.assertFalse(self.ui._is_current_popup(self.popup, 7))

    def test_explanation_result_updates_only_the_popup_that_started_request(self):
        explanation = SimpleNamespace(text="Useful explanation", is_error=False, context=None)
        self.ui._meaning_results.put((7, self.popup, explanation, "1.0", None))
        self.ui._process_meaning_results()
        self.assertEqual(self.popup.updated, [("Useful explanation", False)])
        self.assertEqual(len(self.ui.root.scheduled), 1)

    def test_result_from_replaced_popup_is_discarded(self):
        replacement = FakePopup()
        self.ui.active_popup = replacement
        explanation = SimpleNamespace(text="Stale explanation", is_error=False, context=None)
        self.ui._meaning_results.put((7, self.popup, explanation, "1.0", None))
        self.ui._process_meaning_results()
        self.assertEqual(self.popup.updated, [])
        self.assertEqual(replacement.updated, [])

    def test_result_from_closed_popup_is_discarded(self):
        self.popup.exists = False
        explanation = SimpleNamespace(text="Late explanation", is_error=False, context=None)
        self.ui._meaning_results.put((7, self.popup, explanation, "1.0", None))
        self.ui._process_meaning_results()
        self.assertEqual(self.popup.updated, [])

    def test_closing_already_destroyed_popup_is_safe(self):
        self.popup.raises = True
        self.ui._close_active_popup()
        self.assertIsNone(self.ui.active_popup)

    def test_followup_result_updates_the_popup_that_started_it(self):
        self.ui.controller = SimpleNamespace(
            answer=lambda _question, _context: SimpleNamespace(
                text="Grounded answer", is_error=False, sources=("R1",)
            )
        )
        self.ui.status = SimpleNamespace(set=lambda _value: None)
        entry = FakeControl()
        ask_button = FakeControl()
        responses = []

        with patch("app.ui.threading.Thread", ImmediateThread):
            self.ui._ask_followup(
                "What does this mean?", None,
                lambda text, sources: responses.append((text, sources)),
                entry, ask_button, self.popup,
            )
        self.ui.root.scheduled.pop()[1]()

        self.assertEqual(responses, [("Grounded answer", ("R1",))])
        self.assertEqual(entry.options, [{"state": "normal"}])
        self.assertEqual(ask_button.options, [{"state": "normal"}])

    def test_followup_result_is_discarded_after_popup_replacement(self):
        self.ui.controller = SimpleNamespace(
            answer=lambda _question, _context: SimpleNamespace(
                text="Late answer", is_error=False, sources=()
            )
        )
        self.ui.status = SimpleNamespace(set=lambda _value: None)
        entry = FakeControl()
        ask_button = FakeControl()
        responses = []

        with patch("app.ui.threading.Thread", ImmediateThread):
            self.ui._ask_followup(
                "What does this mean?", None,
                lambda text, sources: responses.append((text, sources)),
                entry, ask_button, self.popup,
            )
        self.ui.active_popup = FakePopup()
        self.ui.root.scheduled.pop()[1]()

        self.assertEqual(responses, [])
        self.assertEqual(entry.options, [])
        self.assertEqual(ask_button.options, [])


if __name__ == "__main__":
    unittest.main()
