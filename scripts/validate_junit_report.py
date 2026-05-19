"""校验 JUnit 报告：存在 failure/error/skip 时以非零退出码结束（供 Jenkins 使用）。"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET


def _collect_junit_stats(root: ET.Element) -> tuple[int, int, int, int]:
    """
    汇总 JUnit 统计。pytest --junitxml 根节点为 <testsuites>，计数在子节点 <testsuite> 上。
    """
    suites: list[ET.Element]
    if root.tag == "testsuite":
        suites = [root]
    else:
        suites = root.findall("testsuite")
        if not suites:
            suites = root.findall(".//testsuite")

    tests = failures = errors = skipped = 0
    for suite in suites:
        tests += int(suite.attrib.get("tests", 0))
        failures += int(suite.attrib.get("failures", 0))
        errors += int(suite.attrib.get("errors", 0))
        skipped += int(suite.attrib.get("skipped", 0))

    # 兼容非 pytest 的单层报告（属性在根节点）
    if not suites:
        tests = int(root.attrib.get("tests", 0))
        failures = int(root.attrib.get("failures", 0))
        errors = int(root.attrib.get("errors", 0))
        skipped = int(root.attrib.get("skipped", 0))

    return tests, failures, errors, skipped


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: validate_junit_report.py <report.xml> <label>", file=sys.stderr)
        return 2

    report_path, label = sys.argv[1], sys.argv[2]
    root = ET.parse(report_path).getroot()
    tests, failures, errors, skipped = _collect_junit_stats(root)

    if tests == 0:
        print(f"[{label}] ERROR: 未执行任何测试用例（请检查 JUnit XML 格式）")
        return 1

    if skipped > 0:
        print(f"[{label}] ERROR: 存在 {skipped} 个 SKIPPED（共 {tests} 个）")
        return 1

    if failures > 0 or errors > 0:
        print(f"[{label}] ERROR: failures={failures}, errors={errors}")
        return 1

    passed = tests - skipped - failures - errors
    print(f"[{label}] OK: {passed} passed / {tests} total, 0 skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
