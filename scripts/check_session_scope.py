from __future__ import annotations

import fnmatch
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


SCOPES: dict[str, tuple[str, ...]] = {
    "orchestrator": (
        "README.md",
        "协作说明.md",
        ".dockerignore",
        ".gitignore",
        ".gitattributes",
        "Dockerfile",
        "docker-compose.yml",
        "deploy/**",
        "fund_estimator/api/app.py",
        "fund_estimator/web/index.html",
        "fund_estimator/web/shell.js",
        "fund_estimator/web/shell.css",
        "fund_estimator/web/manifest.webmanifest",
        "scripts/check_session_scope.py",
        "tests/test_api.py",
    ),
    "estimate": (
        "fund_estimator/web/estimate.html",
        "fund_estimator/web/app.js",
        "fund_estimator/web/styles.css",
        "fund_estimator/data_sources/**",
        "fund_estimator/models/schema.py",
        "fund_estimator/services/cache.py",
        "fund_estimator/services/confidence.py",
        "fund_estimator/services/estimator.py",
        "fund_estimator/services/exceptions.py",
        "fund_estimator/services/watchlist.py",
        "tests/test_api.py",
        "tests/test_cache.py",
        "tests/test_confidence.py",
        "tests/test_estimator.py",
        "tests/test_parsers.py",
        "tests/test_sina.py",
    ),
    "arbitrage": (
        "fund_estimator/web/arbitrage.html",
        "fund_estimator/web/lof_app.js",
        "fund_estimator/web/lof.css",
        "fund_estimator/web/vendor/qrcode.js",
        "fund_estimator/models/lof.py",
        "fund_estimator/models/etf.py",
        "fund_estimator/services/cache.py",
        "fund_estimator/services/lof.py",
        "fund_estimator/services/lof_config.py",
        "fund_estimator/services/lof_notifications.py",
        "fund_estimator/services/etf.py",
        "fund_estimator/services/etf_config.py",
        "fund_estimator/lof_worker.py",
        "tests/test_api.py",
        "tests/test_cache.py",
        "tests/test_lof.py",
    ),
    "compare": (
        "fund_estimator/web/compare.html",
        "fund_estimator/web/compare_app.js",
        "fund_estimator/web/compare.css",
        "fund_estimator/models/schema.py",
        "fund_estimator/services/comparison.py",
        "tests/test_api.py",
        "tests/test_compare.py",
    ),
}

PROTECTED_BY_ORCHESTRATOR = (
    "README.md",
    "协作说明.md",
    ".dockerignore",
    ".gitignore",
    ".gitattributes",
    "Dockerfile",
    "docker-compose.yml",
    "deploy/**",
    "fund_estimator/api/app.py",
    "fund_estimator/web/index.html",
    "fund_estimator/web/shell.js",
    "fund_estimator/web/shell.css",
    "fund_estimator/web/manifest.webmanifest",
    "scripts/check_session_scope.py",
)


def changed_files() -> list[str]:
    output = subprocess.check_output(["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"])
    files: list[str] = []
    parts = output.split(b"\0")
    index = 0
    while index < len(parts):
        entry = parts[index]
        index += 1
        if not entry:
            continue
        status = entry[:2].decode("ascii", errors="replace")
        path_bytes = entry[3:]
        if status[0] in {"R", "C"} and index < len(parts):
            path_bytes = parts[index]
            index += 1
        path = path_bytes.decode("utf-8", errors="surrogateescape")
        files.append(path.replace("\\", "/").strip('"'))
    return files


def matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in SCOPES:
        names = ", ".join(sorted(SCOPES))
        print(f"用法: python scripts/check_session_scope.py <scope>")
        print(f"scope 可选: {names}")
        return 2

    scope = sys.argv[1]
    files = changed_files()
    if not files:
        print("当前没有未提交改动。")
        return 0

    allowed = SCOPES[scope]
    forbidden = [path for path in files if not matches(path, allowed)]

    if scope != "orchestrator":
        protected = [path for path in files if matches(path, PROTECTED_BY_ORCHESTRATOR)]
        if protected:
            forbidden = sorted(set(forbidden + protected))

    if forbidden:
        print(f"当前 session scope: {scope}")
        print("以下文件不允许由该 session 修改：")
        for path in forbidden:
            print(f"- {path}")
        print("")
        print("处理方式：只保留自己职责范围内的改动；统筹壳、路由、部署、README 交给 orchestrator session。")
        return 1

    print(f"scope 检查通过：{scope}")
    for path in files:
        print(f"- {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
