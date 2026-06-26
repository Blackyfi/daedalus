"""Argus deterministic tamper / fake-green detection (IMPROVEMENTS #8)."""
from __future__ import annotations

from daedalus.argus.verifier import detect_tampering


def test_no_tampering_on_honest_fix():
    diff = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,2 @@
 def add(a, b):
-    return a - b
+    return a + b
"""
    assert detect_tampering(diff) == []


def test_deleted_test_file_is_blocker():
    diff = """diff --git a/tests/test_calc.py b/tests/test_calc.py
deleted file mode 100644
index 1234567..0000000
--- a/tests/test_calc.py
+++ /dev/null
@@ -1,3 +0,0 @@
-def test_add():
-    assert add(1, 2) == 3
"""
    out = detect_tampering(diff)
    assert len(out) == 1
    assert out[0]["severity"] == "blocker"
    assert "deleted" in out[0]["description"].lower()


def test_added_skip_marker_is_blocker():
    diff = """diff --git a/tests/test_calc.py b/tests/test_calc.py
--- a/tests/test_calc.py
+++ b/tests/test_calc.py
@@ -1,3 +1,4 @@
 def test_add():
+    import pytest; pytest.skip("flaky")
     assert add(1, 2) == 3
"""
    out = detect_tampering(diff)
    assert any("skip" in f["description"].lower() for f in out)


def test_removed_assertions_with_no_replacement_is_blocker():
    diff = """diff --git a/tests/test_calc.py b/tests/test_calc.py
--- a/tests/test_calc.py
+++ b/tests/test_calc.py
@@ -1,4 +1,2 @@
 def test_add():
-    assert add(1, 2) == 3
-    assert add(0, 0) == 0
     pass
"""
    out = detect_tampering(diff)
    assert len(out) == 1
    assert "assertion" in out[0]["description"].lower()


def test_refactored_assertions_not_flagged():
    # Assertions removed AND re-added (a rename/refactor) is not tampering.
    diff = """diff --git a/tests/test_calc.py b/tests/test_calc.py
--- a/tests/test_calc.py
+++ b/tests/test_calc.py
@@ -1,3 +1,3 @@
 def test_add():
-    assert add(1, 2) == 3
+    assert add(1, 2) == 3  # clearer
"""
    assert detect_tampering(diff) == []


def test_deleting_non_test_file_is_not_tampering():
    diff = """diff --git a/old_module.py b/old_module.py
deleted file mode 100644
--- a/old_module.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def dead():
-    assert False
"""
    assert detect_tampering(diff) == []


def test_jest_spec_skip_flagged():
    diff = """diff --git a/src/calc.spec.ts b/src/calc.spec.ts
--- a/src/calc.spec.ts
+++ b/src/calc.spec.ts
@@ -1,3 +1,3 @@
-  it("adds", () => expect(add(1,2)).toBe(3));
+  it.skip("adds", () => expect(add(1,2)).toBe(3));
"""
    out = detect_tampering(diff)
    assert any(f["severity"] == "blocker" for f in out)
