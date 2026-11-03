import unittest
from types import SimpleNamespace

from app.ui import _pdf_boundary_transition, _pdf_wheel_direction


class PdfWheelDirectionTests(unittest.TestCase):
    def test_windows_and_macos_mousewheel_direction(self):
        self.assertEqual(_pdf_wheel_direction(SimpleNamespace(delta=120, num=None)), -1)
        self.assertEqual(_pdf_wheel_direction(SimpleNamespace(delta=-120, num=None)), 1)
        self.assertEqual(_pdf_wheel_direction(SimpleNamespace(delta=1, num=None)), -1)
        self.assertEqual(_pdf_wheel_direction(SimpleNamespace(delta=-1, num=None)), 1)

    def test_linux_wheel_buttons(self):
        self.assertEqual(_pdf_wheel_direction(SimpleNamespace(num=4, delta=0)), -1)
        self.assertEqual(_pdf_wheel_direction(SimpleNamespace(num=5, delta=0)), 1)

    def test_boundary_transitions(self):
        self.assertEqual(_pdf_boundary_transition(1, (0.0, 1.0)), 1)
        self.assertEqual(_pdf_boundary_transition(-1, (0.0, 1.0)), -1)
        self.assertEqual(_pdf_boundary_transition(1, (0.0, 0.8)), 0)
        self.assertEqual(_pdf_boundary_transition(-1, (0.2, 1.0)), 0)
        self.assertEqual(_pdf_boundary_transition(1, (0.0, 0.9995)), 1)


if __name__ == "__main__":
    unittest.main()
