"""Run:  python tests/test_concurrency.py   (or: python -m unittest discover tests)"""
import os, sys, tempfile, unittest

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A  # noqa: E402


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        A.init_db(reset=True)
        self.c = A.app.test_client()
        self.last = [e for e in self.c.get("/api/events").json if e["capacity"] == 2][0]["id"]

    def sim(self, safe, users=20, same_user=False):
        return self.c.post("/api/simulate", json={
            "event_id": self.last, "users": users, "safe": safe, "same_user": same_user}).json

    def test_mutex_never_overbooks(self):
        for _ in range(5):                       # repeat: races are probabilistic
            self.c.post(f"/api/events/{self.last}/clear-simulated")
            r = self.sim(safe=True)
            self.assertEqual(r["accepted"], 2)
            self.assertEqual(r["event"]["registered"], 2)
            self.assertFalse(r["problem_detected"])

    def test_without_mutex_overbooks(self):
        r = self.sim(safe=False)
        self.assertGreater(r["event"]["registered"], 2, "race condition should be visible")
        self.assertTrue(r["problem_detected"])

    def test_duplicate_prevented_with_mutex(self):
        r = self.sim(safe=True, same_user=True)
        self.assertEqual(r["accepted"], 1)
        self.assertEqual(r["duplicate_emails"], 0)

    def test_cancel_reopens_place(self):
        a = self.c.post(f"/api/events/{self.last}/register", json={"name": "A", "email": "a@x.com"}).json
        self.c.post(f"/api/events/{self.last}/register", json={"name": "B", "email": "b@x.com"})
        full = self.c.post(f"/api/events/{self.last}/register", json={"name": "C", "email": "c@x.com"})
        self.assertEqual(full.status_code, 409)
        self.c.post(f"/api/registrations/{a['registration_id']}/cancel", json={"email": "a@x.com"})
        again = self.c.post(f"/api/events/{self.last}/register", json={"name": "C", "email": "c@x.com"})
        self.assertEqual(again.status_code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
